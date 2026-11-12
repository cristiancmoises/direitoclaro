"""Optional offline browser smoke using TestClient API snapshots; NOT HTTP E2E.

Requires Playwright and a system Chromium binary. May need environment-specific
browser executable path edits. No external network or production contact.
"""
from pathlib import Path
import json,os,tempfile
from playwright.sync_api import sync_playwright
from fastapi.testclient import TestClient
import sys
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
        script=(root/'web/app.js').read_text()
        css=(root/'web/styles.css').read_text()
        results={}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            for name,size in [('desktop',{'width':1366,'height':900}),('mobile',{'width':390,'height':844})]:
                page=browser.new_page(viewport=size)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_content(html,wait_until='domcontentloaded')
                page.add_style_tag(content=css)
                page.evaluate('''(data)=>{window.__fakeApi=data;
                    window.fetch=async (url)=>({ok:true,status:200,json:async()=>window.__fakeApi[String(url).split('?')[0]]||{results:[],count:0}});
                    history.replaceState=()=>{};}''',api)
                page.add_script_tag(content=script)
                page.wait_for_selector('.law-card',timeout=5000)
                result={'cards':page.locator('.law-card').count(),
                        'horizontal_overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),
                        'keyboard_focus_search':None,'light_theme':None,'reader_opened':None}
                page.keyboard.press('/')
                result['keyboard_focus_search']=page.locator('#search-input').evaluate('(x)=>document.activeElement===x')
                page.locator('#law-grid .law-card').nth(1).click()
                page.wait_for_selector('.article-result',timeout=5000)
                result['reader_opened']=False
                count=page.locator('.article-result').count()
                result['article_results']=count
                if count:
                    page.locator('.article-result').first.click()
                    result['reader_opened']=page.locator('#article-dialog').evaluate('(x)=>x.open')
                    page.locator('#dialog-close').click()
                page.locator('#theme-toggle').click()
                result['light_theme']=page.locator('html').get_attribute('data-theme')=='light'
                page.locator('#search-input').fill('garantia')
                page.locator('#search-form button[type=submit]').first.click()
                page.wait_for_timeout(150)
                if name=='desktop':
                    page.locator('#theme-toggle').click()  # capture default dark theme
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.6.0_DESKTOP.png',full_page=True)
                else:
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.6.0_MOBILE.png',full_page=True)
                result['javascript_errors']=errors
                assert count and result['reader_opened'] and not errors and not result['horizontal_overflow']
                results[name]=result
                page.close()
            browser.close()
    (root/'docs'/'BROWSER_OFFLINE_SMOKE_0.6.0.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(results,ensure_ascii=False,indent=2))
