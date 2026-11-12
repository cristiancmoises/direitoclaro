/* Local Chromium integration checks. Requires Node, Playwright, axe-core and Python dependencies. */
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright');
const axe = require('axe-core');

const repo = path.resolve(__dirname, '..');
const python = process.env.DIREITO_TEST_PYTHON || 'python3';
const dbDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'direito-http-e2e-'));
const report = { browser: '', axe: axe.version, http: {}, viewports: {}, failures: [] };
const check = (ok, message) => { if (!ok) report.failures.push(message); };
const statusTags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

function createSnapshot() {
  const script = `
from pathlib import Path
import sys
from app.captures import stage_capture
from app.public_snapshot import export_snapshot
from app.storage import connect, setup
directory = Path(sys.argv[1])
private = directory / 'private.sqlite3'
with connect(private) as con:
    setup(con)
raw = b'<html><body><p>Art. 99123. SYNTHETIC_PRIVATE_HTTP_E2E_MARKER never belongs in public search.</p></body></html>'
stage_capture('consumidor', raw, private, minimum=1)
export_snapshot(private, directory / 'public.sqlite3', acknowledge_nonproduction=True)
`;
  execFileSync(python, ['-c', script, dbDirectory], {
    cwd: repo,
    env: { ...process.env, DIREITO_PUBLIC_READ_ONLY: '0', PYTHONDONTWRITEBYTECODE: '1' },
  });
}

async function availablePort() {
  const listener = net.createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const { port } = listener.address();
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function waitForServer(base, server) {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      if ((await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(1000) })).ok) return;
    } catch { /* Await Uvicorn startup. */ }
    if (server.exitCode !== null) break;
    await delay(50);
  }
  throw new Error('Loopback Uvicorn did not start');
}

async function checkHttp(base) {
  const get = (route, options) => fetch(base + route, { signal: AbortSignal.timeout(5000), ...options });
  check((await get('/api/ready').then(response => response.json())).ready === true, 'Read-only snapshot is not ready');
  const search = await get('/api/search?q=arrependimento');
  check(search.ok && (await search.json()).results.some(row => row.article_key === '49'), 'HTTP search misses Article 49');
  check(search.headers.get('cache-control') === 'no-store', 'HTTP API response lacks no-store');
  check(search.headers.get('content-security-policy')?.includes("script-src 'self'"), 'HTTP API response lacks CSP');
  check(search.headers.get('x-content-type-options') === 'nosniff', 'HTTP API response lacks nosniff');
  check((await get('/api/status').then(response => response.json())).pending_captures === 0, 'Pending capture visible in public status');
  check((await get('/api/articles/consumidor/99123')).status === 404, 'Pending article reachable over HTTP');
  check((await get('/api/articles/consumidor/49/history').then(response => response.json())).versions.length === 0, 'Seed article history is unexpected');
  check((await get('/api/articles/consumidor/49/diff?older=1&newer=2')).status === 404, 'Missing history versions were accepted');
  check([404, 405].includes((await get('/api/captures', { method: 'POST' })).status), 'Private write route is reachable');
  check((await get('/assets/%2e%2e%2fapp/main.py')).status === 404, 'Static root traversal accepted');
  check((await get('/data/private.sqlite3')).status === 404, 'Private SQLite reachable over HTTP');
  check((await get('/api/search?q=' + 'a'.repeat(161))).status === 422, 'Overlong search was accepted');
  for (const route of ['/api/laws', '/api/search?q=garantia', '/api/discover?q=garantia', '/api/coverage-progress']) {
    const durations = [];
    let bytes;
    for (let attempt = 0; attempt < 20; attempt++) {
      const start = performance.now();
      const response = await get(route);
      bytes = (await response.arrayBuffer()).byteLength;
      check(response.ok, `HTTP timing request failed for ${route}`);
      durations.push(performance.now() - start);
    }
    durations.sort((a, b) => a - b);
    report.http[route] = { samples: durations.length, p50_ms: durations[9], p95_ms: durations[18], bytes };
  }
}

async function auditAxe(page, label) {
  await page.evaluate(axe.source);
  const result = await page.evaluate(tags => axe.run(document, { runOnly: { type: 'tag', values: tags } }), statusTags);
  const violations = result.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }));
  for (const violation of violations) check(false, `${label}: axe ${violation.id} (${violation.targets.length} nodes)`);
  return { violations, needs_manual_review: result.incomplete.map(({ id, nodes }) => ({ id, nodes: nodes.length })) };
}

async function contrast(page, selector, foreground, background, backgroundSelector = selector) {
  return page.evaluate(({ selector, foreground, background, backgroundSelector }) => {
    const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number)
      .map(x => x / 255).map(x => x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
      .reduce((value, channel, index) => value + channel * [0.2126, 0.7152, 0.0722][index], 0);
    const fg = luminance(getComputedStyle(document.querySelector(selector))[foreground]);
    const bg = luminance(getComputedStyle(document.querySelector(backgroundSelector))[background]);
    return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
  }, { selector, foreground, background, backgroundSelector });
}

async function viewportChecks(browser, base, width, height, deviceScaleFactor = 1) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor, reducedMotion: 'reduce' });
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push(request.url()));
    await page.addInitScript(() => {
      window.auditPaint = {};
      new PerformanceObserver(list => { window.auditPaint.lcp_ms = list.getEntries().at(-1).startTime; })
        .observe({ type: 'largest-contentful-paint', buffered: true });
    });
    const response = await page.goto(base, { waitUntil: 'networkidle' });
    await page.locator('.law-card').first().waitFor();
    const evidence = report.viewports[width] = { height, deviceScaleFactor, pageErrors: errors, themes: {} };
    evidence.navigation = await page.evaluate(() => ({ ...window.auditPaint, load_ms: performance.getEntriesByType('navigation')[0].loadEventEnd }));
    check(response.ok(), `${width}: homepage failed`);
    check(await page.locator('.hero-flag').evaluate(img => img.complete && img.naturalWidth > 0), `${width}: flag image failed to load`);
    check((await page.locator('footer').innerText()).includes('Cristian Cezar Moisés'), `${width}: footer attribution changed`);
    check(await page.locator('footer a', { hasText: 'Código Fonte' }).getAttribute('href') === 'https://github.com/cristiancmoises/direitoclaro', `${width}: source footer link changed`);
    await page.keyboard.press('Tab');
    check(await page.locator('.skip-link').evaluate(el => el === document.activeElement), `${width}: keyboard skip link is unreachable`);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Tab');
    check(await page.locator('#search-input').evaluate(el => el === document.activeElement), `${width}: skip link did not bypass navigation`);
    await page.locator('#theme-toggle').focus();
    await page.keyboard.press('/');
    check(await page.locator('#theme-toggle').evaluate(el => el === document.activeElement), `${width}: plain slash steals focus`);
    await page.keyboard.press('Alt+/');
    check(await page.locator('#search-input').evaluate(el => el === document.activeElement), `${width}: Alt+/ did not focus search`);
    for (const theme of ['dark', 'light']) {
      if (theme === 'light') {
        await page.locator('#theme-toggle').click();
        await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(255, 255, 255)');
      }
      const checks = evidence.themes[theme] = await page.evaluate(() => ({
        background: getComputedStyle(document.body).backgroundColor,
        search_radius: getComputedStyle(document.querySelector('.search-box')).borderRadius,
        select_radius: getComputedStyle(document.querySelector('#search-law')).borderRadius,
        overflowing: document.documentElement.scrollWidth > innerWidth,
        overflow_elements: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(el => el.id || el.className),
      }));
      check(checks.background === (theme === 'dark' ? 'rgb(0, 0, 0)' : 'rgb(255, 255, 255)'), `${width}/${theme}: background changed`);
      check(checks.search_radius === '24px' && checks.select_radius === '14px', `${width}/${theme}: rounded controls changed`);
      check(!checks.overflowing, `${width}/${theme}: page scrolls horizontally`);
      checks.axe_home = await auditAxe(page, `${width}/${theme}/home`);
      await page.locator('.capture-notice a').focus();
      await page.keyboard.press('Tab');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      check(await page.locator('.topic-card').first().evaluate(el => el === document.activeElement && getComputedStyle(el).outlineStyle !== 'none'), `${width}/${theme}: keyboard focus ring is missing`);
      checks.focus_contrast = await contrast(page, '.topic-card', 'outlineColor', 'backgroundColor', 'body');
      check(checks.focus_contrast >= 3, `${width}/${theme}: focus ring contrast ${checks.focus_contrast.toFixed(2)}:1`);
      const input = page.locator('#search-input');
      await input.fill('arrependimento');
      await input.press('Enter');
      const result = page.locator('.article-result').first();
      await result.waitFor();
      const liveStatus = page.locator('#resultados').getByRole('status');
      check(await liveStatus.count() > 0 && /1 artigo/.test(await liveStatus.first().textContent()), `${width}/${theme}: search results are not announced`);
      checks.axe_results = await auditAxe(page, `${width}/${theme}/results`);
      await result.focus();
      await page.keyboard.press('Enter');
      const dialog = page.locator('#article-dialog');
      await dialog.waitFor();
      check((await page.locator('#dialog-title').innerText()).includes('49'), `${width}/${theme}: wrong article opened`);
      const panelBadge = '.reader-explained .panel-badge';
      checks.badge_contrast = await contrast(page, panelBadge, 'color', 'backgroundColor');
      checks.source_contrast = await contrast(page, '#reader-source', 'color', 'backgroundColor');
      check(checks.badge_contrast >= 4.5, `${width}/${theme}: reader badge contrast ${checks.badge_contrast.toFixed(2)}:1`);
      check(checks.source_contrast >= 4.5, `${width}/${theme}: reader source link contrast ${checks.source_contrast.toFixed(2)}:1`);
      checks.axe_reader = await auditAxe(page, `${width}/${theme}/reader`);
      check(await page.locator('.reader-scroll').evaluate(el => el.scrollWidth <= el.clientWidth), `${width}/${theme}: reader scrolls horizontally`);
      await page.locator('[data-reading-view=simple]').click();
      check(!(await page.locator('.reader-original').isVisible()), `${width}/${theme}: simple reader does not hide original panel`);
      await page.locator('#history-trigger').click();
      await page.locator('.history-note').waitFor();
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      check(await result.evaluate(el => el === document.activeElement), `${width}/${theme}: closing reader did not return focus`);
      await page.locator('#clear-results').click();
      check(await input.inputValue() === '', `${width}/${theme}: clearing results left a stale query`);
    }
    await page.reload({ waitUntil: 'domcontentloaded' });
    check(await page.locator('html').getAttribute('data-theme') === 'light', `${width}: theme preference was not retained`);
    await page.goto(`${base}/?lei=consumidor&art=49`, { waitUntil: 'domcontentloaded' });
    await page.locator('#article-dialog').waitFor();
    check((await page.locator('#dialog-title').innerText()).includes('49'), `${width}: shared URL did not open the requested article`);
    if (width === 1440) {
      await page.keyboard.press('Escape');
      await context.setOffline(true);
      const input = page.locator('#search-input');
      await input.fill('garantia');
      await input.press('Enter');
      await page.locator('.results-empty-small').waitFor();
      const liveStatus = page.locator('#resultados').getByRole('status');
      check((await liveStatus.textContent()).includes('Não foi possível consultar os dados'), 'Offline search error is not announced in Portuguese');
      await context.setOffline(false);
    }
    check(errors.length === 0, `${width}: browser JavaScript errors: ${errors.join(', ')}`);
    check(requests.every(url => new URL(url).origin === base), `${width}: browser contacted a non-loopback origin`);
  } finally {
    await context.close();
  }
}

(async () => {
  let server;
  let browser;
  try {
    createSnapshot();
    const port = await availablePort();
    const base = `http://127.0.0.1:${port}`;
    server = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log', '--log-level', 'warning'], {
      cwd: repo,
      env: { ...process.env, DIREITO_DATABASE: path.join(dbDirectory, 'public.sqlite3'), DIREITO_PUBLIC_READ_ONLY: '1', PYTHONDONTWRITEBYTECODE: '1' },
      stdio: ['ignore', 'ignore', 'inherit'],
    });
    await waitForServer(base, server);
    await checkHttp(base);
    browser = await chromium.launch({
      executablePath: process.env.DIREITO_TEST_CHROMIUM || undefined,
      headless: true,
    });
    report.browser = browser.version();
    for (const viewport of [[1440, 900], [390, 844], [320, 900], [720, 450, 2]]) await viewportChecks(browser, base, ...viewport);
    fs.renameSync(path.join(dbDirectory, 'public.sqlite3'), path.join(dbDirectory, 'withdrawn.sqlite3'));
    check((await fetch(base + '/api/ready')).status === 503, 'Readiness still passes after the snapshot disappears');
    check((await fetch(base + '/api/health')).ok, 'Snapshot failure unexpectedly stops liveness');
  } catch (error) {
    report.failures.push(error.stack);
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      const stopped = once(server, 'exit');
      server.kill('SIGTERM');
      await stopped;
    }
    fs.rmSync(dbDirectory, { recursive: true, force: true });
    console.log(JSON.stringify(report, null, 2));
    if (report.failures.length) process.exitCode = 1;
  }
})();
