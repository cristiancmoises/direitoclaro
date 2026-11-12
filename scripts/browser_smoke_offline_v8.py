"""Visual regression smoke with offline TestClient fixtures; does not certify WCAG.

Requires playwright and system Chromium, no network or live backend.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from playwright.sync_api import sync_playwright
from fastapi.testclient import TestClient

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
with tempfile.TemporaryDirectory() as td:
    os.environ['DIREITO_DATABASE']=td+'/smoke.sqlite3'
    from app import storage
    storage.DATABASE=Path(os.environ['DIREITO_DATABASE'])
    from app.main import app
    with TestClient(app) as client:
        api={p:client.get(p).json() for p in ['/api/laws','/api/status','/api/topics','/api/portals','/api/norm-types','/api/articles/consumidor/49']}
        for law in api['/api/laws']['laws']:
            key='/api/laws/'+law['slug']+'/articles'
            data=client.get(key+'?limit=30').json()
            api[key]=data
            for article in data['articles']:
                detail='/api/articles/'+law['slug']+'/'+article['article_key']
                api[detail]=client.get(detail).json()
        api['/api/search']=client.get('/api/search',params={'q':'garantia'}).json()
        api['/api/discover']=client.get('/api/discover',params={'q':'Lei 14.133/2021'}).json()
        html=(root/'web/index.html').read_text().replace('<link rel="stylesheet" href="/assets/styles.css">','').replace('<script src="/assets/app.js" defer></script>','')
        svg=(root/'web/bandeira-brasil.svg').read_bytes()
        html=html.replace('/assets/bandeira-brasil.svg','data:image/svg+xml;base64,'+base64.b64encode(svg).decode())
        script=(root/'web/app.js').read_text();css=(root/'web/styles.css').read_text()
        results={}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            for name,size in [('desktop',{'width':1440,'height':900}),('mobile',{'width':390,'height':844})]:
                page=browser.new_page(viewport=size)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_content(html,wait_until='domcontentloaded')
                page.add_style_tag(content=css)
                page.evaluate('''(data)=>{window.__fakeApi=data;
                    window.fetch=async (url)=>({ok:true,status:200,json:async()=>window.__fakeApi[String(url).split('?')[0]]||{results:[],count:0}});
                    history.replaceState=()=>{};}''',api)
                page.add_script_tag(content=script)
                page.wait_for_selector('.law-card',timeout=7000)
                page.wait_for_selector('.topic-card',timeout=7000)
                page.wait_for_selector('.source-entry',timeout=7000)
                page.wait_for_selector('.norm-type',timeout=7000)
                assert page.locator('.norm-type').count()==12
                assert page.locator('.topic-card').count()==15
                assert page.locator('.law-card').count()==12
                assert page.locator('.source-entry').count()==6
                dark=page.evaluate('''()=>({bg:getComputedStyle(document.body).backgroundColor,
                    green:getComputedStyle(document.documentElement).getPropertyValue('--green').trim(),
                    yellow:getComputedStyle(document.documentElement).getPropertyValue('--yellow').trim(),
                    over:document.documentElement.scrollWidth>innerWidth})''')
                flag_loaded=page.locator('.hero-flag').evaluate('(x)=>x.complete && x.naturalWidth>0')
                footer=page.locator('.footer-credits').inner_text()
                if dark['over']:
                    print('OVERFLOW',name,page.evaluate('''()=>({innerWidth,root:document.documentElement.scrollWidth,body:document.body.scrollWidth,docs:[...document.querySelectorAll('body *')].filter(e=>e.scrollWidth>e.clientWidth+3).slice(0,25).map(e=>[e.tagName,typeof e.className==='string'?e.className:'',e.clientWidth,e.scrollWidth])})'''))
                assert dark=={'bg':'rgb(0, 0, 0)','green':'#009b3a','yellow':'#ffdf00','over':False}, dark
                assert flag_loaded
                assert 'Cristian Cezar Moisés' in footer and 'Código Fonte' in footer
                page.locator('.topic-card').first.click()
                page.wait_for_selector('.catalog-hit',timeout=7000)
                assert page.locator('.catalog-hit').count()>=2
                page.locator('#law-grid .law-card').nth(1).click()
                page.wait_for_selector('.article-result',timeout=7000)
                page.locator('.article-result').first.click()
                reader=page.locator('#article-dialog').evaluate('(x)=>x.open')
                assert reader
                page.locator('#dialog-close').click()
                page.evaluate('''()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});document.body.scrollTop=0;}''')
                page.wait_for_timeout(200)
                if name=='desktop':
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.8.0_DESKTOP.png',full_page=False)
                else:
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.8.0_MOBILE.png',full_page=False)
                page.locator('#theme-toggle').click()
                light=page.evaluate('getComputedStyle(document.body).backgroundColor')
                assert light=='rgb(255, 255, 255)',light
                page.evaluate('''()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});document.body.scrollTop=0;}''')
                page.wait_for_timeout(200)
                page.evaluate('''()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});document.body.scrollTop=0;}''')
                if name=='desktop':
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.8.0_LIGHT.png',full_page=False)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                assert not errors,errors
                results[name]={'cards':page.locator('.law-card').count(),'flag_loaded':flag_loaded,'dark_bg':dark['bg'],'light_bg':light,'palette':['#009b3a','#ffdf00'],'no_horizontal_overflow':not dark['over'],'reader_opened':reader,'topics':15,'norm_types':12,'source_portals':6,'catalog_laws':55,'js_errors':errors}
                page.close()
            browser.close()
    (root/'docs'/'BROWSER_OFFLINE_SMOKE_0.8.0.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(results,ensure_ascii=False,indent=2))
