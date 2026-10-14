"""Maintenance tools: lock, commit extraction, release record, verification, publish rollback,
install-check host boundaries and issue registration. Uses temporary repositories only."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'maintenance'))
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
import maint_common, maint_lock, install_check
import release_package as rp
from register_plugin_issue import register


@pytest.fixture
def session(monkeypatch):
    monkeypatch.delenv('CODEX_THREAD_ID',raising=False);monkeypatch.delenv('CAD_QTO_LOCK_TOKEN',raising=False)
    def use(name):monkeypatch.setenv('CLAUDE_CODE_SESSION_ID',name)
    use('session-a');return use


def lock_cmd(maint,*args):
    return maint_lock.main(['--maint-dir',str(maint),*args])


def test_lock_is_exclusive_and_only_the_owner_releases(tmp_path,session):
    assert lock_cmd(tmp_path,'acquire','--model','m','--scope','s')==0
    session('session-b')
    assert lock_cmd(tmp_path,'acquire','--model','m','--scope','s')==maint_lock.EXIT_HELD_BY_OTHER
    assert lock_cmd(tmp_path,'release')==maint_lock.EXIT_NOT_OWNER
    assert lock_cmd(tmp_path,'heartbeat')==maint_lock.EXIT_NOT_OWNER
    assert lock_cmd(tmp_path,'takeover','--model','m')==maint_lock.EXIT_ENV_ERROR  # needs user confirmation
    session('session-a')
    assert lock_cmd(tmp_path,'heartbeat')==0 and lock_cmd(tmp_path,'release','--commit','c')==0
    assert not (tmp_path/'维护中.json').exists()
    assert json.loads((tmp_path/'维护锁历史.jsonl').read_text(encoding='utf-8').splitlines()[-1])['event']=='released'


def test_missing_maintenance_dir_is_an_environment_error_not_a_held_lock(tmp_path,session):
    assert lock_cmd(tmp_path/'absent','acquire','--model','m','--scope','s')==maint_lock.EXIT_ENV_ERROR


def test_heartbeat_never_recreates_a_released_lock(tmp_path):
    with pytest.raises(FileNotFoundError):
        maint_lock.rewrite_existing(tmp_path/'维护中.json',{'owner_token':'x'})
    assert not (tmp_path/'维护中.json').exists()


def test_confirmed_takeover_issues_a_new_token_and_locks_out_the_old_owner(tmp_path,session):
    lock_cmd(tmp_path,'acquire','--model','m','--scope','s')
    old=json.loads((tmp_path/'维护中.json').read_text(encoding='utf-8'))['owner_token']
    session('session-b')
    assert lock_cmd(tmp_path,'takeover','--model','m','--user-confirmed','用户确认原会话已结束')==0
    new=json.loads((tmp_path/'维护中.json').read_text(encoding='utf-8'))
    assert new['owner_token']!=old and new['taken_over_from']['owner_token']==old
    with pytest.raises(PermissionError):
        maint_lock.refresh(tmp_path,old)
    events=[json.loads(l)['event'] for l in (tmp_path/'维护锁历史.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events==['taken_over']


def test_bind_pid_must_be_an_ancestor_of_the_acquiring_process(tmp_path,session):
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    try:
        assert lock_cmd(tmp_path,'acquire','--model','m','--scope','s','--bind-pid',str(child.pid))==maint_lock.EXIT_ENV_ERROR
        assert maint_lock.process_created(child.pid) is not None
    finally:
        child.kill();child.wait()
    assert maint_lock.process_created(child.pid) is None and not (tmp_path/'维护中.json').exists()


# ---------- release extraction ----------

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data if isinstance(data,bytes) else data.encode('utf-8'))


def manifest_for(repo):
    files=[]
    for p in sorted((repo/'skills').rglob('*')):
        if p.is_file():
            data=p.read_bytes();files.append({'path':p.relative_to(repo/'skills').as_posix(),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    return {'schema_version':'cad-qto-runtime-manifest/1.0','runtime_file_count':len(files),'files':files}


def git(repo,*args):
    subprocess.run(['git','-c','user.name=t','-c','user.email=t@example.invalid',*args],cwd=repo,check=True,
                   stdin=subprocess.DEVNULL,capture_output=True)


@pytest.fixture
def mini(tmp_path,monkeypatch):
    repo=tmp_path/'repo';maint=tmp_path/'maint';maint.mkdir()
    write(repo/'skills/a/SKILL.md','---\nname: a\ndescription: synthetic entry\n---\n')
    write(repo/'skills/_shared/x.py','print(1)\n')
    write(repo/'使用说明.md','# 说明\n');write(repo/'README.md','repo only\n');write(repo/'tests/t.py','')
    for rel in ['.codex-plugin/plugin.json','.claude-plugin/plugin.json']:write(repo/rel,json.dumps({'name':'cad-qto','version':'0.3.0'}))
    write(repo/'.claude-plugin/marketplace.json',json.dumps({'name':'cad-qto-local','plugins':[{'name':'cad-qto','source':'./','version':'0.3.0'}]}))
    write(repo/'运行文件清单.json',json.dumps(manifest_for(repo),ensure_ascii=False))
    git(repo,'init','-q');git(repo,'add','-A');git(repo,'commit','-qm','init')
    monkeypatch.setattr(maint_common,'REPO_ROOT',repo);monkeypatch.setattr(rp,'REPO_ROOT',repo)
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True).stdout.strip()
    return repo,maint,commit


def test_build_extracts_exactly_the_manifest_and_whitelist_and_freezes_the_record(mini,tmp_path):
    repo,maint,commit=mini
    result=rp.build(commit,tmp_path/'pkg',maint,maint/'发布记录',False,True)
    files={p.relative_to(tmp_path/'pkg').as_posix() for p in (tmp_path/'pkg').rglob('*') if p.is_file()}
    assert files=={'skills/a/SKILL.md','skills/_shared/x.py',*rp.ROOT_WHITELIST,'发布来源.json'}
    source=json.loads((tmp_path/'pkg/发布来源.json').read_text(encoding='utf-8'))
    assert source['source_commit']==commit and source['business_version']=='0.3.0'
    record=json.loads(Path(result['record']).read_text(encoding='utf-8'))
    assert record['untested_rehearsal'] is True and rp.verify_dir(tmp_path/'pkg',record)['status']=='PASS'
    again=rp.build(commit,tmp_path/'pkg2',maint,maint/'发布记录',False,True)
    assert again['record']==result['record']  # same commit, same frozen record


def test_build_refuses_without_passing_test_record_unless_rehearsal(mini,tmp_path):
    repo,maint,commit=mini
    with pytest.raises(rp.ReleaseError,match='回归记录'):
        rp.build(commit,tmp_path/'pkg',maint,maint/'发布记录',False,False)
    assert not (tmp_path/'pkg').exists()
    fp=rp.tree_fingerprint(commit)['tree_fingerprint'];(maint/'受测记录').mkdir()
    (maint/'受测记录'/f'{fp}.json').write_text(json.dumps({'status':'PASS','recorded_at':'t','summary':{}}),encoding='utf-8')
    assert rp.build(commit,tmp_path/'pkg',maint,maint/'发布记录',False,False)['untested_rehearsal'] is False


def test_worktree_fingerprint_equals_commit_fingerprint_when_clean(mini):
    repo,maint,commit=mini
    assert rp.tree_fingerprint()==rp.tree_fingerprint(commit)
    write(repo/'README.md','changed\n')
    assert rp.tree_fingerprint()['tree_fingerprint']!=rp.tree_fingerprint(commit)['tree_fingerprint']


def test_build_refuses_dirty_tree_tampered_manifest_and_version_mismatch(mini,tmp_path):
    repo,maint,commit=mini
    write(repo/'skills/_shared/x.py','print(2)\n')
    with pytest.raises(rp.ReleaseError,match='未提交'):
        rp.build(commit,tmp_path/'p1',maint,maint/'r',False,True)
    git(repo,'commit','-qam','tamper');tampered=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True).stdout.strip()
    with pytest.raises(rp.ReleaseError,match='tampered'):
        rp.build(tampered,tmp_path/'p2',maint,maint/'r',False,True)
    assert not (tmp_path/'p2').exists()
    write(repo/'.claude-plugin/plugin.json',json.dumps({'name':'cad-qto','version':'0.2.9'}))
    write(repo/'skills/_shared/x.py','print(1)\n');git(repo,'commit','-qam','version')
    mismatch=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True).stdout.strip()
    with pytest.raises(rp.ReleaseError,match='业务版本不一致'):
        rp.build(mismatch,tmp_path/'p3',maint,maint/'r',False,True)
    with pytest.raises(rp.ReleaseError,match='40'):
        rp.build(commit[:12],tmp_path/'p4',maint,maint/'r',False,True)


def test_verify_reports_missing_tampered_extra_and_listed_exemptions(mini,tmp_path):
    repo,maint,commit=mini
    record=json.loads(Path(rp.build(commit,tmp_path/'pkg',maint,maint/'r',False,True)['record']).read_text(encoding='utf-8'))
    pkg=tmp_path/'pkg';(pkg/'skills/a/SKILL.md').unlink();write(pkg/'skills/_shared/x.py','evil\n')
    write(pkg/'stray.txt','x');write(pkg/'.host-meta/info.json','{}')
    report=rp.verify_dir(pkg,record,['.host-meta/*'])
    assert report['status']=='FAIL' and report['missing']==['skills/a/SKILL.md']
    assert report['tampered']==['skills/_shared/x.py'] and report['extra']==['stray.txt'] and report['exempted']==['.host-meta/info.json']


def test_publish_keeps_previous_and_restores_it_when_the_new_copy_fails(mini,tmp_path,monkeypatch):
    repo,maint,commit=mini
    built=rp.build(commit,tmp_path/'pkg',maint,maint/'r',False,True);record=Path(built['record'])
    target=tmp_path/'release';write(target/'old.txt','previous release')
    real=rp.verify_dir;calls=[]
    def flaky(directory,rec,exempt=None):
        calls.append(directory);return real(directory,rec,exempt) if len(calls)==1 else {'status':'FAIL'}
    monkeypatch.setattr(rp,'verify_dir',flaky)
    with pytest.raises(rp.ReleaseError):
        rp.publish(tmp_path/'pkg',record,target,maint)
    assert (target/'old.txt').read_text(encoding='utf-8')=='previous release'
    monkeypatch.setattr(rp,'verify_dir',real)
    result=rp.publish(tmp_path/'pkg',record,target,maint)
    assert result['status']=='PASS' and (target/'发布来源.json').exists() and not (target/'old.txt').exists()
    assert (Path(result['previous_package'])/'old.txt').exists()


def test_codex_install_side_is_pending_never_success(mini,tmp_path):
    repo,maint,commit=mini
    record=Path(rp.build(commit,tmp_path/'pkg',maint,maint/'r',False,True)['record'])
    assert install_check.main(['init','--record',str(record)])==0
    for name in ['codex-install','codex-verify','codex-rollback']:
        assert install_check.main([name,'--record',str(record)])==3
    check=json.loads(install_check.check_path(record).read_text(encoding='utf-8'))
    assert check['hosts']['Codex']['status']=='待 Codex 接续' and check['overall']=='未完成'


# ---------- issue registration ----------

BODY='---\n'+''.join(f'{k}: x\n' for k in ['登记时间','发现宿主','项目与任务','插件版本','涉及Skill','类型','影响识图或数量','阻断程度'])+'---\n## 现象\n'


def test_registration_never_overwrites_and_requires_template_fields(tmp_path):
    (tmp_path/'问题登记/待处理').mkdir(parents=True)
    from datetime import datetime
    when=datetime(2026,10,14,19,32)
    first=register(tmp_path,'Claude','管网','DN/绑定 漏判',BODY,when)
    second=register(tmp_path,'Claude','管网','DN/绑定 漏判',BODY,when)
    assert first.name=='20261014-1932_claude_管网_DN_绑定_漏判.md' and second.name.endswith('_2.md')
    assert not list((tmp_path/'问题登记/待处理').glob('*.tmp'))
    with pytest.raises(ValueError,match='缺少字段'):
        register(tmp_path,'Claude','管网','t','---\n类型: x\n---\n',when)
