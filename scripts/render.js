// 用 headless Chromium 渲染頁面，輸出 {status, html}；給 wzj/probe.py 呼叫
let playwright;
try { playwright = require("playwright"); } catch { playwright = require("/opt/node22/lib/node_modules/playwright"); }

(async () => {
  const url = process.argv[2];
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
  const browser = await playwright.chromium.launch(proxy ? { proxy: { server: proxy } } : {});
  try {
    const page = await browser.newPage({
      locale: "zh-TW",
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    });
    const res = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    // 價格常在下拉或延遲載入區塊，捲到底觸發 lazy load
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
    await page.waitForTimeout(1500);
    process.stdout.write(JSON.stringify({ status: res ? res.status() : null, html: await page.content() }));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(String(e)); process.exit(1); });
