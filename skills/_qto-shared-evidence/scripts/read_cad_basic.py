#!/usr/bin/env python3
"""Read CAD basic data with the common reader; never invoke takeoff functions."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
from collections import Counter

VERSION='cad-basic-read/1.0.0'


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(args):
    source_dwg=args.source_dwg.resolve() if args.source_dwg else None
    dxf=args.source_dxf.resolve() if args.source_dxf else None
    if source_dwg is None and dxf is None:raise ValueError('需要原DWG或保留原生数据的DXF')
    for p in (source_dwg,dxf):
        if p is not None and not p.is_file():raise ValueError('输入不存在：'+str(p))
    protected={p:sha(p) for p in (source_dwg,dxf) if p is not None}
    out=args.output_dir.resolve()
    if out.exists():raise ValueError('输出目录已存在，请使用新的业务目录，避免覆盖已有成果')
    out.mkdir(parents=True,exist_ok=False)
    if dxf is None:
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:raise ValueError('DWG转换需要PowerShell及现有CAD引擎；也可提供可追溯DXF')
        dxf=out/'工作文件/原生交换副本.dxf';dxf.parent.mkdir(exist_ok=False)
        command=[shell,'-NoProfile','-File',str(Path(__file__).with_name('export_dwg_exchange_dxf.ps1')),
            '-SourceDwg',str(source_dwg),'-OutputDxf',str(dxf),'-ExportBackend',args.export_backend,
            '-TimeoutSeconds',str(args.export_timeout)]
        extra={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        p=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=args.export_timeout+60,**extra)
        (out/'工作文件/转换日志.txt').write_bytes(p.stdout+p.stderr)
        if p.returncode:raise ValueError('DWG转换失败，详见转换日志；原图未改写')
    protected[dxf]=sha(dxf)
    from cad_native.runtime_audit import modules,vendor_module,install_reader_guard
    before=modules() if os.name=='nt' else None
    guard=install_reader_guard()
    import extract_cad_neutral
    argv=['--source-dxf',str(dxf),'--output-dir',str(out)]
    if source_dwg:argv.extend(['--source-dwg',str(source_dwg)])
    code=extract_cad_neutral.main(argv)
    if code not in (0,3):raise ValueError('公共CAD读取失败，检查输出中的原始错误记录')
    manifest=json.loads((out/'CAD中性取证清单.json').read_text(encoding='utf8'))
    native=json.loads((out/'CAD原生构件基础数据.json').read_text(encoding='utf8'))
    indexed={o['handle']:o for o in native['objects']}
    active=native['instances'];active_handles={i['definition_handle'] for i in active}
    unread=[{'handle':o['handle'],'class':o['class_name'],'status':o['decoded']['status'],
        'reason':o['decoded'].get('stopped_at',o['decoded'].get('reason',o['decoded']['status']))} for o in native['objects'] if not o['read_state']['derived_serialization_complete']]
    after=modules() if os.name=='nt' else None
    vendor=[p for p in set((before or [])+(after or [])) if vendor_module(p)]
    if vendor:raise ValueError('读取进程检测到专有运行模块，不能宣称本次独立读取通过')
    if any(sha(p)!=h for p,h in protected.items()):raise ValueError('读取期间来源发生改变，结果须重新核验')
    report={'schema':'cad-basic-read/1.0','reader_version':VERSION,'decoder_baseline':'0.5.1-native-fields-inline',
        'purpose':'只读取基础数据','status':manifest['status'],'calculation_performed':False,
        'source':manifest['authoritative_source'],'exchange_dxf':manifest['exchange_dxf'],
        'common_reader_manifest':{'path':str(out/'CAD中性取证清单.json'),'sha256':sha(out/'CAD中性取证清单.json')},
        'native_artifact':manifest['artifacts']['native_basic_data'],
        'native_summary':native['summary'],
        'active_native_instances':len(active),
        'active_native_classes':dict(Counter(indexed[i['definition_handle']]['class_name'] for i in active)),
        'active_native_fully_decoded_instances':sum(indexed[i['definition_handle']]['read_state']['derived_serialization_complete'] for i in active),
        'inactive_native_definitions':sum(o['handle'] not in active_handles and o['type']!='ACAD_PROXY_OBJECT' for o in native['objects']),
        'native_configuration_containers':sum(o['type']=='ACAD_PROXY_OBJECT' for o in native['objects']),
        'unresolved_native_records':unread,
        'runtime_audit':{'pid':os.getpid(),'modules_before':before,'modules_after':after,
            'module_inspection_supported':os.name=='nt','vendor_modules_detected':vendor,
            'reader_guard':guard,'scope':'reading process; DWG conversion precedes guard and has a separate receipt'},
        'inputs_unchanged':True,
        'limitations':['专用字段读完不自动等于全部曲线语义已核明。',
            '0.5.1尚未支持的配置、类别或版本保留原记录和停止原因。',
            '活动实例、未实例化定义和配置容器分开报告，不代表实物数量。']}
    result=out/'CAD基础读取回执.json'
    result.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    json.loads(result.read_bytes().decode('utf8',errors='strict'))
    print(json.dumps({'status':report['status'],'receipt':str(result),'active_native_instances':len(active),
        'active_native_fully_decoded_instances':report['active_native_fully_decoded_instances'],'calculation_performed':False},ensure_ascii=False))
    return 0 if report['status']=='PASS' else 3


def main(argv=None):
    p=argparse.ArgumentParser(description='读取普通CAD和天正构件基础数据，不做算量')
    p.add_argument('--source-dxf',type=Path);p.add_argument('--source-dwg',type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--export-backend',choices=['auto','coreconsole','oda','oda-audit'],default='coreconsole')
    p.add_argument('--export-timeout',type=int,default=600)
    args=p.parse_args(argv)
    try:return run(args)
    except (ValueError,OSError,RuntimeError,subprocess.TimeoutExpired) as error:
        print('ERROR: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
