"""One-shot, assertion-guarded source migration used on the isolated repair branch.
Not a runtime dependency. The final main commit excludes this bootstrap script.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path.cwd()
SHARED='skills/_qto-shared-evidence/scripts/'
PIPE='skills/qto-pipe-networks/scripts/build_pipe_inference_input.py'
COMP='skills/qto-components-points/scripts/build_component_instances_from_neutral.py'
EXTRACT=SHARED+'extract_cad_neutral.py'
CONTRACT=SHARED+'qto_review_contract.py'
CORE='skills/_qto-shared-evidence/assets/review/review-core.js'
APP='skills/_qto-shared-evidence/assets/review/review-app.js'


def read(path): return (ROOT/path).read_text(encoding='utf-8-sig')
def write(path,text):
    p=ROOT/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8',newline='\n')
def replace(path,old,new):
    text=read(path)
    if old not in text and new in text:return
    assert text.count(old)==1,(path,'unexpected source anchor',old[:100],text.count(old))
    write(path,text.replace(old,new,1))
def function(path,name,body):
    text=read(path);tree=ast.parse(text)
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    assert len(nodes)==1,(path,name,len(nodes))
    node=nodes[0];lines=text.splitlines(keepends=True)
    indent=' '*node.col_offset
    replacement='\n'.join(indent+line if line else '' for line in body.strip('\n').splitlines())+'\n'
    lines[node.lineno-1:node.end_lineno]=[replacement]
    write(path,''.join(lines))

# 1. Geometry extraction and shared affine transforms.
replace(EXTRACT,'VERSION = "qto-cad-neutral-extractor/1.0.0"','from qto_cad_geometry import GEOMETRY_SCHEMA, entity_geometry as extract_geometry_facts\n\nVERSION = "qto-cad-neutral-extractor/1.1.0"')
function(EXTRACT,'entity_geometry','''def entity_geometry(entity: Any) -> dict[str, Any]:
    """Preserve WCS parametric geometry; never substitute an end-point chord."""
    return extract_geometry_facts(entity)
''')
replace(EXTRACT,'        "geometry_signature": geometry["signature"],','''        "geometry_signature": geometry["signature"],
        "geometry": geometry["geometry"],
        "raw_geometry": geometry["raw_geometry"],
        "geometry_status": geometry["geometry_status"],
        "geometry_issues": geometry["geometry_issues"],
        "bbox3d": geometry["bbox3d"],
        "extrusion": geometry["extrusion"],
        "insert_matrix_wcs": geometry["insert_matrix_wcs"],
        "length_error_estimate": geometry["length_error_estimate"],''')
replace(EXTRACT,'            "extractor_version": VERSION,','            "extractor_version": VERSION,\n            "geometry_schema_version": GEOMETRY_SCHEMA,')
replace(EXTRACT,'                    types[record["entity_type"]] += 1','''                    types[record["entity_type"]] += 1
                    if record.get("geometry_issues"):
                        failures.append({"layout": layout, "parent_block": parent_block,
                                         "entity_handle": record["entity_handle"], "entity_type": record["entity_type"],
                                         "error": "; ".join(record["geometry_issues"]), "record_retained": True})''')
replace(EXTRACT,'json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))','json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)')

for path in (PIPE,COMP):
    replace(path,'VERSION = ', 'VERSION = ') if False else None
    text=read(path)
    marker='\nVERSION = '
    if 'import qto_cad_geometry as cad_geometry' not in text:
        assert text.count(marker)==1
        write(path,text.replace(marker,'\nimport qto_cad_geometry as cad_geometry\n'+marker,1))
    function(path,'matrix_for_insert','''def matrix_for_insert(row: dict[str, Any], origin: list[float]) -> tuple[float, ...]:
    return cad_geometry.matrix_for_insert(row, origin)
''')
    function(path,'compose_matrix' if path==PIPE else 'compose','''def %s(parent: tuple[float, ...], child: tuple[float, ...]) -> tuple[float, ...]:
    return cad_geometry.compose_matrix(parent, child)
''' % ('compose_matrix' if path==PIPE else 'compose'))
    function(path,'transform_point','''def transform_point(value: Any, matrix: tuple[float, ...]) -> list[float] | None:
    return cad_geometry.transform_point(value, matrix)
''')
    anchor='        fail("cad-manifest schema_version 必须为 qto-cad-neutral-evidence/1.0")'
    replace(path,anchor,anchor+'\n    if manifest.get("geometry_schema_version") != cad_geometry.GEOMETRY_SCHEMA:\n        fail("CAD旧缓存缺少完整WCS几何，请用新版中性提取器重新生成；不得继承旧曲线测长")')
replace(PIPE,'VERSION = "qto-pipe-neutral-adapter/1.3.0"','VERSION = "qto-pipe-neutral-adapter/1.4.0"')
replace(COMP,'VERSION = "qto-component-neutral-adapter/1.1.0"','VERSION = "qto-component-neutral-adapter/1.2.0"')
function(PIPE,'transform_row','''def transform_row(row: dict[str, Any], matrix: tuple[float, ...], instance_path: str, target_layout: str) -> dict[str, Any]:
    return cad_geometry.transform_row(row, matrix, instance_path, target_layout)
''')
function(PIPE,'expanded_manifest_rows','''def expanded_manifest_rows(layout_path: Path, block_path: Path, manifest: dict[str, Any],
                           max_depth: int, failures: list[dict[str, Any]],
                           target_layout: str | None = None, scope_bounds: dict[str, float] | None = None) -> Iterable[dict[str, Any]]:
    definitions: dict[str, list[dict[str, Any]]] = {}
    for row in load_stream(block_path):
        definitions.setdefault(str(row.get("parent_block") or ""), []).append(row)
    origins = {str(item["block_name"]): item.get("origin", [0., 0., 0.]) for item in manifest.get("blocks", []) if isinstance(item, dict) and item.get("block_name")}
    extent_cache = {}
    def expand(insert, parent_matrix, path, stack, layout, inherited_layer):
        name = str(insert.get("block_name") or "")
        if not name or name not in definitions:
            failures.append({"object_id": insert.get("record_id"), "kind": "missing_block_definition", "block_name": name, "instance_path": path})
            return
        if name in stack or len(stack) >= max_depth:
            failures.append({"object_id": insert.get("record_id"), "kind": "cyclic_block_reference" if name in stack else "block_depth_limit", "instance_path": path})
            return
        if insert.get("geometry_issues"):
            failures.append({"object_id": insert.get("record_id"), "kind": "unsupported_insert_geometry", "instance_path": path, "issues": insert["geometry_issues"]})
            return
        matrix = compose_matrix(parent_matrix, matrix_for_insert(insert, origins.get(name, [0., 0., 0.])))
        local_box = cad_geometry.definition_bounds(name, definitions, origins, max_depth, extent_cache)
        box = cad_geometry.transform_bounds(local_box, matrix)
        if not cad_geometry.bounds_intersect(box, scope_bounds):
            return
        layer = str(insert.get("layer") or "")
        effective_layer = inherited_layer if layer in {"", "0"} else layer
        for row in definitions[name]:
            child_path = f"{path}/{name}"
            expanded = transform_row(row, matrix, child_path, layout)
            if expanded.get("layer") in {"", "0"}:
                expanded["source_layer"] = expanded.get("layer")
                expanded["layer"] = effective_layer
            yield expanded
            if expanded.get("entity_type") == "INSERT":
                yield from expand(row, matrix, f"{child_path}/{row.get('record_id')}", (*stack, name), layout, effective_layer)
    for row in load_stream(layout_path):
        layout = str(row.get("layout") or "Model")
        if target_layout is not None and layout != target_layout:
            continue
        yield row
        if row.get("entity_type") == "INSERT":
            yield from expand(row, cad_geometry.IDENTITY, str(row.get("record_id") or "LAYOUT-INSERT"), (), layout, str(row.get("layer") or ""))
''')
replace(PIPE,'                block_expansion_failures,\n            )','''                block_expansion_failures,
                target_layout=str((profile.get("scope") or {}).get("layout", "Model")),
                scope_bounds=(profile.get("scope") or {}).get("bounds"),
            )''')
# Unknown extents remain visible instead of being falsely treated as out of scope.
function(PIPE,'bbox_intersects','''def bbox_intersects(row: dict[str, Any], bounds: dict[str, float] | None) -> bool:
    if bounds is None:
        return True
    bbox = row.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4 and all(isinstance(n, (int, float)) and math.isfinite(n) for n in bbox[:4]):
        return not (bbox[2] < bounds["xmin"] or bbox[0] >= bounds["xmax_exclusive"] or bbox[3] < bounds["ymin"] or bbox[1] >= bounds["ymax_exclusive"])
    return True  # incomplete geometry is unresolved, not silently excluded
''')
# Component envelopes also retain Z, using the same block geometry as pipes.
function(COMP,'transformed_box','''def transformed_box(box, matrix):
    return cad_geometry.transform_bounds(box, matrix)
''')
function(COMP,'definition_box','''def definition_box(name, stack=()):
    return cad_geometry.definition_bounds(name, definitions, origins, max_depth, block_boxes, stack)
''')
replace(COMP,'            world_box = transformed_box(local_box, matrix) if local_box else None','''            world_box3d = transformed_box(local_box, matrix) if local_box else None
            world_box = [world_box3d[0], world_box3d[1], world_box3d[3], world_box3d[4]] if world_box3d else None''')
replace(COMP,'            in_scope = bbox_intersects({"bbox": world_box}, bounds) if world_box else (bbox_intersects(insert, bounds) if top_level else point_in_bounds(world_point, bounds))','''            in_scope = bbox_intersects({"bbox": world_box}, bounds) if world_box else True
            if not in_scope:
                return  # only complete, conservative bounds allow pruning
            if insert.get("geometry_issues"):
                dispositions.append({"object_id": f"PT-{path}", "physical_id": f"PT-{path}", "source_record_id": source_record_id,
                                     "disposition": "unresolved", "basis": "; ".join(insert["geometry_issues"]), "rule_ids": [], "instance_path": path})
                failures.append({"kind": "unsupported_insert_geometry", "instance_path": path, "issues": insert["geometry_issues"]})
                return''')

# 2. Snapshot-aware review contract, frontend and read-only handoff.
replace(CONTRACT,"from pathlib import Path","from pathlib import Path\nfrom qto_review_revision import bind_result_versions, classify_feedback")
replace(CONTRACT," json.dumps(d,ensure_ascii=False,allow_nan=False)\n return d"," bind_result_versions(d)\n json.dumps(d,ensure_ascii=False,allow_nan=False)\n return d")
replace(CONTRACT," return p,unmatched"," return classify_feedback(p,view,unmatched)")
replace(CONTRACT,"'drawing_revision':view['revision'],'annotations':{}","'drawing_revision':view['revision'],'result_fingerprint':view['result_fingerprint'],'annotations':{}")
text=read(CORE)
revision_code=read('maintenance/review_revision_js.txt')
if 'function assessRecord(' not in text:
    text=text.replace('function parse(input,view){',revision_code+'\nfunction parse(input,view){',1)
write(CORE,text)
replace(CORE,'   result[k]=q;','''   const validity=assessRecord(q,view);
   q.applicability_state=validity.state;q.applicability_reason=validity.reason;
   result[k]=q;''')
replace(CORE,' return p;\n}\nfunction saveRecord',' p.current_result_fingerprint=view.result_fingerprint;\n return p;\n}\nfunction saveRecord')
replace(CORE,"function receiptState(receipt,annotation){return receipt&&annotation&&receipt.source_annotation_updated_at===annotation.updated_at?'current':receipt?'stale':'none';}","function receiptState(receipt,annotation){return receipt&&annotation&&annotation.result_fingerprint&&annotation.applicability_state!=='stale'&&annotation.applicability_state!=='unmatched'&&receipt.source_annotation_updated_at===annotation.updated_at&&receipt.source_result_fingerprint===annotation.result_fingerprint?'current':receipt?'stale':'none';}")
replace(CORE,'embed,readFileText,receiptState};','embed,readFileText,receiptState,assessRecord};')
replace(APP,"const out={...old,object_id:selected.id,", "const out={...old,result_fingerprint:selected.result_fingerprint,applicability_state:'current',applicability_reason:'用户本轮重新保存意见',object_id:selected.id,")
replace(APP,'professional:selected.professional,scene_id:selected.scene_id,quantities:selected.quantities,attributes:selected.attributes};','professional:selected.professional,scene_id:selected.scene_id,quantities:selected.quantities,attributes:selected.attributes,result_fingerprint:selected.result_fingerprint};')
replace(APP,"  $('entry-status').textContent=feedback.drafts[key(i)]?", "  $('entry-status').textContent=(r.applicability_state==='stale'||r.applicability_state==='unmatched')?'历史意见待重新核对：'+r.applicability_reason:feedback.drafts[key(i)]?")
replace(APP,"这条意见已有更新，旧回执不能证明新意见已应用。","意见或成果版本已变化，旧回执不能证明当前结果已复核。")
replace(APP,"function backup(){feedback.drawing_revision=data.revision;","function backup(){feedback.drawing_revision=data.revision;feedback.current_result_fingerprint=data.result_fingerprint;")
reader=SHARED+'read_qto_review_feedback.py'
replace(reader,"for k,r in fb['annotations'].items()]","for k,r in fb['annotations'].items() if r.get('applicability_state')=='current']")
replace(reader,"'feedback':fb,'actions':actions,'unmatched':unmatched,","'result_fingerprint':view['result_fingerprint'],'feedback':fb,'actions':actions,'historical_annotations':{k:r for k,r in fb['annotations'].items() if r.get('applicability_state')!='current'},'unmatched':unmatched,")
replace(reader,"'saved':len(actions)","'saved':len(fb['annotations']),'actionable':len(actions)")

# Existing positive fixtures now provide a valid snapshot binding. Negative
# cases remain; no assertions are removed to force a green report.
replace('tests/run_html_review_tests.py',"'member_snapshot':g['member_snapshot'],'confirmed_result'","'member_snapshot':g['member_snapshot'],'result_fingerprint':g['result_fingerprint'],'confirmed_result'")
replace('tests/run_html_review_ui_tests.mjs',"source_annotation_updated_at:note.updated_at,object_ids:note.object_ids","source_annotation_updated_at:note.updated_at,source_result_fingerprint:note.result_fingerprint,object_ids:note.object_ids")

replace('tests/run_html_review_ui_tests.mjs',"import fs from 'node:fs';","import {spawnSync} from 'node:child_process';import fs from 'node:fs';")
ui=read('tests/run_html_review_ui_tests.mjs')
if '// Rebuild the changed engineering snapshot' not in ui:
    assert ui.count('const packageFile=')==1
    write('tests/run_html_review_ui_tests.mjs',ui.replace('const packageFile=',read('maintenance/ui_revision_tests.txt')+'\nconst packageFile=',1))

# 3. The word '合计' alone is not a declaration of complete scope.
for path in (ROOT/'skills/qto-surfaces-envelope/scripts').glob('*.py'):
    text=path.read_text(encoding='utf-8-sig')
    tree=ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PARTIAL_FORBIDDEN_WORDS' for t in node.targets):
            value=ast.literal_eval(node.value)
            assert '合计' in value or value==set(value)-{'合计'}
            new=set(value)-{'合计'}
            lines=text.splitlines(keepends=True)
            lines[node.lineno-1:node.end_lineno]=['PARTIAL_FORBIDDEN_WORDS = '+('{'+', '.join(repr(x) for x in sorted(new))+'}' if new else 'set()')+'\n']
            text=''.join(lines)
    path.write_text(text,encoding='utf-8',newline='\n')

# 4. Release metadata is generated from actual runtime bytes.
replace('.codex-plugin/plugin.json','0.1.0+codex.20260914054325','0.1.1+codex.20260920')
readme=read('README.md')
readme=readme.replace('本仓库保存已安装版本 `0.1.0+codex.20260914054325` 的完整发布副本','本仓库当前修复版本为 `0.1.1+codex.20260920`；初始发布基线为 `0.1.0+codex.20260914054325`')
readme=readme.replace('213 个运行文件的大小与 SHA-256','运行文件的大小与 SHA-256（数量见清单）')
if 'docs/修复记录_20260920.md' not in readme:
    readme+='\n## 2026-09-20 审查修复\n\n详见[修复记录与兼容边界](docs/修复记录_20260920.md)。本次修复几何保真、WCS变换、旧批注版本、组成员身份与部分量合计校验；新增实际模块回归测试。旧中性缓存须重新提取，旧意见保留为历史待复核，不自动转为当前数值确认。仓库更新不代表用户电脑已自动更新安装。\n\n```powershell\npython -m pytest tests/test_audit_regressions.py -q\n```\n'
write('README.md',readme)
# Append explicit superseding technical requirements without deleting history.
for path,extra in {
 'skills/_qto-shared-evidence/references/CAD中性事实提取.md': '\n## 1.1 几何契约修订\n\n新提取器保留 `qto-cad-geometry/1.1` 完整WCS参数曲线、原始顶点/bulge、三维包围盒和块实例矩阵。折线/弧线不得按首尾弦长复算；非均匀缩放后的圆弧按仿射参数曲线测量。数值积分给出误差估计，无法满足精度时明确失败。旧清单无 geometry_schema_version 时重新提取，不回填新版本标识掩盖旧数据。未知包围盒不作范围外排除；只有完整保守范围可预筛。样条、网格、阵列块等未覆盖表达保留事实与未决，不把锚点当完整几何。\n',
 'skills/_qto-shared-evidence/references/HTML交互复核与批注.md': '\n## 2026-09-20 成果快照绑定（优先于旧版回执说明）\n\n来源身份继续使用项目、复核范围和图纸哈希；另由规范化器重算对象/问题组 result_fingerprint。批注及成员快照须绑定该值。数量、规格、几何、采用依据变化时旧意见标为 stale，保留在历史中而不进入当前可执行 actions；缺失对象标为 unmatched。组成员逐一核对 object_id，不能仅比较 catalog_key。旧版无绑定批注不自动升级。页面标题或单纯revision标签变化不使工程判断失效。方法意见的理由可参考，旧数字确认不能继承。回执同时绑定 source_annotation_updated_at 与 source_result_fingerprint；两者任一不符都显示过期。\n',
}.items():
    text=read(path)
    if extra.strip() not in text:write(path,text+extra)
manifest_path='运行文件清单.json'
manifest=json.loads(read(manifest_path))
paths={item['path'] for item in manifest['files']}
paths.update(['_qto-shared-evidence/scripts/qto_cad_geometry.py','_qto-shared-evidence/scripts/qto_review_revision.py'])
manifest['files']=[{'path':p,'sha256':hashlib.sha256((ROOT/'skills'/p).read_bytes()).hexdigest(),'bytes':(ROOT/'skills'/p).stat().st_size} for p in sorted(paths)]
manifest['runtime_file_count']=len(manifest['files'])
write(manifest_path,json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
release=json.loads(read('仓库发布核对.json'))
release['audit_fix_20260920']={'version':'0.1.1+codex.20260920','baseline_commit':'cac19cc2729abff6bb84757b5b80e1701f8751e3','runtime_file_count':manifest['runtime_file_count'],'runtime_manifest_sha256':hashlib.sha256((ROOT/manifest_path).read_bytes()).hexdigest(),'validation':'See GitHub Actions and tests/test_audit_regressions.py; no real DWG or business adoption claim','local_installation_updated':False}
write('仓库发布核对.json',json.dumps(release,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'PATCH_APPLIED','runtime_files':len(paths),'version':'0.1.1+codex.20260920'},ensure_ascii=False))
