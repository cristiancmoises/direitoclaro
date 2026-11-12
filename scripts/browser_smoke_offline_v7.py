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
        api={p:client.get(p).json() for p in ['/api/laws','/api/status','/api/articles/consumidor/49']}
        for law in api['/api/laws']['laws']:
            key='/api/laws/'+law['slug']+'/articles'
            data=client.get(key+'?limit=30').json()
            api[key]=data
            for article in data['articles']:
                detail='/api/articles/'+law['slug']+'/'+article['article_key']
                api[detail]=client.get(detail).json()
        api['/api/search']=client.get('/api/search',params={'q':'garantia'}).json()
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
                dark=page.evaluate('''()=>({bg:getComputedStyle(document.body).backgroundColor,
                    green:getComputedStyle(document.documentElement).getPropertyValue('--green').trim(),
                    yellow:getComputedStyle(document.documentElement).getPropertyValue('--yellow').trim(),
                    over:document.documentElement.scrollWidth>innerWidth})''')
                flag_loaded=page.locator('.hero-flag').evaluate('(x)=>x.complete && x.naturalWidth>0')
                footer=page.locator('.footer-credits').inner_text()
                assert dark=={'bg':'rgb(0, 0, 0)','green':'#009b3a','yellow':'#ffdf00','over':False}, dark
                assert flag_loaded
                assert 'Cristian Cezar Moisés' in footer and 'Código Fonte' in footer
                page.locator('#law-grid .law-card').nth(1).click()
                page.wait_for_selector('.article-result',timeout=7000)
                page.locator('.article-result').first.click()
                reader=page.locator('#article-dialog').evaluate('(x)=>x.open')
                assert reader
                page.locator('#dialog-close').click()
                if name=='desktop':
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.7.0_DESKTOP.png',full_page=True)
                else:
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.7.0_MOBILE.png',full_page=True)
                page.locator('#theme-toggle').click()
                light=page.evaluate('getComputedStyle(document.body).backgroundColor')
                assert light=='rgb(255, 255, 255)',light
                if name=='desktop':
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.7.0_LIGHT.png',full_page=True)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                assert not errors,errors
                results[name]={'cards':page.locator('.law-card').count(),'flag_loaded':flag_loaded,'dark_bg':dark['bg'],'light_bg':light,'palette':['#009b3a','#ffdf00'],'no_horizontal_overflow':not dark['over'],'reader_opened':reader,'js_errors':errors}
                page.close()
            browser.close()
    (root/'docs'/'BROWSER_OFFLINE_SMOKE_0.7.0.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(results,ensure_ascii=False,indent=2))
