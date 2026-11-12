"""Visual regression smoke for Direito Claro v0.9.0."""
import base64, json, os, sys, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
with tempfile.TemporaryDirectory() as td:
    os.environ['DIREITO_DATABASE'] = td + '/smoke.sqlite3'
    from app import storage
    storage.DATABASE = Path(os.environ['DIREITO_DATABASE'])
    from app.main import app
    with TestClient(app) as client:
        api = {p: client.get(p).json() for p in ['/api/laws','/api/status','/api/topics','/api/portals','/api/norm-types','/api/articles/consumidor/49']}
        for law in api['/api/laws']['laws']:
            key = '/api/laws/' + law['slug'] + '/articles'
            data = client.get(key + '?limit=30').json()
            api[key] = data
            for article in data['articles']:
                detail = '/api/articles/' + law['slug'] + '/' + article['article_key']
                api[detail] = client.get(detail).json()
        api['/api/search'] = client.get('/api/search', params={'q':'garantia'}).json()
        api['/api/discover'] = client.get('/api/discover', params={'q':'Lei 14.133/2021'}).json()
        html = (root/'web/index.html').read_text().replace('<link rel="stylesheet" href="/assets/styles.css">','').replace('<script src="/assets/app.js" defer></script>','')
        svg = (root/'web/bandeira-brasil.svg').read_bytes()
        html = html.replace('/assets/bandeira-brasil.svg', 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode())
        script=(root/'web/app.js').read_text(); css=(root/'web/styles.css').read_text()
        results={}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            for name, size in [('desktop', {'width': 1440, 'height': 900}), ('mobile', {'width': 390, 'height': 844})]:
                page = browser.new_page(viewport=size)
                errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
                page.set_content(html, wait_until='domcontentloaded')
                page.add_style_tag(content=css)
                page.evaluate('''(data)=>{window.__fakeApi=data;
                    window.fetch=async (url)=>({ok:true,status:200,json:async()=>window.__fakeApi[String(url).split('?')[0]]||{results:[],count:0}});
                    history.replaceState=()=>{};}''', api)
                page.add_script_tag(content=script)
                page.wait_for_selector('.law-card', timeout=7000)
                page.wait_for_selector('.topic-card', timeout=7000)
                page.wait_for_selector('.source-entry', timeout=7000)
                page.wait_for_selector('.norm-type', timeout=7000)
                dark = page.evaluate('''()=>({bg:getComputedStyle(document.body).backgroundColor,
                    green:getComputedStyle(document.documentElement).getPropertyValue('--green').trim(),
                    yellow:getComputedStyle(document.documentElement).getPropertyValue('--yellow').trim(),
                    searchRadius:getComputedStyle(document.querySelector('.search-box')).borderRadius,
                    selectRadius:getComputedStyle(document.querySelector('.search-filters select')).borderRadius,
                    buttonText:document.querySelector('.search-submit').textContent.trim(),
                    over:document.documentElement.scrollWidth>innerWidth})''')
                assert dark['bg'] == 'rgb(0, 0, 0)'
                assert dark['green'] == '#009b3a'
                assert dark['yellow'] == '#ffdf00'
                assert dark['buttonText'] == 'Buscar'
                assert dark['searchRadius'] in {'24px','24px 24px 24px 24px'}
                assert dark['selectRadius'] in {'14px','14px 14px 14px 14px'}
                assert not dark['over']
                assert page.locator('.law-badge').count() >= 12
                assert page.locator('.footer-credits').inner_text().find('Cristian Cezar Moisés') >= 0
                page.locator('.topic-card').first.click()
                page.wait_for_selector('.catalog-hit', timeout=7000)
                page.locator('#law-grid .law-card').nth(1).click()
                page.wait_for_selector('.article-result', timeout=7000)
                page.locator('.article-result').first.click()
                assert page.locator('#article-dialog').evaluate('(x)=>x.open')
                page.locator('#dialog-close').click()
                page.evaluate('''()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});document.body.scrollTop=0;}''')
                page.wait_for_timeout(150)
                page.screenshot(path=f'/mnt/data/DIREITO_CLARO_V0.9.0_{name.upper()}.png', full_page=False)
                if name == 'desktop':
                    page.locator('#theme-toggle').click()
                    assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
                    page.evaluate('''()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});document.body.scrollTop=0;}''')
                    page.wait_for_timeout(150)
                    page.screenshot(path='/mnt/data/DIREITO_CLARO_V0.9.0_LIGHT.png', full_page=False)
                assert not errors, errors
                results[name] = dark
                page.close()
            browser.close()
    (root/'docs'/'BROWSER_OFFLINE_SMOKE_0.9.0.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(results, ensure_ascii=False, indent=2))
