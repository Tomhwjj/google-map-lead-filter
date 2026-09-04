// 线索卡片双模式切换 —— 从 scripts/render_report.py 原版 <script> 提取。
// 头部/长尾两套分数、分级、维度条、排序、统计栏、A/B/C 筛选、导出 CSV。
(function () {
  const app = document.getElementById('leads-app');
  if (!app) return; // 非卡片页不执行

  let mode = 'head';
  let filter = 'all';
  const GRADE_STYLE = {
    A: { color: '#16a34a', bg: '#ecfdf3' },
    B: { color: '#d97706', bg: '#fffbeb' },
    C: { color: '#6b7280', bg: '#f3f4f6' },
  };

  function applyMode() {
    app.querySelectorAll('.card').forEach((c) => {
      const isTail = mode === 'tail';
      const score = isTail ? c.dataset.scoreLt : c.dataset.score;
      const grade = isTail ? c.dataset.gradeLt : c.dataset.grade;
      c.querySelector('.score-num').textContent = score;
      c.querySelector('.head-grade').style.display = isTail ? 'none' : '';
      const tg = c.querySelector('.tail-grade');
      tg.style.display = isTail ? '' : 'none';
      tg.textContent = grade;
      const st = GRADE_STYLE[grade] || GRADE_STYLE.C;
      tg.style.color = st.color;
      tg.style.background = st.bg;
      c.querySelector('.head-detail').style.display = isTail ? 'none' : '';
      c.querySelector('.tail-detail').style.display = isTail ? '' : 'none';
      c.dataset.grade = grade;
    });
    sortCards();
    updateStats();
    applyFilter();
  }

  function sortCards() {
    const list = document.getElementById('list');
    const cards = Array.from(list.querySelectorAll('.card'));
    cards.sort((a, b) =>
      mode === 'tail'
        ? b.dataset.scoreLt - a.dataset.scoreLt
        : b.dataset.score - a.dataset.score);
    cards.forEach((c) => list.appendChild(c));
  }

  function updateStats() {
    const cnt = { A: 0, B: 0, C: 0 };
    app.querySelectorAll('.card').forEach((c) => {
      const g = mode === 'tail' ? c.dataset.gradeLt : c.dataset.grade;
      cnt[g] = (cnt[g] || 0) + 1;
    });
    document.getElementById('stat-a').textContent = cnt.A;
    document.getElementById('stat-b').textContent = cnt.B;
  }

  function applyFilter() {
    app.querySelectorAll('.card').forEach((c) => {
      const g = mode === 'tail' ? c.dataset.gradeLt : c.dataset.grade;
      c.style.display = filter === 'all' || g === filter ? '' : 'none';
    });
  }

  app.querySelectorAll('.mode-switch button').forEach((b) => {
    b.addEventListener('click', () => {
      app.querySelectorAll('.mode-switch button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      mode = b.dataset.mode;
      applyMode();
    });
  });

  app.querySelectorAll('.lead-filter button').forEach((b) => {
    b.addEventListener('click', () => {
      app.querySelectorAll('.lead-filter button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      filter = b.dataset.f;
      applyFilter();
    });
  });

  window.exportCSV = function () {
    const cards = Array.from(app.querySelectorAll('.card')).filter((c) => c.style.display !== 'none');
    const rows = [['公司名', '国家', '城市', '官网', '电话', '邮箱', '客户类型', '卖Deye', '品牌命中', '分数', '分级', '开发理由']];
    cards.forEach((c) => {
      const name = c.querySelector('.name').textContent;
      const ctype = c.querySelector('.ctype') ? c.querySelector('.ctype').textContent : '';
      const loc = c.querySelector('.loc') ? c.querySelector('.loc').textContent : '';
      const parts = loc.split(' · ');
      const country = parts[0] || '';
      const city = parts[1] || '';
      const siteA = c.querySelector('a.site');
      const website = siteA ? siteA.getAttribute('href') : '';
      const phoneA = c.querySelector('a.phone');
      const phone = phoneA ? phoneA.getAttribute('href').replace('tel:', '') : '';
      const mailA = c.querySelector('a[href^="mailto:"]');
      const email = mailA ? mailA.getAttribute('href').replace('mailto:', '') : '';
      const deye = c.querySelector('.deye') ? '是' : '';
      const brands = Array.from(c.querySelectorAll('.brand')).map((b) => b.textContent).join('|');
      const score = c.querySelector('.score-num').textContent;
      const grade = mode === 'tail' ? c.dataset.gradeLt : c.dataset.grade;
      const reasonEl = c.querySelector('.reason');
      const reason = reasonEl ? reasonEl.textContent.replace('开发理由：', '') : '';
      rows.push([name, country, city, website, phone, email, ctype, deye, brands, score, grade, reason]);
    });
    const csv = rows.map((r) => r.map((x) => '"' + (x || '').replace(/"/g, '""') + '"').join(',')).join('\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'leads_' + mode + '.csv';
    a.click();
  };

  // 初始：头部模式排序
  sortCards();
})();
