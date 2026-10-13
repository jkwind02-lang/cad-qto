"""Real browser regression for independent records on the same physical surface.

Requires Playwright and Chromium. --html accepts a generated local review page;
the test saves only local copies and blocks all HTTP/WebSocket requests.
"""
import argparse, base64, gzip, hashlib, json, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright


def payload(text):
    return json.loads(gzip.decompress(base64.b64decode(re.search(r'id="payload"[^>]*>(.*?)</script>', text, re.S)[1])))


def synthetic_page(root, output):
    """Four layers distributed across two list pages, without project evidence."""
    sys.path.insert(0, str(root / '_qto-shared-evidence/scripts'))
    from qto_review_contract import VIEW_SCHEMA
    from build_qto_review_html import build
    objects=[]; members=[]
    for i in range(79):
        layer=i in (0,25,50,78);key=f'synthetic|{i:03d}'
        if layer:members.append(key)
        objects.append({'id':'shared-floor' if layer else f'surface-{i}',
            'catalog_key':key,'professional':'qto-surfaces-envelope',
            'category':'material layers','title':f'Layer {i}' if layer else f'Surface {i}',
            'scene_id':'plan','kind':'polygon','points':[[0,0],[4,0],[4,3],[0,3]],
            'status':'synthetic','source':'Synthetic regression fixture','detail':'Independent record identity',
            'quantities':[{'name':'area','value':12,'unit':'m²','level':'synthetic'}]})
    data={'schema':VIEW_SCHEMA,'project_id':'synthetic-record-identity','review_id':'synthetic',
        'title':'Synthetic material record identity','revision':'1',
        'sources':[{'id':'S','title':'Synthetic source','sha256':hashlib.sha256(b'synthetic-record-identity').hexdigest()}],
        'scenes':[{'id':'plan','source_id':'S','title':'Synthetic plan','coordinate_unit':'m',
            'y_axis':'up','bounds':[-1,-1,5,4],'background':[]}],
        'objects':objects,'groups':[{'id':'layers','title':'Material layers','member_keys':members,
            'category':'Material identity','proposal':'Review each layer','reason':'Layers share one surface','status':'synthetic'}],
        'summary':[],'scope_notes':['Synthetic fixture; no project data.']}
    view=output/'synthetic-view.json';view.write_text(json.dumps(data),encoding='utf8')
    html=output/'synthetic-review.html'
    build(view,html,feedback_url='',feedback_backup_url='')
    return html


def main():
    p=argparse.ArgumentParser()
    source_group=p.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--html',type=Path)
    source_group.add_argument('--synthetic-root',type=Path,help='Generate a synthetic fixture using this skills root')
    p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--browser',type=Path)
    p.add_argument('--physical-id')
    a=p.parse_args();a.output_root=a.output_root.resolve();a.output_root.mkdir(parents=True,exist_ok=False)
    source=(a.html if a.html else synthetic_page(a.synthetic_root.resolve(),a.output_root)).resolve()
    text=source.read_text(encoding='utf8');data=payload(text)
    seed=json.loads(re.search(r'id="feedback-seed"[^>]*>(.*?)</script>',text,re.S)[1])
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    records=data['objects']; families={}
    for o in records:families.setdefault((o['professional'],o['category'],o['id']),[]).append(o)
    candidates=[v for k,v in families.items() if len(v)>1 and (not a.physical_id or k[2]==a.physical_id)]
    assert candidates,'Expected a multi-record physical surface fixture'
    targets=candidates[0];requests=[];errors=[];checked=[]
    def select(page,obj):
        page.locator('#mode-objects').click()
        page.locator('#professional').select_option('全部')
        page.locator('#search').fill(obj['catalog_key'])
        rows=page.locator('#list .item');key='record|'+obj['catalog_key']
        assert key in rows.evaluate_all('els=>els.map(e=>e.dataset.key)'),obj['catalog_key']
        rows.evaluate_all('(els,k)=>els.find(e=>e.dataset.key===k).click()',key)
        active=page.locator('#list .item.active');assert active.count()==1
        assert active.get_attribute('data-key')==key
        assert obj['title'] in active.inner_text()
        assert obj['title'] in page.locator('#object-facts').inner_text()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,**({'executable_path':str(a.browser)} if a.browser else {}))
        def context():
            ctx=browser.new_context(viewport={'width':1600,'height':1000},accept_downloads=True,service_workers='block')
            ctx.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:','ws:','wss:')) else None)
            ctx.route(re.compile(r'^(https?|wss?)://'),lambda r:r.abort())
            page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.set_default_timeout(10000)
            return ctx,page
        ctx,page=context();page.goto(source.as_uri());page.locator('#loading').wait_for(state='hidden')
        page.locator('#mode-objects').click();page.locator('#professional').select_option('全部');page.locator('#search').fill('')
        catalog=[]
        while True:
            catalog+=page.locator('#list .item').evaluate_all('els=>els.map(e=>e.dataset.key)')
            if page.locator('#list-next').is_disabled():break
            page.locator('#list-next').click()
        assert catalog==['record|'+o['catalog_key'] for o in records]
        for o in records:select(page,o);checked.append(o['catalog_key'])
        for o in targets:
            g=next(g for g in data['groups'] if o['catalog_key'] in g['member_keys'])
            page.locator('#mode-groups').click();page.locator('#professional').select_option('全部');page.locator('#search').fill(g['id'])
            page.locator('#list .item').evaluate_all('(els,k)=>els.find(e=>e.dataset.key===k).click()','group|'+g['id'])
            while True:
                rows=page.locator('#member-list .member-row')
                if o['catalog_key'] in rows.evaluate_all('els=>els.map(e=>e.dataset.key)'):
                    rows.evaluate_all('(els,k)=>els.find(e=>e.dataset.key===k).querySelector("button").click()',o['catalog_key']);break
                page.locator('#members-next').click()
            page.locator('#member-single').click()
            active=page.locator('#list .item.active');assert active.count()==1
            assert active.get_attribute('data-key')=='record|'+o['catalog_key']
            assert o['title'] in active.inner_text() and o['title'] in page.locator('#object-facts').inner_text()
            page.locator('#review-result').fill('Browser regression: '+o['catalog_key'])
            page.locator('#review-reason').fill('Distinct material record on one physical surface; test copy only.')
            page.locator('#save-review').click()
        page.screenshot(path=str(a.output_root/'selected-layer.png'))
        with page.expect_download() as download:page.locator('#save-html').click()
        saved=a.output_root/'saved-review.html';download.value.save_as(saved)
        saved_text=saved.read_text(encoding='utf8');assert payload(saved_text)==data
        new_seed=json.loads(re.search(r'id="feedback-seed"[^>]*>(.*?)</script>',saved_text,re.S)[1])
        changed={o.get('annotation_key') or o['catalog_key'] for o in targets}
        for k,v in seed['annotations'].items():
            if k not in changed:assert new_seed['annotations'][k]==v
            else:assert any(h['confirmed_result']==v['confirmed_result'] for h in new_seed['annotations'][k]['history'])
        ctx.close();ctx,page=context();page.goto(saved.as_uri());page.locator('#loading').wait_for(state='hidden')
        for o in targets:
            select(page,o)
            assert page.locator('#review-result').input_value()=='Browser regression: '+o['catalog_key']
            note=new_seed['annotations'][o.get('annotation_key') or o['catalog_key']]
            assert note['object_snapshot']['catalog_key']==o['catalog_key']
        page.screenshot(path=str(a.output_root/'restored-layer.png'));ctx.close();browser.close()
    assert not requests and not errors
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    result={'status':'PASS','input_sha256':before,'records_directly_discoverable_and_selected':len(checked),'same_surface_layers_checked':len(targets),'group_to_single_identity':'PASS','saved_and_restored_identity':'PASS','old_annotations_preserved_or_retained_in_history':len(seed['annotations']),'payload_unchanged':True,'network_requests':requests,'page_errors':errors}
    (a.output_root/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (a.output_root/'saved-feedback.json').write_text(json.dumps(new_seed,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
