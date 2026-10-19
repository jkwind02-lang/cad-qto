"""Track virtual-gap hypotheses independently from drawn-pipe evidence."""
import copy
import heapq
from collections import defaultdict


def isolate_gap_dependencies(assignments, segments, terminal_seeds, adjacency, independent_adjacency, bridge_links):
    # These are evidence inputs, not previously propagated assignment states.
    segment_seeds={sid:copy.deepcopy(row['seeds']) for sid,row in segments.items()}
    seeds={sid:segment_seeds[sid]+copy.deepcopy(terminal_seeds[sid]) for sid in segments}
    independent={}
    seen = set()
    component_number=0
    rank={'scenario':1,'inferred':2,'confirmed':3}
    levels={'scenario':'方案暂估量','inferred':'规则推算量','confirmed':'确认量'}
    for sid in sorted(segments):
        if sid in seen:
            continue
        todo, members = [sid], set()
        while todo:
            current=todo.pop()
            if current in members: continue
            members.add(current);todo.extend(independent_adjacency.get(current,set())-members)
        seen.update(members)
        component_number+=1
        rows=[s for member in members for s in seeds[member]]
        dns=sorted({s['dn'] for s in rows})
        run_id='INDEPENDENT-'+str(component_number).zfill(4)
        for member in members:
            own=segment_seeds[member]
            # Direct pipe seeds keep their actual own strength; a source at a
            # neighbouring terminal was already capped at pipe-propagation level.
            local=seeds[member]
            own_dns={s['dn'] for s in own}
            local_dns={s['dn'] for s in local}
            if len(dns)==1:
                dn=dns[0]
                support=own if own and own_dns=={dn} else rows
                best=max((s['evidence_level'] for s in support),key=rank.__getitem__)
                level=best if support is own else ('scenario' if best=='scenario' else 'inferred')
                method='direct_or_bound_seed' if support is own else 'constrained_through_run_propagation'
                alternatives=[]
            elif len(dns)>1 and len(local_dns)==1:
                dn=next(iter(local_dns));support=local
                level=max((s['evidence_level'] for s in support),key=rank.__getitem__)
                method='local_seed_with_run_conflict';alternatives=dns
            else:
                dn=None;support=rows;level='scenario' if dns else None
                method='multi_dn_run_requires_change_boundary' if dns else 'no_independent_dn_path'
                alternatives=dns
            independent[member]={'dn':dn,'evidence_level':level,'quantity_level':levels[level] if level else '未决量',
                'method':method,'run_id':run_id,'source_ids':sorted({s['source_id'] for s in support}),
                'assumption_ids':sorted({s['assumption_id'] for s in support if isinstance(s.get('assumption_id'),str)}),
                'alternatives':alternatives}

    def path_to_seed(sid,dn):
        todo=[(0,0,sid,())];best={}
        while todo:
            count,depth,current,deps=heapq.heappop(todo)
            if current in best and best[current]<=(count,depth): continue
            best[current]=(count,depth)
            matches=[s for s in seeds[current] if s['dn']==dn]
            if matches:
                return matches,set(deps)
            for other in sorted(adjacency[current]):
                added=tuple(b['assumption_id'] for b in bridge_links.get(frozenset((current,other)),[]))
                combined=tuple(sorted(set(deps)|set(added)))
                heapq.heappush(todo,(len(combined),depth+1,other,combined))
        return [],set()

    for sid,assignment in assignments.items():
        own=independent[sid]
        if own['dn'] is not None or own['alternatives']:
            # Rebuild strength and provenance from raw independent inputs. Never
            # retain the stronger state previously borrowed across a weak bridge.
            run_id=assignment['run_id']
            assignment.update(copy.deepcopy(own));assignment['run_id']=run_id
            assignment.pop('requires_topology_assumption',None)
            assignment.pop('gap_assumption_ids',None)
            continue
        dn=assignment['dn']
        if dn is None:
            candidates=assignment.get('alternatives',[])
            deps=set();raw_assumptions=set();raw_sources=set()
            for candidate in candidates:
                matches,used=path_to_seed(sid,candidate)
                deps.update(used)
                raw_assumptions.update(s['assumption_id'] for s in matches if isinstance(s.get('assumption_id'),str))
                raw_sources.update(s['source_id'] for s in matches)
            if deps:
                assignment.update(evidence_level='scenario',quantity_level='方案暂估量',
                    assumption_ids=sorted(raw_assumptions|deps),source_ids=sorted(raw_sources),gap_assumption_ids=sorted(deps),
                    requires_topology_assumption=True,method='conditional_virtual_gap_dn_alternatives')
            continue
        matches,deps=path_to_seed(sid,dn)
        if not deps and assignment['method'] in {'project_role_dn_rule','multiple_project_role_dn_rules'}:
            # Role rules are scenarios already; all participating candidate gap
            # hypotheses remain explicit rather than being lost in role counts.
            reached={sid};todo=[sid]
            while todo:
                current=todo.pop()
                for other in adjacency[current]-reached: reached.add(other);todo.append(other)
            deps={b['assumption_id'] for pair,rows in bridge_links.items() if pair<=reached for b in rows}
        if not deps: continue
        assumptions=({s['assumption_id'] for s in matches if isinstance(s.get('assumption_id'),str)}
                     if matches else set(assignment.get('assumption_ids',[])))|deps
        if matches:
            assignment['source_ids']=sorted({s['source_id'] for s in matches})
        assignment.update(evidence_level='scenario',quantity_level='方案暂估量',
                          assumption_ids=sorted(assumptions),gap_assumption_ids=sorted(deps),
                          requires_topology_assumption=True,method='conditional_virtual_gap_dn_propagation')
    return independent


def summarize_assignments(assignments,segments):
    summary=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))
    for sid,row in assignments.items():
        summary[segments[sid]['system']][row['quantity_level']][row['dn'] or 'DN待定']+=segments[sid]['length']
    return summary
