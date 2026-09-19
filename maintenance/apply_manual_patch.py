"""One-time audited edits on the isolated development branch, never an install hook."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
SHARED='skills/_qto-shared-evidence/scripts/'
PIPE='skills/qto-pipe-networks/scripts/'

def edit(path,fn):
 p=ROOT/path;s=p.read_text(encoding='utf-8-sig');new=fn(s)
 if new==s:raise RuntimeError('No change: '+path)
 with p.open('w',encoding='utf-8',newline='') as f:f.write(new)

def rep(s,a,b,count=1):
 if s.count(a)!=count:raise RuntimeError('Unexpected replacement count '+repr(a[:100])+': '+str(s.count(a)))
 return s.replace(a,b)

def adapter(s):
 s=rep(s,'VERSION = "qto-pipe-neutral-adapter/1.4.0"','from qto_pipe_topology import RouteIndex, route_distance, project_route, geometry_parts, scale_route_geometry\n\nVERSION = "qto-pipe-neutral-adapter/1.5.0"')
 s=rep(s,'                "geometry_signature": row.get("geometry_signature"),','                "geometry_signature": row.get("geometry_signature"),\n                "geometry": scale_route_geometry(row.get("geometry"), factor),\n                "space_id": str(rule.get("space_id") or scope.get("space_id") or layout),\n                "physical_id": row.get("physical_id"),')
 s=rep(s,'                "dn_seeds": [],','                "dn_seeds": [],\n                "geometry": item.get("geometry"),\n                "space_id": item.get("space_id"),\n                "physical_id": item.get("physical_id"),')
 s=rep(s,'        dn_candidates: list[dict[str, Any]] = []','        dn_route_index = RouteIndex(segments)\n        dn_candidates: list[dict[str, Any]] = []')
 s=rep(s,'''                ranked = sorted(
                    (point_segment_distance(anchor, item["start"], item["end"]), item["segment_id"])
                    for item in segments if item["system"] == system
                )''','''                ranked = dn_route_index.nearest(anchor, system, rule.get("space_id") or scope.get("space_id") or layout, 2)''')
 s=rep(s,'first_distance = point_segment_distance(anchor, target["start"], target["end"])','first_distance = route_distance(anchor, target)')
 s=rep(s,'                dn_candidates.append(candidate)','''                target = next(item for item in segments if item["segment_id"] == segment_id)
                _, part_index, parameter = project_route(anchor, geometry_parts(target))
                candidate.update(part_index=part_index, parameter=parameter)
                dn_candidates.append(candidate)
                group_id = candidate["candidate_id"]
                for alt_distance, alt_id in dn_route_index.within(anchor, system, max_distance + max(0.0, min_margin), rule.get("space_id") or scope.get("space_id") or layout):
                    if alt_id == segment_id:
                        continue
                    dn_candidates.append({"candidate_id": f"DNC-{len(dn_candidates) + 1:05d}", "segment_id": alt_id,
                        "dn": candidate["dn"], "source_id": candidate["source_id"], "disposition": "excluded",
                        "reason": "同一标注的其他绑定候选，本轮未采用；并非排除该管段数量", "alternative_group": group_id,
                        "threshold_sensitive": sensitive, "diagnostics": {"distance": alt_distance, "text": raw_text, "rule_id": rule["rule_id"]}})''')
 s=rep(s,'            "gap_classifications": profile.get("gap_classifications", []),','            "gap_classifications": profile.get("gap_classifications", []),\n            "gap_exclusions": profile.get("gap_exclusions", []),\n            "junctions": profile.get("junctions", []),\n            "cross_view_links": profile.get("cross_view_links", []),')
 return s
edit(PIPE+'build_pipe_inference_input.py',adapter)

def audit(s):
 s=rep(s,'CURVE_TYPES = {','sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts"))\nfrom qto_spatial import nearby_pairs\n\nCURVE_TYPES = {')
 s=rep(s,'if length <= 0 or distance(start, end) <= 0:','if length <= 0 or (distance(start, end) <= 0 and not (isinstance(raw.get("geometry"), dict) and raw["geometry"].get("parts"))):')
 s=rep(s,'                "geometry_signature": raw.get("geometry_signature"),','                "geometry_signature": raw.get("geometry_signature"),\n                "space_id": raw.get("space_id", "single-view"),')
 old='''        for left_index, (left_id, left_point) in enumerate(endpoints):
            for right_id, right_point in endpoints[left_index + 1 :]:
                if left_id == right_id:
                    continue
                if distance(left_point, right_point) <= connection_tolerance:
                    connected_endpoint_count[left_id] += 1
                    connected_endpoint_count[right_id] += 1'''
 new='''        groups = [(candidates[oid]["system"], candidates[oid]["space_id"]) for oid, _ in endpoints]
        for left_index, right_index in nearby_pairs([p for _,p in endpoints], connection_tolerance, groups):
            left_id, right_id = endpoints[left_index][0], endpoints[right_index][0]
            if left_id != right_id:
                connected_endpoint_count[left_id] += 1
                connected_endpoint_count[right_id] += 1'''
 s=rep(s,old,new)
 return s
edit(PIPE+'audit_pipe_object_selection.py',audit)

def engine(s):
 s=rep(s,'from model_recognition_basis import validate_working_basis','''from model_recognition_basis import validate_working_basis
from qto_pipe_topology import (prepare_network, make_endpoint_nodes, gap_pairs, add_interfaces,
    NodeLocator, RouteIndex, ambiguous_role_nodes, finalize_network, review_markdown)''')
 s=rep(s,'        data = load(args.input.resolve())','        data = load(args.input.resolve())\n        data, route_preparation = prepare_network(data)\n        uncertain_change_systems = set()')
 s=rep(s,'                "seeds": seeds,','''                "seeds": seeds,
                **{k: raw.get(k) for k in ("geometry", "parent_segment_id", "space_id", "start_token", "end_token", "tangent_start", "tangent_end")},''')
 start=s.index('        endpoint_uf = UnionFind(len(endpoints))');end=s.index('        segment_nodes: dict',start)
 s=s[:start]+'''        node_points, endpoint_node, node_partitions, snap_stats = make_endpoint_nodes(endpoints, segments, snap)

'''+s[end:]
 s=rep(s,'        candidates: dict[int, list[tuple[float, int]]] = defaultdict(list)','        spatial_gap_options = gap_pairs(endpoints, open_endpoints, segments, gap_max)\n        candidates: dict[int, list[tuple[float, int]]] = defaultdict(list)')
 s=rep(s,'            for right in open_endpoints[left_pos + 1 :]:','            for right in spatial_gap_options.get(left, []):')
 s=rep(s,'            outward_a = vector(a_other, a["point"])','            outward_a = tuple(-x for x in a_segment["tangent_start"]) if a["end_name"] == "start" else tuple(a_segment["tangent_end"])')
 s=rep(s,'                outward_b = vector(b_other, b["point"])','                outward_b = tuple(-x for x in b_segment["tangent_start"]) if b["end_name"] == "start" else tuple(b_segment["tangent_end"])')
 s=rep(s,'                    "system": a["system"],','                    "system": a["system"],\n                    "space_id": a_segment["space_id"],')
 # Fix the bridge record to read its own member, not a stale loop local.
 s=rep(s,'                    "space_id": a_segment["space_id"],','                    "space_id": segments[a["segment_id"]]["space_id"],')
 start=s.index('        def nearest_node(');end=s.index('        change_nodes: set',start)
 s=s[:start]+'''        interface_edges = add_interfaces(route_preparation, endpoint_lookup, endpoint_node, graph)
        node_locator = NodeLocator(node_points, node_partitions, segment_nodes, segments, max(terminal_max, snap, 1e-9))
        def nearest_node(raw_point, system, maximum, context=None):
            return node_locator.bind({**(context or {}), "point": list(raw_point), "system": system}, maximum)

'''+s[end:]
 s=rep(s,'node = nearest_node(raw_point, system, terminal_max)','node = nearest_node(raw_point, system, terminal_max, raw)')
 s=rep(s,'node = nearest_node(point(raw.get("point"), f"roots[{index}].point"), system, terminal_max)','node = nearest_node(point(raw.get("point"), f"roots[{index}].point"), system, terminal_max, raw)')
 s=rep(s,'node = nearest_node(terminal_point, system, terminal_max)','node = nearest_node(terminal_point, system, terminal_max, raw)')
 s=rep(s,'                fail(f"change_nodes[{index}] 未绑定到网络节点")','''                uncertain_change_systems.add(system)
                route_preparation["issues"].append({"type": "变径边界未绑定，受影响系统停止规格传播", "object": raw})
                continue''')
 s=rep(s,'                fail(f"roots[{index}] 未绑定到网络节点")','''                route_preparation["issues"].append({"type": "水源或立管根节点未绑定", "object": raw})
                continue''')
 s=rep(s,'                fail(f"{terminal_id} 未绑定到网络端点")','''                route_preparation["issues"].append({"type": "末端端口未绑定", "object": raw})
                continue''')
 s=rep(s,'        raw_endpoint_handoff = data.get("endpoint_handoff")','''        raw_endpoint_handoff = data.get("endpoint_handoff")
        if isinstance(raw_endpoint_handoff, dict):
            unbound = len(data.get("terminals", [])) - len(terminal_records)
            route_preparation["endpoint_receipt"] = {"provided": len(data.get("terminals", [])), "bound": len(terminal_records), "unbound": unbound}
            raw_endpoint_handoff = dict(raw_endpoint_handoff)
            raw_endpoint_handoff["delivered_count"] -= unbound
            raw_endpoint_handoff["unresolved_count"] += unbound''')
 s=rep(s,'            if node in change_nodes:\n                continue','            if node in change_nodes or any(segments[sid]["system"] in uncertain_change_systems for sid in incident):\n                continue')
 s=rep(s,'            if bridge["node_a"] in change_nodes or bridge["node_b"] in change_nodes:\n                continue','            if bridge["node_a"] in change_nodes or bridge["node_b"] in change_nodes or bridge["system"] in uncertain_change_systems:\n                continue')
 s=rep(s,'        terminal_seeds: dict[str, list[dict[str, Any]]] = defaultdict(list)','''        for link in interface_edges:
            if link.get("dn_continuity") is not True or link["system"] in uncertain_change_systems:
                continue
            if link["node_a"] in change_nodes or link["node_b"] in change_nodes:
                continue
            left_set, right_set = node_segments[link["node_a"]], node_segments[link["node_b"]]
            if len(left_set) == len(right_set) == 1:
                left, right = next(iter(left_set)), next(iter(right_set))
                adjacency[left].add(right); adjacency[right].add(left)

        terminal_seeds: dict[str, list[dict[str, Any]]] = defaultdict(list)''')
 s=rep(s,'        role_dn_rules = []','''        ambiguous_nodes = ambiguous_role_nodes(graph, roots_by_system)
        for sid, (left, right) in segment_nodes.items():
            if left in ambiguous_nodes or right in ambiguous_nodes:
                roles[sid] = {"role": "环网", "downstream_terminal_count": 0, "role_basis": "环路或多源不套用树状下游数量"}
        if ambiguous_nodes:
            route_preparation["issues"].append({"type": "环网或多水源需按系统图复核主支管", "node_count": len(ambiguous_nodes)})

        role_dn_rules = []''')
 s=rep(s,'                directions.append(vector(node_points[node_id], target))','                directions.append(tuple(segments[segment_id]["tangent_start"]) if left == node_id else tuple(-x for x in segments[segment_id]["tangent_end"]))')
 s=rep(s,'        valve_records = []','        valve_route_index = RouteIndex(list(segments.values()))\n        valve_records = []')
 old='''            ranked = sorted(
                (
                    point_segment_distance(valve_point, segment["start"], segment["end"]),
                    segment_id,
                )
                for segment_id, segment in segments.items()
                if segment["system"] == system
            )'''
 new='''            spaces = {r["space_id"] for r in segments.values() if r["system"] == system}
            valve_space = raw.get("space_id") or (next(iter(spaces)) if len(spaces) == 1 else None)
            ranked = valve_route_index.within(valve_point, system, valve_max, valve_space)'''
 s=rep(s,old,new)
 s=rep(s,'            attached_segments = nearby_segments','            attached_segments = []  # proximity alone is not an actual online connection')
 s=rep(s,'contact_segments = [sid for gap, sid in ranked if gap <= snap]','contact_segments = [sid for gap, sid in ranked if gap <= snap and valve_space is not None]')
 s=rep(s,'                        if bridge["system"] != system:\n                            continue','                        if bridge["system"] != system or bridge.get("space_id") != valve_space:\n                            continue')
 s=rep(s,'                    "nearby_segment_ids": nearby_segments,','                    "nearby_segment_ids": nearby_segments,\n                    "nearby_dn_candidates": sorted({assignments[sid]["dn"] for sid in nearby_segments if assignments[sid]["dn"]}),\n                    "space_id": valve_space,')
 s=rep(s,'                and point_segment_distance(tuple(item["point"]), bridge_start, bridge_end) <= valve_max','                and item.get("space_id") == bridge.get("space_id")\n                and point_segment_distance(tuple(item["point"]), bridge_start, bridge_end) <= snap')
 s=rep(s,'        output_path = args.output.resolve()','''        output = finalize_network(output, route_preparation, segments, snap_stats)
        output_path = args.output.resolve()''')
 s=rep(s,'        print(\n            json.dumps(\n                {\n                    "status": "PASS",','''        import hashlib
        summary = review_markdown(output)
        summary_path = output_path.with_name(output_path.stem + "_" + hashlib.sha256(summary.encode()).hexdigest()[:10] + "_复核摘要.md")
        if summary_path.exists() and summary_path.read_text(encoding="utf-8") != summary:
            fail("复核摘要文件冲突，请使用新版本输出目录")
        summary_path.write_text(summary, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "review_summary": str(summary_path),''')
 return s
edit(PIPE+'infer_pipe_network.py',engine)

def minimum(s):
 s=rep(s,'from model_recognition_basis import validate_working_basis','from model_recognition_basis import validate_working_basis\nfrom recompute_qto_dependencies import check_result_dependencies')
 s=rep(s,'    return errors\n\n\ndef main()','    errors.extend(check_result_dependencies(data))\n    return errors\n\n\ndef main()')
 return s
edit(SHARED+'validate_minimum_result.py',minimum)

def pipevalidator(s):
 s=rep(s,'                    endpoint_handoff.get("source_instance_count"),','                    endpoint_handoff.get("source_endpoint_port_count", endpoint_handoff.get("source_instance_count")),')
 marker='    status = "FAIL" if errors else ("PASS_WITH_LIMITATIONS" if warnings else "PASS")'
 check='''    if args.inference and isinstance(inference.get("route_preparation"), dict):
        prep = inference["route_preparation"]
        if not math.isclose(as_number(prep.get("length_delta")) or 0, 0, abs_tol=1e-7):
            errors.append("原工程路线分段前后长度不守恒")
        parent_map = prep.get("parent_map", {})
        children = [sid for values in parent_map.values() for sid in values]
        actual = [r.get("segment_id") for r in inference.get("segments", [])]
        if len(children) != len(set(children)) or set(children) != set(actual):
            errors.append("原对象与派生管段映射不完整或重复")
        source_lengths = prep.get("source_lengths", {})
        edge_lengths = {r["segment_id"]: r["length"] for r in inference.get("segments", [])}
        for parent, keys in parent_map.items():
            if parent not in source_lengths or any(k not in edge_lengths for k in keys) or not math.isclose(sum(edge_lengths.get(k, 0) for k in keys), source_lengths.get(parent, -1), abs_tol=1e-7, rel_tol=1e-10):
                errors.append("派生管段与原路线长度不符: " + parent)
        if prep.get("issues"):
            warnings.append("路径识别或跨图关系尚有未决，详见自动生成复核摘要")

'''
 s=rep(s,marker,check+marker)
 return s
edit(PIPE+'validate_pipe_result.py',pipevalidator)

# Version changes do not change capability tiers or business-adoption flags.
def plugin(s):
 d=json.loads(s);d['version']='0.2.0+codex.20260920';return json.dumps(d,ensure_ascii=False,indent=2)+'\n'
edit('.codex-plugin/plugin.json',plugin)
