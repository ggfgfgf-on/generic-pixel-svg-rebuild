// 把本地 HTML 按精确像素尺寸截成 PNG：node render.js <html> <out.png> [w h] [urlSuffix]
const fs = require('fs'), path = require('path'), glob = [];

function* roots() {
  const starts = [process.cwd(), process.env.DSH_HOME, process.env.DSH_CHECKOUT, process.env.DSH_ROOT]
    .filter(Boolean);
  for (const s of starts) {
    let dir = path.resolve(s);
    for (let i = 0; i < 7; i++) {
      yield dir;
      const up = path.dirname(dir); if (up === dir) break; dir = up;
    }
  }
}

function loadPw() {
  const cands = [];
  if (process.env.PW_CORE) cands.push(process.env.PW_CORE);
  for (const dir of roots()) {
    cands.push(path.join(dir, 'node_modules', 'playwright-core'));
    const pnpm = path.join(dir, 'node_modules', '.pnpm');
    if (fs.existsSync(pnpm)) {
      for (const d of fs.readdirSync(pnpm)) {
        if (d.startsWith('playwright-core@')) cands.push(path.join(pnpm, d, 'node_modules', 'playwright-core'));
      }
    }
  }
  for (const c of cands) {
    try { const pw = require(c); if (pw && pw.chromium) return pw; } catch {}
  }
  throw new Error('playwright-core not found; set PW_CORE to its path');
}

(async () => {
  const [html, out, wArg, hArg, suffix] = process.argv.slice(2);
  if (!html || !out) { console.error('usage: node render.js <html> <out.png> [w h] [urlSuffix]'); process.exit(2); }
  const w = +(wArg || 1280), h = +(hArg || 800);
  const pw = loadPw();
  const browser = await pw.chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const url = 'file:///' + path.resolve(html).replace(/\\/g, '/') + (suffix || '');
  await page.goto(url, { waitUntil: 'load', timeout: 120000 });
  await page.waitForTimeout(120);
  await page.screenshot({ path: out, animations: 'disabled' });
  await browser.close();
  console.log(JSON.stringify({ out, w, h, url }));
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
