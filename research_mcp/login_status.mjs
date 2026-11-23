const rows = [];
const task = await taskSpace(input.space);
for (const entry of input.platforms) {
  let page;
  const start = Date.now();
  let status = 'unknown';
  try {
    page = await task.newPage();
    await page.goto(entry.url, {timeout:12000});
    try { await page.waitForFunction(({yes}) => /登录|驗證|验证|注册用户/.test(document.body?.innerText || '') || (yes && document.querySelector(yes)), {yes:entry.yes}, {timeout:3000}); } catch {}
    status = await page.evaluate(({yes}) => {
      const text = document.body?.innerText || '';
      if (/注册用户.*威望|威望.*方可使用/.test(text)) return 'restricted';
      if (/安全验证|请完成验证|访问受限|访问过于频繁/.test(text)) return 'blocked';
      if (/passport\.|login\./.test(location.hostname) || /\/login(?:[/?]|$)/.test(location.pathname)) return 'expired';
      // A public search result is NOT proof of a signed-in account.
      const marker = yes && document.querySelector(yes);
      if (marker && marker.getBoundingClientRect().width > 0 && marker.getBoundingClientRect().height > 0) return 'logged_in';
      if (/请先登录|登录后查看|扫码登录|扫描二维码登录/.test(text)) return 'expired';
      return 'unknown';
    }, {yes:entry.yes});
  } catch { status = 'unknown'; }
  finally { if (page) {try {await page.close();} catch {status='unknown';}} }
  rows.push({channel:entry.channel,status,seconds:(Date.now()-start)/1000});
}
console.log('RESEARCH_LOGIN_JSON:'+JSON.stringify(rows));
