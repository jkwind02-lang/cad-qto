"""One authoritative delivery payload per HTML, with complete feedback round trips."""
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
import build_qto_review_html as builder
import qto_review_contract as contract
import review_delivery_fixture as fixture


def build(tmp_path):
    raw=fixture.view(False,True)
    source=tmp_path/'view.json';source.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
    html=tmp_path/'page.html';builder.build(source,html,feedback_url='')
    return contract.normalize_view(raw),html


def seed(text):
    return json.loads(re.search(r'<script id="feedback-seed" type="application/json">([\s\S]*?)</script>',text).group(1))


def test_builder_and_python_reader_reconstruct_exact_current_snapshot(tmp_path):
    view,html=build(tmp_path);data=seed(html.read_text('utf-8'))
    assert 'delivery_snapshot' not in data
    assert data['delivery_snapshot_ref']['result_fingerprint']==view['result_fingerprint']
    restored=contract.extract_json_file(html)
    assert restored['delivery_snapshot']==contract.empty_feedback(view)['delivery_snapshot']
    assert len(json.dumps(data,ensure_ascii=False))<len(json.dumps(restored,ensure_ascii=False))/2


def test_browser_save_restore_preserves_history_and_export_is_full(tmp_path):
    view,html=build(tmp_path)
    vp=tmp_path/'normalized.json';vp.write_text(json.dumps(view,ensure_ascii=False),encoding='utf-8')
    saved=tmp_path/'saved.html';out=tmp_path/'export.json'
    js="""
(async()=>{const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const c=globalThis.QtoReviewCore,html=fs.readFileSync(process.argv[2],'utf8'),view=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
let p=c.parse(await c.readFileTextAsync(html),view);assert.deepEqual(p.delivery_snapshot.delivery,view.delivery);
p.historical_delivery_snapshots=[{result_fingerprint:'old',delivery:{notice:'完整历史事实',quantity_rows:[{quantity:null,basis:'旧依据'}]}}];
const saved=c.embed(html,p,view),embedded=await c.readFileTextAsync(saved);
const restored=c.parse(embedded,view);assert.deepEqual(restored.delivery_snapshot,p.delivery_snapshot);
assert.deepEqual(restored.historical_delivery_snapshots,p.historical_delivery_snapshots);
fs.writeFileSync(process.argv[4],saved);fs.writeFileSync(process.argv[5],JSON.stringify(restored));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    core=ROOT/'skills/_qto-shared-evidence/assets/review/review-core.js'
    r=subprocess.run(['node','-e',js,str(core),str(html),str(vp),str(saved),str(out)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert r.returncode==0,r.stdout+r.stderr
    exported=json.loads(out.read_text('utf-8'));restored=contract.extract_json_file(saved)
    assert restored['delivery_snapshot']==exported['delivery_snapshot']
    assert restored['historical_delivery_snapshots']==exported['historical_delivery_snapshots']


def test_mismatched_html_reference_is_rejected_without_executing_scripts(tmp_path):
    _,html=build(tmp_path);text=html.read_text('utf-8');data=seed(text)
    data['delivery_snapshot_ref']['result_fingerprint']='0'*64
    text=re.sub(r'(<script id="feedback-seed" type="application/json">)[\s\S]*?(</script>)',lambda m:m[1]+json.dumps(data)+m[2],text)
    html.write_text(text,encoding='utf-8')
    with pytest.raises(ValueError,match='当前交付引用与内嵌视图不一致'):contract.extract_json_file(html)


def test_legacy_full_snapshot_html_still_reads(tmp_path):
    view,html=build(tmp_path);text=html.read_text('utf-8');full=contract.empty_feedback(view)
    full.pop('delivery_snapshot_ref',None)
    text=re.sub(r'(<script id="feedback-seed" type="application/json">)[\s\S]*?(</script>)',lambda m:m[1]+json.dumps(full)+m[2],text)
    html.write_text(text,encoding='utf-8')
    assert contract.extract_json_file(html)==full


@pytest.mark.parametrize('bad_ref',[None,[], 'not-a-reference'])
def test_malformed_reference_is_a_controlled_cli_rejection(tmp_path,bad_ref):
    _,html=build(tmp_path);text=html.read_text('utf-8');data=seed(text)
    data['delivery_snapshot_ref']=bad_ref
    text=re.sub(r'(<script id="feedback-seed" type="application/json">)[\s\S]*?(</script>)',lambda m:m[1]+json.dumps(data)+m[2],text)
    html.write_text(text,encoding='utf-8');out=tmp_path/'handoff.json'
    r=subprocess.run([sys.executable,str(ROOT/'skills/_qto-shared-evidence/scripts/read_qto_review_feedback.py'),
        '--view',str(tmp_path/'view.json'),'--feedback',str(html),'--output',str(out)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert r.returncode==2 and 'INPUT_REQUIRED' in r.stdout and 'Traceback' not in r.stderr
    assert not out.exists()


def test_cross_result_import_keeps_source_snapshot_and_plain_page_preserves_history(tmp_path):
    first,html=build(tmp_path);changed=fixture.view(False,True)
    changed['delivery']['quantity_rows'][0]['basis']+='（下一轮）'
    latest=contract.normalize_view(changed);vp=tmp_path/'latest.json';vp.write_text(json.dumps(latest,ensure_ascii=False),encoding='utf-8')
    legacy=copy.deepcopy(changed);legacy.pop('delivery');lp=tmp_path/'legacy.json';lp.write_text(json.dumps(legacy,ensure_ascii=False),encoding='utf-8')
    old_html=tmp_path/'legacy.html';builder.build(lp,old_html,feedback_url='')
    plain_view=tmp_path/'plain-view.json';plain_view.write_text(json.dumps(contract.normalize_view(legacy),ensure_ascii=False),encoding='utf-8')
    saved=tmp_path/'legacy-saved.html';core=ROOT/'skills/_qto-shared-evidence/assets/review/review-core.js'
    js="""(async()=>{const fs=require('node:fs'),vm=require('node:vm'),a=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));const c=globalThis.QtoReviewCore;
const incoming=await c.readFileTextAsync(fs.readFileSync(process.argv[2],'utf8'));
const old=incoming.delivery_snapshot,next=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const p=c.parse(incoming,next);a(p.historical_delivery_snapshots.some(s=>s.result_fingerprint===old.result_fingerprint));
a.notEqual(p.delivery_snapshot.result_fingerprint,old.result_fingerprint);
const legacy=fs.readFileSync(process.argv[4],'utf8'),v=JSON.parse(fs.readFileSync(process.argv[5],'utf8'));
const local=c.parse(c.readFileText(legacy),v),oldImport=c.parse(incoming,v),merged=c.mergePackages(local,oldImport);
a(merged.historical_delivery_snapshots.some(s=>s.result_fingerprint===old.result_fingerprint));
const saved=c.embed(legacy,p,v),restored=c.readFileText(saved);a.deepEqual(restored.delivery_snapshot,p.delivery_snapshot);
a.deepEqual(restored.historical_delivery_snapshots,p.historical_delivery_snapshots);fs.writeFileSync(process.argv[6],saved);
})().catch(e=>{console.error(e);process.exitCode=1;});"""
    r=subprocess.run(['node','-e',js,str(core),str(html),str(vp),str(old_html),str(plain_view),str(saved)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert r.returncode==0,r.stdout+r.stderr
    restored=contract.extract_json_file(saved)
    assert any(s['result_fingerprint']==first['result_fingerprint'] for s in restored['historical_delivery_snapshots'])
