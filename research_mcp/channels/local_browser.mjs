// Runs only inside ego-browser nodejs. DOM reads only; no fetch/cookies/storage.
let payload;
const emit = (status, items = []) => { payload = {status, items}; };
const platform = input.platform || "jd";
const q = encodeURIComponent(input.query);
const definition = input.definition || {};
const routes = {
  xianyu: ["https://www.goofish.com/search?q=" + q, 'a[class*="feeds-item-wrap"]', 'www.goofish.com'],
  weibo: ["https://s.weibo.com/weibo?q=" + q, '.card-wrap[action-type="feed_list_item"]', 's.weibo.com'],
  jd: ["https://search.jd.com/Search?keyword=" + q, 'a[href*="chat.jd.com/index.action"][href*="pid="]', 'search.jd.com'],
  taobao: ["https://s.taobao.com/search?q=" + q, 'a[class*="doubleCardWrapper"]', 's.taobao.com'],
  tmall: ["https://s.taobao.com/search?tab=mall&q=" + q, 'a[class*="doubleCardWrapper"]', 's.taobao.com'],
  smzdm: ["https://search.smzdm.com/?s=" + q, '.feed-deal-card__title-link, h3 a[href*="smzdm.com/p/"]', 'search.smzdm.com'],
  zhihu: ["https://www.zhihu.com/search?type=content&q=" + q, '.ContentItem-title a', 'www.zhihu.com'],
  xiaohongshu: ["https://www.xiaohongshu.com/search_result?keyword=" + q, 'section.note-item', 'www.xiaohongshu.com'],
  douyin: ["https://www.douyin.com/search/" + q, '[id^="waterfall_item_"]', 'www.douyin.com'],
  sogou_wechat: ["https://weixin.sogou.com/weixin?type=2&query=" + q, '.news-list h3 a', 'weixin.sogou.com'],
};
if (definition.search_url) routes[platform] = [definition.search_url.replace('{query}', q), definition.selector, new URL(definition.search_url).hostname];
let ownedPage, deadline;
let expired = false;
try {
  const entry = (await listTaskSpaces()).find(s => s.id === input.space);
  if (!entry || entry.ownership !== "agent") {
    emit("auth_required");
  } else {
    const task = await taskSpace(input.space);
    // Existing login tabs are never navigated or closed. Respect the SDK budget.
    for (let attempt = 0; !ownedPage; attempt++) {
      try { ownedPage = await task.newPage(); }
      catch (error) {
        if (!/Page budget reached/.test(String(error)) || attempt >= 30) throw error;
        await new Promise(resolve => setTimeout(resolve, 400));
      }
    }
    // Finish cleanup before Python's 45s subprocess safety timeout.
    deadline = setTimeout(() => { expired = true; ownedPage.close().catch(() => {}); }, 27000);
    const page = ownedPage;
    if (!page || !routes[platform]) {
      emit("startup_failed");
    } else {
      const [url, selector, host] = routes[platform];
      await page.goto(url, {timeout: 18000});
      let huxiuItems;
      if (platform === 'huxiu' || platform === 'maimai') {
        if (platform === 'huxiu') {
        await page.click('svg.search-btn');
        await page.fill('input[placeholder="搜索文章"]', input.query);
        await page.click('.input-icons .icon-search');
        await page.waitForSelector('.result-article__title', {timeout:8000});
        } else { await page.waitForSelector(definition.selector, {timeout:8000}); }
        const titles = await page.evaluate(selector => [...document.querySelectorAll(selector)].map(e=>e.innerText.trim()), platform === 'maimai' ? definition.selector : '.result-article__title');
        huxiuItems = [];
        for (let i=0; i<Math.min(input.limit,2,titles.length); i++) {
          const popup = page.waitForEvent('popup', {timeout:8000}).then(
            detail=>({detail}), error=>({error}));
          await page.click((platform === 'maimai' ? definition.selector : '.result-article__title')+' >> nth='+i);
          const opened = await popup;
          if (opened.error) throw opened.error;
          const detail = opened.detail;
          try {
            const pattern = platform === 'maimai' ? /https:\/\/maimai\.cn\/community\/feed-detail\/[0-9]+/ : /https:\/\/www\.huxiu\.com\/article\/[0-9]+\.html/;
            await detail.waitForURL(pattern, {timeout:8000});
            const u = new URL(await detail.url());
            if (definition.hosts.includes(u.hostname) && new RegExp('^(?:'+definition.path+')$').test(u.pathname)) huxiuItems.push({title:titles[i],url:'https://'+u.hostname+u.pathname});
          } finally { await detail.close(); }
        }
      }
      try {
        await page.waitForFunction(({selector, host}) =>
          document.querySelector(selector) || (document.body && (
            /请完成验证|安全验证|拖动.{0,8}滑块|请按住滑块|访问受限|请先登录|登录后查看|扫码登录|扫描二维码登录|没有找到|没有搜到/.test(document.body.innerText) ||
            (location.hostname !== host && /login|passport/.test(location.href)))),
          {selector, host}, {timeout: 12000});
      } catch {}
      const result = huxiuItems ? {status:huxiuItems.length ? 'ok':'parse_error',items:huxiuItems} : await page.evaluate(({limit, platform, host, definition}) => {
        const text = document.body?.innerText || "";
        if ((location.pathname || '').includes('/_____tmd_____/') || /请完成验证|安全验证|拖动.{0,8}滑块|请按住滑块|访问受限|访问过于频繁/.test(text)) return {status: "blocked"};
        if (/passport\.|login\./.test(location.hostname) || /请先登录|登录后查看|扫码登录|扫描二维码登录/.test(text)) return {status: "auth_required"};
        if (location.hostname !== host) return {status: "blocked"};
        const items = [], seen = new Set();
        const get = (e, s) => e?.querySelector(s)?.innerText?.trim() || "";
        const add = (item) => {
          if (!item.title?.trim() || !item.url || seen.has(item.url) || items.length >= limit) return;
          seen.add(item.url); items.push(item);
        };
        if (definition.search_url) {
          // Cards may link the same object via thumbnail, title and review count.
          // Preserve result order while preferring the descriptive title.
          const candidates = new Map();
          for (const a of document.querySelectorAll(definition.selector)) {
            let u; try {u = new URL(a.href);} catch {continue;}
            if (!definition.hosts.includes(u.hostname) || !new RegExp('^(?:'+(definition.dom_path || definition.path)+')$').test(u.pathname)) continue;
            if (platform === 'amazon') {
              const id = u.pathname.match(/\/dp\/([A-Z0-9]{10})(?:\/|$)/)?.[1];
              if (!id) continue;
              u = new URL('https://www.amazon.com/dp/'+id);
            }
            let url = 'https://'+u.hostname+u.pathname;
            if (definition.query_key) {
              const value = u.searchParams.get(definition.query_key);
              if (!value || (definition.query_pattern && !new RegExp('^(?:'+definition.query_pattern+')$').test(value))) continue;
              url += '?'+definition.query_key+'='+encodeURIComponent(value);
            }
            const body = platform === 'x' ? get(a.closest('article'), '[data-testid="tweetText"]') : '';
            if (platform === 'x' && !body) continue;
            const item = {title: platform === 'tieba' ? (a.closest('.thread-content-box')?.querySelector('.title')?.innerText || a.closest('.thread-content-box')?.innerText || '').slice(0,180) : body ? body.slice(0,180) : (definition.title_selector ? get(definition.title_container ? a.closest(definition.title_container) : a,definition.title_selector) : a.innerText.trim()), url, ...(body ? {summary:body.slice(0,1000)} : {})};
            if (!candidates.has(url) || candidates.get(url).title.length < item.title.length) candidates.set(url,item);
          }
          for (const item of candidates.values()) add(item);
        } else if (platform === "xianyu") {
          for (const card of document.querySelectorAll('a[class*="feeds-item-wrap"]')) {
            let u; try {u = new URL(card.href);} catch {continue;}
            const id = u.searchParams.get('id');
            if (u.hostname !== 'www.goofish.com' || u.pathname !== '/item' || !/^\d+$/.test(id || '')) continue;
            const title = card.querySelector('[class*="row1-wrap-title"]')?.getAttribute('title');
            const price = get(card, '[class*="price-wrap"]').replace(/[¥￥\s]/g, '');
            if (!/^\d+(?:\.\d{1,2})?$/.test(price)) continue;
            const want = get(card, '[class*="price-desc"]').match(/([0-9]+(?:\.[0-9]+)?万?)人想要/);
            add({title, url:'https://www.goofish.com/item?id='+id, price,
              price_note:'页面标价，非成交价', want_count:want?.[1] || '',
              region:get(card, '[class*="seller-text"]')});
          }
        } else if (platform === "weibo") {
          for (const card of document.querySelectorAll('.card-wrap[action-type="feed_list_item"]')) {
            // Filter labelled promotions, not posts merely discussing advertising.
            if (/(?:^|\s)广告(?:\s|$)/.test(card.innerText) || /广告/.test(get(card, '.card-top'))) continue;
            const link = card.querySelector('.from a');
            let u; try {u = new URL(link?.href);} catch {continue;}
            if (u.hostname !== 'weibo.com' || !/^\/\d+\/[a-zA-Z0-9]+$/.test(u.pathname)) continue;
            const body = get(card, '[node-type="feed_list_content_full"]') || get(card, '[node-type="feed_list_content"]');
            if (!body) continue;
            add({title:body.slice(0,180), summary:body.slice(0,1000),
              url:'https://weibo.com'+u.pathname, author:get(card, '.name')});
          }
        } else if (platform === "jd") {
          for (const link of document.querySelectorAll('a[href*="chat.jd.com/index.action"][href*="pid="]')) {
            const id = new URL(link.href).searchParams.get("pid");
            if (!/^\d+$/.test(id)) continue;
            // New JD cards expose the public product ID in their service link.
            const card = link.closest('[class*="_info_"]');
            if (!card) continue;
            const title = card.querySelector('[class*="goods_title"] [title]')?.getAttribute("title");
            const box = card.querySelector('[class*="_container_"]:has([class*="_price_"])');
            const conditional = /国补|补贴|券后|到手/.test(box?.innerText || "");
            const priceNode = conditional ? box?.querySelector('[class*="_gray_"]') : box?.querySelector('[class*="_price_"]');
            const price = priceNode?.innerText.replace(/[¥￥\s]/g, "");
            if (!/^\d+(?:\.\d{1,2})?$/.test(price || "")) continue;
            add({title, url: "https://item.jd.com/" + id + ".html", price, author: get(card, '[class*="_name_"]')});
          }
        } else if (platform === "taobao" || platform === "tmall") {
          // Ignore ad redirectors; never return tracking URLs or guess their IDs.
          for (const card of document.querySelectorAll('a[class*="doubleCardWrapper"]')) {
            let u; try {u = new URL(card.href);} catch {continue;}
            const id = u.searchParams.get("id");
            if (!['item.taobao.com','detail.tmall.com'].includes(u.hostname) || !/^\d+$/.test(id || "") ||
                (platform === "tmall" && u.hostname !== "detail.tmall.com")) continue;
            const title = card.querySelector('[class*="title--"][title]')?.getAttribute('title');
            const price = get(card, '[class*="innerPriceWrapper"]').replace(/[¥￥\s]/g, "");
            const label = get(card, '[class*="priceWrapper"]').match(/国补后|补贴后|补贴价|优惠后|券后|到手价/)?.[0];
            const item = {title, url: "https://"+u.hostname+"/item.htm?id="+id, author: get(card, '[class*="shopNameText"]')};
            if (/^\d+(?:\.\d{1,2})?$/.test(price)) Object.assign(item, {price,
              price_note: label ? "页面展示价（"+label+"），条件与结算价请回原页核对" : "页面展示价，优惠条件与结算价请回原页核对"});
            add(item);
          }
        } else if (platform === "smzdm") {
          for (const a of document.querySelectorAll('.feed-deal-card__title-link, h3 a[href*="smzdm.com/p/"]')) {
            const u = new URL(a.href);
            if (!['www.smzdm.com','post.smzdm.com'].includes(u.hostname) || !/^\/p\/[a-zA-Z0-9]+\/$/.test(u.pathname)) continue;
            add({title:a.innerText.trim(),url:"https://"+u.hostname+u.pathname});
          }
        } else if (platform === "zhihu") {
          for (const a of document.querySelectorAll('.ContentItem-title a')) {
            const u = new URL(a.href);
            if (!['www.zhihu.com','zhuanlan.zhihu.com'].includes(u.hostname) || !/^\/(?:question\/\d+(?:\/answer\/\d+)?|p\/\d+)$/.test(u.pathname)) continue;
            const card = a.closest('.ContentItem');
            add({title:a.innerText.trim(),url:"https://"+u.hostname+u.pathname,
              summary:card ? get(card,'.RichContent-inner').slice(0,1000) : '', author:card ? get(card,'.AuthorInfo-name') : ''});
          }
        } else if (platform === "xiaohongshu") {
          for (const card of document.querySelectorAll('section.note-item')) {
            const id = card.getAttribute('data-note-id');
            if (!/^[a-f0-9]{24}$/.test(id || "")) continue;
            // Canonical link only; do not export xsec_token or signed media URLs.
            add({title:get(card,'.title'),url:"https://www.xiaohongshu.com/explore/"+id,author:get(card,'.name')});
          }
        } else if (platform === "douyin") {
          for (const card of document.querySelectorAll('[id^="waterfall_item_"]')) {
            const id = card.id.replace('waterfall_item_','');
            if (!/^\d+$/.test(id)) continue;
            add({title:get(card,'.BjLsdJMi'),url:"https://www.douyin.com/"+(/^图文/.test(card.innerText) ? 'note/' : 'video/')+id,author:get(card,'.WldPmwm5')});
          }
        } else if (platform === "sogou_wechat") {
          for (const a of document.querySelectorAll('.news-list h3 a')) {
            const u = new URL(a.href), redirect = u.searchParams.get('url');
            if (u.hostname !== 'weixin.sogou.com' || u.pathname !== '/link' || !redirect) continue;
            const card = a.closest('li');
            add({title:a.innerText.trim(),url:'https://weixin.sogou.com/link?url='+encodeURIComponent(redirect),
              summary:card ? get(card,'.txt-info') : '',author:card ? get(card,'.all-time-y2') : ''});
          }
        }
        return {status: items.length ? "ok" : /抱歉.*没有找到|未找到相关商品|没有搜索到相关|没有找到|没有搜到|暂无搜索结果|暂无相关结果/.test(text) ? "empty" : "parse_error", items};
      }, {limit:input.limit, platform, host, definition});
      emit(result.status, result.items);
    }
  }
} catch (error) {
  const message = String(error);
  emit(/permission|control|hand.?off|inactive|unassigned/i.test(message) ? "auth_required" :
       /timed.?out|timeout/i.test(message) ? "timeout" : "startup_failed");
}

finally {
  clearTimeout(deadline);
  if (ownedPage) {
    try { await ownedPage.close(); }
    catch { emit('startup_failed'); }
  }
  if (expired) emit('timeout');
  console.log("RESEARCH_BROWSER_JSON:" + JSON.stringify(payload || {status:'startup_failed',items:[]}));
}
