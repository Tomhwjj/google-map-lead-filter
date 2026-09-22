/* ============================================================
   FB 群组 / 主页  帖子+评论 一键导出器
   2026-09-17  by WorkBuddy
   ------------------------------------------------------------
   为什么需要它：
     服务端抓 FB 已被全面封死（实测 7 条路全挂）。但**你自己的
     浏览器是登录态**，页面已经渲染好——这个脚本只是替你"滚动+
     展开评论+复制"，跑在你本地，不碰你的账号，不发任何外部请求。

   用法（三步）：
     1. 浏览器打开目标群/主页（已登录），把帖子列表滚到最顶上
     2. 按 F12 → 切到 Console 标签 → 粘贴本文件全部内容 → 回车
     3. 等它跑完（会打印进度），自动下载 fb_export_*.json
        → 把这个 json 发给我，我解析入库

   可调参数见下方 CFG。
   ============================================================ */
(async () => {
  const CFG = {
    MAX_SCREEN: 500,       // 最多滚多少屏（1 屏 ≈ 900px）。想要更全就调大
    DELAY: 1100,           // 每屏等待毫秒。网慢 / 群大就调到 1500-2000
    STALL: 8,              // 连续 N 屏页面高度不变 → 判定到底，自动停
    EXPAND_COMMENTS: true, // 自动点"查看更多评论"。只想快速抓主帖可设 false
    SCROLL_RATIO: 0.9      // 每屏滚动比例，别设 1.0（会跳过内容）
  };

  const log = (...a) => console.log('%c[FB导出]', 'color:#0a8;font-weight:bold', ...a);
  const posts = new Map();     // key -> 记录
  const seenBtn = new WeakSet();
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  /* ---- 取元素唯一链接（FB 帖子都会有 posts/ 或 story_fbid 的永久链接）---- */
  const keyFromLink = (el) => {
    const a = el.querySelector(
      'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"], a[href*="multi_permalinks"]'
    );
    if (a && a.href) return a.href.split('?')[0].replace(/\/+$/, '');
    return null;
  };

  /* ---- 抓取：同时判定"主帖 / 评论"，用祖先里有没有 role=article 区分 ---- */
  const grab = () => {
    let n = 0;
    for (const el of document.querySelectorAll('div[role="article"]')) {
      const text = (el.innerText || '').replace(/\u00a0/g, ' ').trim();
      if (text.length < 8) continue;

      let anc = el.parentElement, parent = null, depth = 0;
      while (anc) {
        if (anc.getAttribute && anc.getAttribute('role') === 'article') { parent = anc; depth++; }
        anc = anc.parentElement;
      }

      const link = keyFromLink(el);
      const key = link
        ? (depth ? `c:${link}:${text.slice(0, 60)}` : `p:${link}`)
        : (depth ? `c:txt:${text.slice(0, 120)}` : `p:txt:${text.slice(0, 120)}`);

      const rec = posts.get(key);
      if (!rec) {
        posts.set(key, {
          kind: depth ? 'comment' : 'post',
          text,
          link: link || '',
          depth,
          parentKey: parent
            ? (keyFromLink(parent) || `p:txt:${(parent.innerText || '').slice(0, 120)}`)
            : '',
          capturedAt: Date.now()
        });
        n++;
      } else if (rec.text.length < text.length) {
        rec.text = text;   // 同一条被渲染得更完整 → 覆盖
        n++;
      }
    }
    return n;
  };

  /* ---- 展开评论：按多语言按钮文案匹配 ---- */
  const expand = () => {
    const rx = /(wi[ęe]cej komentarz|zobacz (wi[ęe]cej|wszystkie|wcze[śs]niejsze) komentarz|wszystkie komentarze|view (more|all|\d+) comment|more comment|weitere kommentar|alle kommentar|ver m[áa]s comentario|todos los comentario|ver comentarios|更多评论|查看全部评论|全部评论|查看\d+条评论|all comments)/i;
    let clicked = 0;
    document.querySelectorAll('div[role="button"], span[role="button"], [role="button"], div[aria-label]').forEach(b => {
      if (seenBtn.has(b)) return;
      const s = (b.innerText || b.getAttribute('aria-label') || '').trim();
      if (s && s.length < 70 && rx.test(s)) {
        seenBtn.add(b);
        try { b.click(); clicked++; } catch (e) { /* 忽略不可点元素 */ }
      }
    });
    return clicked;
  };

  /* ---- 开始 ---- */
  grab();
  log(`起点：已捕获 ${posts.size} 条。开始滚动（上限 ${CFG.MAX_SCREEN} 屏）…`);

  let lastH = 0, stall = 0, topReached = false;
  for (let i = 1; i <= CFG.MAX_SCREEN; i++) {
    window.scrollBy(0, Math.round(window.innerHeight * CFG.SCROLL_RATIO));
    if (CFG.EXPAND_COMMENTS) expand();
    await sleep(CFG.DELAY);
    grab();

    const h = document.documentElement.scrollHeight;
    if (h === lastH) stall++; else stall = 0;
    lastH = h;

    if (i % 10 === 0) log(`第 ${i} 屏 · 累计 ${posts.size} 条`);
    if (stall >= CFG.STALL) { log(`连续 ${stall} 屏无新内容 → 判定到底，停止。`); topReached = true; break; }
  }

  if (CFG.EXPAND_COMMENTS) { expand(); await sleep(1800); grab(); }

  /* ---- 导出 ---- */
  const arr = [...posts.values()].sort((a, b) => a.capturedAt - b.capturedAt);
  const nPost = arr.filter(x => x.kind === 'post').length;
  const nCmt = arr.filter(x => x.kind === 'comment').length;
  log(`完成：主帖 ${nPost} · 评论 ${nCmt} · 合计 ${arr.length}${topReached ? '' : '（注意：可能还没滚到底）'}`);

  const payload = {
    exportedAt: new Date().toISOString(),
    pageUrl: location.href,
    pageTitle: document.title,
    counts: { posts: nPost, comments: nCmt },
    items: arr
  };

  const slug = (location.pathname.split('/').filter(Boolean).join('_') || 'page').slice(0, 60);
  const name = `fb_export_${slug}_${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  log(`已下载：${name}  → 把这个文件发给我。`);

  try {
    await navigator.clipboard.writeText(JSON.stringify(payload));
    log('（内容也已复制到剪贴板，可直接粘贴）');
  } catch (e) { /* 大内容剪贴板可能失败，不影响下载 */ }
})();
