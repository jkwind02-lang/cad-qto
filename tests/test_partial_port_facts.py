import json, subprocess, sys
import pytest
from test_manual_routes import ROOT


def test_unlocated_port_retains_known_dn_without_creating_terminal(tmp_path):
    d={'schema_version':'qto-network-endpoint-handoff-input/1.1','handoff_id':'detail-port',
       'source_id':'plan-and-detail','consumer_skill':'qto-pipe-networks','instances':[
           {'physical_id':'heater-1','category':'water-heater','system':'HW','evidence':'detail-1',
            'network_ports':[{'port_id':'cold','network_disposition':'unresolved','reason':'position is not dimensioned',
                              'port_dn':'DN15','port_role':'cold-inlet','port_source_id':'detail-1',
                              'port_evidence_level':'inferred','propagation_scope':'interface_only'}]}]}
    src=tmp_path/'in.json';dst=tmp_path/'out.json';src.write_text(json.dumps(d),encoding='utf-8')
    r=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-components-points/scripts/build_network_endpoint_handoff.py'),
                      '--input',str(src),'--output',str(dst)],capture_output=True)
    assert r.returncode==0,r.stderr
    out=json.loads(dst.read_text(encoding='utf-8'))
    assert out['delivered_count']==0 and out['unresolved_count']==1 and not out['terminals']
    known=out['dispositions'][0]['known_facts']
    assert known['port_dn']=='DN15' and known['port_role']=='cold-inlet'
    assert known['port_evidence_level']=='inferred' and 'point' not in known


@pytest.mark.parametrize('facts',[{'port_dn':0},{'point':[float('nan'),0,0]},{'port_evidence_level':'assumed-confirmed'}])
def test_unresolved_facts_still_reject_invalid_known_values(tmp_path,facts):
    d={'schema_version':'qto-network-endpoint-handoff-input/1.1','handoff_id':'partial',
       'source_id':'detail','consumer_skill':'qto-pipe-networks','instances':[
       {'physical_id':'P','category':'heater','system':'W','evidence':'detail',
        'network_ports':[{'port_id':'CW','network_disposition':'unresolved','reason':'unlocated',**facts}]}]}
    src=tmp_path/'in.json';out=tmp_path/'out.json';src.write_text(json.dumps(d),encoding='utf-8')
    r=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-components-points/scripts/build_network_endpoint_handoff.py'),
                      '--input',str(src),'--output',str(out)],capture_output=True)
    assert r.returncode!=0 and not out.exists()
