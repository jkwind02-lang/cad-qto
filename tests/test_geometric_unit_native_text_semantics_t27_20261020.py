"""Anonymous native text controls; no source unit or SI acceptance is added."""
import json

import ezdxf
import pytest

from test_geometric_units_t27_20261019 import (
    case, check, save_source, units, sha, put, command, SHARED,
)


def native(case, kind, value):
    model = case['doc'].modelspace()
    if kind == 'TEXT':
        return model.add_text(value, dxfattribs={'insert': (2000, 5000), 'height': 100})
    if kind == 'MTEXT':
        return model.add_mtext(value, dxfattribs={'insert': (2000, 5000), 'char_height': 100})
    block = case['doc'].blocks.new('NATIVE_' + kind + str(len(case['doc'].blocks)))
    insert = model.add_blockref(block.name, (2000, 5000))
    if kind == 'ATTDEF':
        return block.add_attdef('LABEL', (0, 0), text=value, dxfattribs={'height': 100})
    return insert.add_attrib('LABEL', value, (2000, 5000), dxfattribs={'height': 100})


def run_cli(case, name):
    request = put(case['tmp'] / (name + '-request.json'), case['request'])
    scope = put(case['tmp'] / (name + '-scope.json'), case['scope'])
    output = case['tmp'] / (name + '-credential.json')
    before = sha(case['path'])
    process = command(SHARED / 'qto_geometric_unit_evidence.py',
                      '--cad-manifest', case['manifest_path'], '--request', request,
                      '--scope', scope, '--output', output)
    (case['tmp'] / (name + '-stdout.log')).write_text(process.stdout, encoding='utf-8')
    (case['tmp'] / (name + '-stderr.log')).write_text(process.stderr, encoding='utf-8')
    put(case['tmp'] / (name + '-actual-cli.json'), dict(
        entry=str(SHARED / 'qto_geometric_unit_evidence.py'),
        arguments=['--cad-manifest', str(case['manifest_path']), '--request', str(request),
                   '--scope', str(scope), '--output', str(output)],
        exit_code=process.returncode, source_path=str(case['path']),
        source_before_sha256=before, source_after_sha256=sha(case['path']),
        output=str(output), stdout=process.stdout, stderr=process.stderr))
    assert sha(case['path']) == before
    return process, json.loads(output.read_text(encoding='utf-8'))


@pytest.mark.parametrize('kind', ['TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'])
@pytest.mark.parametrize('mode', ['caret', 'organization_format'])
def test_native_source_families_decode_controls_and_keep_raw_audit(case, kind, mode):
    raw = ('GASES & CHEMICALS^M^J' if mode == 'caret' else
           r'{\C1;合作单位:}匿名公司' if kind == 'MTEXT' else
           '%%u合作单位%%u：匿名公司')
    entity = native(case, kind, raw)
    save_source(case)
    actual = ezdxf.readfile(case['path']).entitydb[entity.dxf.handle]
    assert units._raw_text(actual) == raw
    decoded = units._text(actual)
    assert ('GASES & CHEMICALS' if mode == 'caret' else '合作单位') in decoded
    assert '^M' not in decoded
    result = check(case)
    search = result['unit_text_search']
    assert search['unit_text_matches'] == []
    audit = [r for r in search['text_decoding_audit'] if r['handle'] == entity.dxf.handle]
    assert audit and all(r['raw_text'] == raw and r['text'] == decoded for r in audit)
    assert all(r['is_unit_candidate'] is False for r in audit)
    assert search['whole_document_text_decoding_audit']
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False


@pytest.mark.parametrize('kind', ['ATTRIB', 'ATTDEF'])
def test_native_embedded_attribute_mtext_is_decoded_and_raw_is_not_flattened(case, kind):
    case['doc'].dxfversion = 'AC1032'
    entity = native(case, kind, 'fallback')
    mtext = case['doc'].modelspace().add_mtext(r'{\C1;建设单位:}\P匿名公司^M^J')
    entity.set_mtext(mtext)
    case['doc'].modelspace().delete_entity(mtext)
    save_source(case)
    loaded = ezdxf.readfile(case['path']).entitydb[entity.dxf.handle]
    assert loaded.has_embedded_mtext_entity
    assert '\\C1;' in units._raw_text(loaded)
    assert units._text(loaded).strip() == '建设单位:\n匿名公司'
    assert check(case)['unit_text_search']['unit_text_matches'] == []


@pytest.mark.parametrize('label', ['合作单位:', '建设单位：', '建設单位:', '设计单位：',
                                    '施工单位:', '监理单位：', '编制单位:', '审图单位：'])
def test_only_finite_explicit_organization_label_is_removed(label):
    assert units._unit_text_candidate(label + '匿名公司') is False
    assert units._unit_text_candidate(label + '匿名公司；坐标单位待定') is True
    assert units._unit_text_candidate(label + '匿名公司\n尺寸以mm为单位') is True
    assert units._unit_text_candidate(label + '其余单位待定') is True


def test_anonymous_public_cli_accepts_only_control_and_organization_noise_removal(case):
    handles = []
    for kind in ['TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF']:
        text = r'{\C1;合作单位:}匿名公司^M^J' if kind == 'MTEXT' else '%%u合作单位%%u：匿名公司^M^J'
        handles.append(native(case, kind, text).dxf.handle)
    handles.append(native(case, 'TEXT', 'GASES & CHEMICALS^M^J').dxf.handle)
    save_source(case)
    process, result = run_cli(case, 'native-noise')
    assert process.returncode == 0, process.stderr
    assert result['status'] == 'INFERRED' and result['quantity_level'] == '规则推算量'
    assert result['frame']['checked_axes'] == 2 and result['dimensions']
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False
    search = result['unit_text_search']
    assert search['unit_text_matches'] == [] and search['uninspected_active_text_carriers'] == []
    assert set(handles) <= {r['handle'] for r in search['text_decoding_audit']}
    assert result['cad_manifest']['sha256'] == sha(case['manifest_path'])


@pytest.mark.parametrize('text', [
    '合作单位：匿名公司；坐标单位：m',
    '建设单位：匿名公司 尺寸以mm为单位',
    '建设单位：匿名公司（其余单位待定）',
    '合作单位：匿名公司^M^J坐标单位待定',
    '建设单位：坐标单位m，尺寸单位mm',
    'in',
])
def test_anonymous_public_cli_retains_true_mixed_pending_or_independent_unit_conflict(case, text):
    entity = native(case, 'TEXT', text)
    save_source(case)
    process, partial = run_cli(case, 'unit-conflict')
    assert process.returncode == 2
    search = partial['unit_text_search']
    assert search['status'] == 'CONFLICT'
    matches = [r for r in search['unit_text_matches'] if r['handle'] == entity.dxf.handle]
    assert matches and matches[0]['raw_text'] == text
    assert '单位' in process.stderr


@pytest.mark.parametrize('carrier', ['ACAD_PROXY_ENTITY', 'OLE2FRAME'])
def test_anonymous_public_cli_unknown_native_carrier_still_blocks(case, carrier):
    native(case, 'TEXT', '合作单位：匿名公司^M^J')
    entity = case['doc'].modelspace().new_entity(carrier, {})
    save_source(case)
    process, partial = run_cli(case, 'unknown-carrier')
    assert process.returncode == 2
    search = partial['unit_text_search']
    assert search['status'] == 'UNRESOLVED' and search['unit_text_matches'] == []
    assert any(r['handle'] == entity.dxf.handle for r in search['uninspected_active_text_carriers'])
