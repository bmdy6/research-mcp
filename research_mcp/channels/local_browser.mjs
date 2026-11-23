// Runs only inside ego-browser nodejs. DOM reads only; no fetch/cookies/storage.
const emit = (status, items = []) => console.log("RESEARCH_BROWSER_JSON:" + JSON.stringify({status, items}));
try {
  const entry = (await listTaskSpaces()).find(s => s.id === input.space);
  if (!entry || entry.ownership !== "agent") {
    emit("auth_required");
  } else {
    const task = await taskSpace(input.space);
    const pages = await task.pages();
    const page = pages.find(p => p.label === input.page);
    if (!page) {
      emit("startup_failed");
    } else {
      await page.goto("https://search.jd.com/Search?keyword=" + encodeURIComponent(input.query), {timeout: 18000});
      // Wait for public result DOM or a visible barrier, never solve a challenge.
      try {
        await page.waitForFunction(() =>
          document.querySelector('a[href*="chat.jd.com/index.action"][href*="pid="]') ||
          /passport\.jd\.com/.test(location.hostname) ||
          /请完成验证|安全验证|拖动滑块|访问受限/.test(document.body.innerText), undefined, {timeout: 12000});
      } catch {}
      const result = await page.evaluate((limit) => {
        const text = document.body.innerText;
        if (/请完成验证|安全验证|拖动滑块|访问受限/.test(text)) return {status: "blocked"};
        if (/passport\.jd\.com/.test(location.hostname)) return {status: "auth_required"};
        if (location.hostname !== "search.jd.com") return {status: "blocked"};
        const items = [], seen = new Set();
        for (const link of document.querySelectorAll('a[href*="chat.jd.com/index.action"][href*="pid="]')) {
          const id = new URL(link.href).searchParams.get("pid");
          if (!/^\d+$/.test(id) || seen.has(id)) continue;
          // New JD DOM has no product anchor; its customer-service link exposes
          // the same product id. Construct the canonical public product URL.
          const card = link.closest('[class*="_info_"]');
          if (!card) continue;
          const title = card.querySelector('[class*="goods_title"] [title]')?.getAttribute("title");
          const box = card.querySelector('[class*="_container_"]:has([class*="_price_"])');
          // When a subsidy/coupon price is prominent, use the plain grey price.
          const conditional = /国补|补贴|券后|到手/.test(box?.innerText || "");
          const priceNode = conditional ? box?.querySelector('[class*="_gray_"]') : box?.querySelector('[class*="_price_"]');
          const price = priceNode?.innerText.replace(/[¥￥\s]/g, "");
          const author = card.querySelector('[class*="_name_"]')?.innerText || "";
          if (!title || !/^\d+(?:\.\d{1,2})?$/.test(price || "")) continue;
          seen.add(id);
          items.push({title, url: "https://item.jd.com/" + id + ".html", price, author});
          if (items.length >= limit) break;
        }
        return {status: items.length ? "ok" : /抱歉.*没有找到|未找到相关商品|没有搜索到相关/.test(text) ? "empty" : "parse_error", items};
      }, input.limit);
      emit(result.status, result.items);
    }
  }
} catch (error) {
  const message = String(error);
  emit(/permission|control|hand.?off|inactive|unassigned/i.test(message) ? "auth_required" :
       /timed.?out|timeout/i.test(message) ? "timeout" : "startup_failed");
}
