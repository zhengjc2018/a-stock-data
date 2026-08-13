const $ = (id) => document.getElementById(id);

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)))
  ? "--" : Number(v).toFixed(d);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const signed = (v, d = 2) => (v === null || v === undefined ? "--" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

const LABELS = {
  code: "代码", name: "名称", industry: "行业", price: "价格", pct: "涨跌幅",
  change_pct: "涨跌幅", limit_days: "连板数", zt_stat: "连板", first_seal: "首封",
  last_seal: "末封", seal_fund: "封单资金", break_times: "炸板次数", turnover: "换手率",
  amplitude: "振幅", speed: "涨速", dt_days: "跌停天数", open_times: "开板次数",
  market: "市场", start: "开始", end: "结束", link: "链接", deviation: "偏离",
  days: "天数", rule: "规则", heat: "热度", rank_chg: "排名变化", concepts: "概念",
  tag: "标签", date: "日期", title: "标题", org: "机构", rating: "评级",
  eps_this: "今年EPS", eps_next: "明年EPS", main_net: "主力净额", small_net: "小单",
  mid_net: "中单", large_net: "大单", super_net: "超大单", net_buy_wan: "净买(万)",
  buy_wan: "买额(万)", sell_wan: "卖额(万)", reason: "原因", close: "收盘",
  change: "涨跌", volume: "成交量", amount: "成交额", open_interest: "持仓",
  iv: "IV", delta: "Delta", gamma: "Gamma", theta: "Theta", vega: "Vega",
  theory: "理论价", last: "最新", bid: "买价", ask: "卖价", strike: "行权价",
  type: "类型", url: "链接", period: "报告期", time: "时间", content: "内容",
  summary: "摘要", rank: "排名", leader: "领涨股", main_pct: "净占比",
  total_shares: "总股本", float_shares: "流通股", mcap: "总市值", float_mcap: "流通市值",
  list_date: "上市日期", turn_rate: "换手", prob: "高开概率", score: "评分",
  yzt_count: "昨涨停", promotion_rate: "晋级率", break_rate: "炸板率",
  zt_count: "涨停", zb_count: "炸板", dt_count: "跌停", max_height: "最高连板",
};

function setStatus(text, mode) {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + (mode || "");
}

function setUpdated(ts) {
  if (!ts) return;
  const d = new Date(ts * 1000);
  $("updated").textContent = "数据时间 " + d.toLocaleTimeString("zh-CN", { hour12: false });
}

function table(headers, rows) {
  if (!rows || !rows.length) return '<div class="empty">暂无数据</div>';
  return `<div class="table-scroll"><table><thead><tr>${
    headers.map((h) => `<th class="${h.align === "left" ? "left" : ""} ${h.hideSm ? "hide-sm" : ""}">${esc(h.label)}</th>`).join("")
  }</tr></thead><tbody>${
    rows.map((r) => `<tr>${
      headers.map((h) => {
        const raw = r[h.key];
        const val = h.format ? h.format(raw, r) : (raw === null || raw === undefined ? "--" : raw);
        return `<td class="${h.align === "left" ? "left" : ""} ${h.cls ? h.cls(raw, r) : ""} ${h.hideSm ? "hide-sm" : ""}">${h.raw ? val : esc(val)}</td>`;
      }).join("")
    }</tr>`).join("")
  }</tbody></table></div>`;
}

function genericTable(list, limit = 50) {
  if (!list || !list.length) return '<div class="empty">暂无数据</div>';
  const rows = list.slice(0, limit);
  const keys = Object.keys(rows[0] || {}).filter((k) => k !== "url");
  const headers = keys.map((k) => ({
    key: k, label: LABELS[k] || k, align: "left",
    format: (v, r) => {
      if (k === "iv" && typeof v === "number") return (v * 100).toFixed(2) + "%";
      if (typeof v === "number") return v >= 10000 ? v.toFixed(0) : Number(v.toFixed(2)).toLocaleString();
      if (Array.isArray(v)) return v.join("、");
      return v;
    },
  }));
  return table(headers, rows);
}

function newsBlock(title, items, timeKey, textKey) {
  if (!items || !items.length) return `<div class="news-col"><h3>${esc(title)}</h3><div class="empty">暂无数据</div></div>`;
  return `<div class="news-col"><h3>${esc(title)}</h3>${items.map((it) => `
    <div class="news-item">
      <div class="t">${esc(it[textKey] || it.title || "")}</div>
      <div class="d">${esc(it[timeKey] || it.time || "")}</div>
    </div>`).join("")}</div>`;
}

function renderIndices(indices) {
  const box = $("indices");
  box.innerHTML = "";
  if (!indices || !Object.keys(indices).length) {
    box.innerHTML = '<div class="empty" style="grid-column:1/-1">暂无指数数据</div>';
    return;
  }
  Object.entries(indices).forEach(([code, q]) => {
    const el = document.createElement("div");
    el.className = "idx";
    el.innerHTML = `
      <div class="nm">${esc(q.name || code)}</div>
      <div class="pr ${cls(q.change_pct)}">${fmt(q.price)}</div>
      <div class="pc ${cls(q.change_pct)}">${signed(q.change_pct)}%</div>`;
    box.appendChild(el);
  });
}

function renderChips(s) {
  const box = $("chips");
  box.innerHTML = "";
  if (!s || Object.keys(s).length === 0) {
    box.innerHTML = '<div class="chip">情绪数据暂不可用</div>';
    return;
  }
  const items = [
    ["涨停", s.zt_count], ["炸板", s.zb_count], ["跌停", s.dt_count],
    ["最高连板", s.max_height ? `${s.max_height}板` : "--"],
    ["炸板率", s.break_rate ? `${s.break_rate}%` : "--"],
    ["昨涨停晋级", s.promotion_rate !== null && s.promotion_rate !== undefined
      ? `${s.promotion_rate}%` : "--"],
  ];
  items.forEach(([label, val]) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `<span>${label}</span><b>${val ?? "--"}</b>`;
    box.appendChild(chip);
  });
}

function renderFlow(flow) {
  if (!flow || !flow.rows || !flow.rows.length) return '<div class="empty">板块资金流暂不可用</div>';
  return table([
    { key: "rank", label: "#", align: "left" },
    { key: "name", label: "板块", align: "left" },
    { key: "change_pct", label: "涨跌幅", format: (v) => signed(v) + "%", cls: (v) => cls(v) },
    { key: "main_net", label: "主力净额", format: (v) => (v / 1e8).toFixed(1) + "亿", cls: (v) => cls(v) },
    { key: "main_pct", label: "净占比", format: (v) => fmt(v) + "%", hideSm: true, cls: (v) => cls(v) },
    { key: "leader", label: "领涨股", align: "left", hideSm: true },
  ], flow.rows);
}

async function loadOverview() {
  try {
    const d = await (await fetch("/api/overview")).json();
    setUpdated(d.ts);
    $("overview-time").textContent = new Date(d.ts * 1000).toLocaleString("zh-CN", { hour12: false });
    renderIndices(d.indices);
    renderChips(d.sentiment);
    $("overview-flow").innerHTML = renderFlow(d.board_flow);
    if (d.error) setStatus("部分接口异常", "err");
  } catch (e) {
    setStatus("连接失败", "err");
  }
}

const POOL_TABS = [
  ["zt", "涨停池"], ["zb", "炸板池"], ["dt", "跌停池"],
  ["yzt", "昨日涨停"], ["monitor", "重点监控"], ["anomaly", "日内异动"],
];

async function loadPools() {
  const box = $("pools-content");
  box.innerHTML = '<div class="empty">加载中...</div>';
  let d;
  try {
    d = await (await fetch("/api/pools")).json();
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
    return;
  }
  $("pools-meta").textContent = d.date || "";
  const tabs = $("pools-tabs");
  tabs.innerHTML = POOL_TABS.map(([key, label]) =>
    `<button class="sub-tab" data-pool="${key}">${label}</button>`).join("");
  tabs.querySelectorAll(".sub-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      tabs.querySelectorAll(".sub-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderPool(d, btn.dataset.pool);
    });
  });
  const first = tabs.querySelector(".sub-tab");
  first.classList.add("active");
  renderPool(d, "zt");
}

function renderPool(d, key) {
  const box = $("pools-content");
  const rows = d[key] || [];
  if (key === "monitor") {
    box.innerHTML = table([
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "market", label: "市场" },
      { key: "start", label: "开始" },
      { key: "end", label: "结束" },
      { key: "link", label: "链接", align: "left", raw: true, format: (v) => v ? `<a href="${esc(v)}" target="_blank">查看</a>` : "--" },
    ], rows);
    return;
  }
  if (key === "anomaly") {
    box.innerHTML = table([
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "market", label: "市场" },
      { key: "change_pct", label: "涨跌幅", format: (v) => signed(v) + "%", cls: (v) => cls(v) },
      { key: "deviation", label: "偏离", format: (v) => fmt(v) + "%" },
      { key: "days", label: "天数" },
      { key: "rule", label: "触发规则", align: "left" },
    ], rows.slice(0, 50));
    return;
  }
  box.innerHTML = genericTable(rows, 80);
}

async function loadBoards() {
  const type = $("board-type").value;
  const period = $("board-period").value;
  const box = $("boards-content");
  box.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await (await fetch(`/api/board_flow?type=${type}&period=${period}&top=12`)).json();
    box.innerHTML = renderFlow(d);
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadHot() {
  const box = $("hot-content");
  box.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await (await fetch("/api/hot")).json();
    $("hot-meta").textContent = `同花顺 ${d.ths.length} · 东财人气 ${d.em_rank.length} · 电报 ${d.telegraph.length} · 全球 ${d.global.length}`;
    box.innerHTML = `<div class="news-list">
      ${newsBlock("同花顺热榜", d.ths.map((r) => ({ ...r, text: `${r.rank}. ${r.name}  ${signed(r.pct)}%` })), "heat", "text")}
      ${newsBlock("东财人气榜", d.em_rank.map((r) => ({ ...r, text: `${r.rank}. ${r.name || r.code}  ${signed(r.pct)}%` })), "price", "text")}
      ${newsBlock("财联社电报", d.telegraph, "time", "content")}
      ${newsBlock("全球资讯", d.global, "time", "summary")}
    </div>`;
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadLhb() {
  const box = $("lhb-content");
  box.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await (await fetch("/api/lhb")).json();
    $("lhb-meta").textContent = `${d.date} · ${d.total_records || 0} 条`;
    box.innerHTML = table([
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "reason", label: "上榜原因", align: "left" },
      { key: "change_pct", label: "涨跌幅", format: (v) => signed(v) + "%", cls: (v) => cls(v) },
      { key: "net_buy_wan", label: "净买(万)", format: (v) => fmt(v, 1) },
      { key: "buy_wan", label: "买额(万)", format: (v) => fmt(v, 1), hideSm: true },
      { key: "sell_wan", label: "卖额(万)", format: (v) => fmt(v, 1), hideSm: true },
      { key: "turnover_pct", label: "换手", format: (v) => fmt(v) + "%", hideSm: true },
    ], d.stocks || []);
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
}

function renderGap(payload) {
  const meta = $("gap-meta");
  const box = $("gap");
  const data = payload.data;
  if (payload.computing) {
    setStatus("计算中", "busy");
    meta.textContent = "首次全市场扫描需要几分钟，完成后自动刷新";
    box.innerHTML = '<div class="empty">正在扫描全市场并计算次日高开概率，请稍候...</div>';
    setTimeout(loadGap, 3000);
    return;
  }
  if (payload.last_err) {
    setStatus("接口异常", "err");
    meta.textContent = payload.last_err;
    box.innerHTML = `<div class="empty">计算失败：${esc(payload.last_err)}<br><button style="margin-top:12px" onclick="loadGap(true)">重新计算</button></div>`;
    return;
  }
  if (!data) {
    setStatus("未计算", "");
    meta.textContent = "尚未计算";
    box.innerHTML = '<div class="empty">点击「立即计算」开始全市场选股</div>';
    return;
  }
  if (!data || !data.candidates || !data.candidates.length) {
    setStatus("暂无推荐", "err");
    meta.textContent = data ? `扫描完成，无候选（${data.date}）` : "暂无数据";
    box.innerHTML = '<div class="empty">当前没有符合条件的次日高开候选</div>';
    return;
  }
  setStatus("已连接", "ok");
  const ranking = data.ranking === "model" ? "模型概率排序" : "规则评分排序";
  const mm = (payload.model && payload.model.metrics) || {};
  const top10 = mm.test_top10 ? `测试Top10命中 ${(mm.test_top10 * 100).toFixed(1)}%` : "";
  meta.textContent = `${data.date} · 仅主板 · GBDT校准 · ${ranking} · 候选 ${data.total} 只 · ${top10} · 耗时 ${data.elapsed_sec}s`;
  const top = data.candidates.slice(0, 10);
  box.innerHTML = table([
    { key: "rank", label: "#", align: "left", raw: true, format: (v, r) => `<b>${r._i + 1}</b>` },
    { key: "code", label: "代码", align: "left" },
    { key: "name", label: "名称", align: "left" },
    { key: "industry", label: "行业", align: "left", hideSm: true },
    { key: "price", label: "现价" },
    { key: "change_pct", label: "今日", format: (v) => signed(v) + "%", cls: (v) => cls(v) },
    { key: "prob", label: "高开概率", format: (v) => (v === null || v === undefined || Number.isNaN(v) ? "--" : (v * 100).toFixed(1) + "%") },
    { key: "reason", label: "入选理由", align: "left" },
  ], top.map((c, i) => ({ ...c, _i: i })));
}

async function loadGap(force) {
  if (force) {
    try { await fetch("/api/gap/refresh", { method: "POST" }); } catch (e) {}
  }
  try {
    renderGap(await (await fetch("/api/gap")).json());
  } catch (e) {
    setStatus("连接失败", "err");
  }
}

const STOCK_TABS = [
  ["eps", "一致预期"], ["reports", "研报"], ["dragon_tiger", "龙虎榜"],
  ["margin", "两融"], ["block_trade", "大宗"], ["holder", "股东户数"],
  ["dividend", "分红"], ["fund_flow", "资金流"], ["irm", "互动易"],
  ["news", "新闻"], ["hot_concept", "概念"], ["announcements", "公告"], ["finance", "财报"],
];

async function loadStock() {
  const code = $("stock-code").value.trim();
  if (!/^\d{6}$/.test(code)) {
    $("stock-content").innerHTML = '<div class="empty">请输入 6 位股票代码</div>';
    return;
  }
  const summary = $("stock-summary");
  const box = $("stock-content");
  summary.innerHTML = "";
  box.innerHTML = '<div class="empty">加载中，约需 10-30 秒...</div>';
  let d;
  try {
    d = await (await fetch(`/api/stock/${code}`)).json();
  } catch (e) {
    box.innerHTML = '<div class="empty">查询失败</div>';
    return;
  }
  if (d.error) {
    box.innerHTML = `<div class="empty">查询失败：${esc(d.error)}</div>`;
    return;
  }
  const q = d.quote || {};
  const info = d.info || {};
  const cards = [
    ["名称", q.name || info.name || code],
    ["现价", q.price ? fmt(q.price) : "--"],
    ["涨跌幅", q.change_pct !== undefined ? signed(q.change_pct) + "%" : "--"],
    ["PE(TTM)", q.pe_ttm ? fmt(q.pe_ttm) : "--"],
    ["PB", q.pb ? fmt(q.pb) : "--"],
    ["总市值", info.mcap ? (info.mcap / 1e8).toFixed(1) + "亿" : q.mcap_yi ? fmt(q.mcap_yi) + "亿" : "--"],
    ["行业", info.industry || "--"],
    ["上市日期", info.list_date || "--"],
  ];
  summary.innerHTML = `<div class="summary-grid">${cards.map(([k, v]) =>
    `<div class="summary"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>`;
  const tabs = $("stock-tabs");
  tabs.innerHTML = STOCK_TABS.map(([key, label]) =>
    `<button class="sub-tab" data-stock="${key}">${label}</button>`).join("");
  const dataMap = {
    eps: d.eps || [], reports: (d.extra || {}).reports || [],
    dragon_tiger: (d.extra || {}).dragon_tiger || [], margin: (d.extra || {}).margin || [],
    block_trade: (d.extra || {}).block_trade || [], holder: (d.extra || {}).holder || [],
    dividend: (d.extra || {}).dividend || [], fund_flow: (d.extra || {}).fund_flow || [],
    irm: (d.extra || {}).irm || [], news: (d.extra || {}).news || [],
    hot_concept: (d.extra || {}).hot_concept || [], announcements: d.announcements || [],
    finance: d.finance || [],
  };
  tabs.querySelectorAll(".sub-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      tabs.querySelectorAll(".sub-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderStockTab(btn.dataset.stock, dataMap);
    });
  });
  tabs.querySelector(".sub-tab").classList.add("active");
  renderStockTab("eps", dataMap);
}

function renderStockTab(key, dataMap) {
  const box = $("stock-content");
  const rows = dataMap[key] || [];
  if (key === "news") {
    box.innerHTML = `<div class="news-list">${newsBlock("个股新闻", rows, "time", "title")}</div>`;
    return;
  }
  box.innerHTML = genericTable(rows, 80);
}

async function loadOptions() {
  const etf = $("option-etf").value;
  const box = $("options-content");
  box.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await (await fetch(`/api/options?etf=${etf}`)).json();
    $("options-meta").textContent = d.month ? `20${d.month} 合约 · 平值附近` : "";
    box.innerHTML = table([
      { key: "strike", label: "行权价", align: "left", format: (v) => fmt(v, 3) },
      { key: "call", label: "认购最新", format: (v) => fmt(v && v.last, 4), cls: (v) => cls(v && v.pct) },
      { key: "call_pct", label: "认购涨跌", format: (v, r) => signed(r.call && r.call.pct) + "%", cls: (v, r) => cls(r.call && r.call.pct) },
      { key: "call_iv", label: "认购IV", format: (v, r) => (r.call && r.call.iv ? (r.call.iv * 100).toFixed(2) + "%" : "--") },
      { key: "call_delta", label: "认购Delta", format: (v, r) => fmt(r.call && r.call.delta, 3), hideSm: true },
      { key: "put", label: "认沽最新", format: (v) => fmt(v && v.last, 4), cls: (v) => cls(v && v.pct) },
      { key: "put_pct", label: "认沽涨跌", format: (v, r) => signed(r.put && r.put.pct) + "%", cls: (v, r) => cls(r.put && r.put.pct) },
      { key: "put_iv", label: "认沽IV", format: (v, r) => (r.put && r.put.iv ? (r.put.iv * 100).toFixed(2) + "%" : "--") },
      { key: "put_delta", label: "认沽Delta", format: (v, r) => fmt(r.put && r.put.delta, 3), hideSm: true },
    ], (d.rows || []).map((r) => ({ ...r, call_pct: r.call && r.call.pct, put_pct: r.put && r.put.pct })));
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadStrategyHealth() {
  const box = $("health-summary");
  const histBox = $("health-history");
  const dayBox = $("health-days");
  box.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await (await fetch("/api/strategy_health")).json();
    const m = d.model || {};
    const s = d.stats || {};
    $("health-meta").textContent = `已验证 ${s.verified_days || 0} 天 · 近30天Top10 ${s.top10_rate ? (s.top10_rate * 100).toFixed(1) + "%" : "--"}`;
    const cards = [
      ["当前模型", (m.type || "--").toUpperCase()],
      ["测试AUC", m.metrics && m.metrics.test_auc ? m.metrics.test_auc : "--"],
      ["测试Top10", m.metrics && m.metrics.test_top10 ? (m.metrics.test_top10 * 100).toFixed(1) + "%" : "--"],
      ["已验证天数", s.verified_days || 0],
      ["近30天Top10", s.top10_rate ? (s.top10_rate * 100).toFixed(1) + "%" : "--"],
      ["真实高开基准", s.base_rate ? (s.base_rate * 100).toFixed(1) + "%" : "--"],
    ];
    box.innerHTML = `<div class="summary-grid">${cards.map(([k, v]) =>
      `<div class="summary"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>`;
    histBox.innerHTML = table([
      { key: "ts", label: "时间", align: "left" },
      { key: "action", label: "动作" },
      { key: "reason", label: "原因", align: "left" },
      { key: "top10", label: "Top10", format: (v, r) => r.metrics && r.metrics.test_top10 ? (r.metrics.test_top10 * 100).toFixed(1) + "%" : "--" },
      { key: "auc", label: "AUC", format: (v, r) => r.metrics && r.metrics.test_auc ? r.metrics.test_auc : "--" },
    ], (d.history || []).map((h) => ({ ...h, top10: h.metrics && h.metrics.test_top10, auc: h.metrics && h.metrics.test_auc })));
    dayBox.innerHTML = table([
      { key: "date", label: "日期", align: "left" },
      { key: "total", label: "候选数" },
      { key: "hits", label: "命中" },
      { key: "top1", label: "Top1" },
      { key: "top3", label: "Top3" },
      { key: "top10", label: "Top10" },
    ], (s.recent || []).map((r) => ({ ...r, top1: r.top1 ? "是" : "-", top3: r.top3 ? "是" : "-", top10: r.top10 ? "是" : "-" })));
  } catch (e) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function refreshRetrainStatus() {
  try {
    const d = await (await fetch("/api/retrain/status")).json();
    const el = $("retrain-status");
    const btn = $("retrain-btn");
    if (d.running) {
      el.textContent = "训练中，约 3-5 分钟，完成后自动刷新";
      btn.disabled = true;
      setTimeout(refreshRetrainStatus, 5000);
      return;
    }
    btn.disabled = false;
    if (d.err) {
      el.textContent = "上次训练失败：" + d.err;
    } else if (d.result && d.result.action === "publish") {
      el.textContent = `上次：已发布新模型（${(d.result.metrics.test_top10 * 100).toFixed(1)}% Top10）`;
    } else if (d.result && d.result.action === "reject") {
      el.textContent = "上次：新模型未达标，未发布";
    } else {
      el.textContent = "";
    }
  } catch (e) {
    $("retrain-status").textContent = "";
  }
}

async function triggerRetrain() {
  const el = $("retrain-status");
  try {
    const r = await fetch("/api/retrain", { method: "POST" });
    const d = await r.json();
    if (!r.ok) {
      el.textContent = "已有训练进行中，请稍候";
      return;
    }
    el.textContent = "训练已启动，约 3-5 分钟...";
    $("retrain-btn").disabled = true;
    setTimeout(() => {
      const poll = setInterval(async () => {
        const s = await (await fetch("/api/retrain/status")).json();
        if (!s.running) {
          clearInterval(poll);
          await refreshRetrainStatus();
          loadStrategyHealth();
        }
      }, 5000);
    }, 2000);
  } catch (e) {
    el.textContent = "触发失败";
  }
}

async function loadTState() {
  try {
    const d = await (await fetch("/api/t/state")).json();
    $("t-meta").textContent = `监控${d.monitoring ? "中" : "未启动"} · 自动执行${d.auto_execute ? "开" : "关"} · 上次检查 ${d.last_check || "--"}`;
    $("t-holdings").innerHTML = table([
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "cost", label: "成本", format: (v) => fmt(v) },
      { key: "qty", label: "数量", format: (v) => fmt(v, 0) },
      { key: "price", label: "现价", format: (v) => fmt(v) },
      { key: "profit", label: "浮动盈亏", format: (v, r) => r.price ? `${signed((r.price / r.cost - 1) * 100)}%` : "--", cls: (v, r) => r.price && r.cost ? cls(r.price - r.cost) : "" },
      { key: "op", label: "操作", align: "left", raw: true, format: (v, r) => `<button data-del="${esc(r.id)}">删除</button>` },
    ], (d.holdings || []).map((h) => ({ ...h, profit: h.price ? (h.price / h.cost - 1) * 100 : null })));
    $("t-holdings").querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        await fetch("/api/t/holdings/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: b.dataset.del }),
        });
        loadTState();
      }));
    const analysisCards = (d.holdings || []).map((h) => {
      const a = h.analysis || {};
      if (a.status === "analyzing") {
        return `<div class="summary"><div class="k">${esc(h.code)} ${esc(h.name)}</div><div class="v" style="font-size:13px">特征分析中...</div></div>`;
      }
      if (a.status === "profile" && a.profile) {
        const p = a.profile;
        return `<div class="summary">
          <div class="k">${esc(h.code)} ${esc(h.name)} · ${esc(p.trend)} · ${esc(p.volatility)}</div>
          <div style="font-size:12px;line-height:1.8;margin-top:4px">
            20日收益 ${(p.ret_20 * 100).toFixed(1)}% · 日均振幅 ${(p.avg_daily_amp * 100).toFixed(1)}%
            <br>VWAP偏离 ${(p.vwap_dev_avg * 100).toFixed(2)}% · 量能比 ${fmt(p.vol_ratio_5_20)}
            <br><span style="color:var(--accent)">参数优化后台进行中...</span>
          </div>
        </div>`;
      }
      if (a.status === "error") {
        return `<div class="summary"><div class="k">${esc(h.code)} ${esc(h.name)}</div><div class="v" style="font-size:12px;color:var(--up)">${esc(a.error || "分析失败")}</div></div>`;
      }
      if (!a.profile) {
        return `<div class="summary"><div class="k">${esc(h.code)} ${esc(h.name)}</div><div class="v" style="font-size:13px">等待分析</div></div>`;
      }
      const p = a.profile;
      const test = (a.test && a.test.combined) || {};
      return `<div class="summary">
        <div class="k">${esc(h.code)} ${esc(h.name)} · ${esc(p.trend)} · ${esc(p.volatility)}</div>
        <div style="font-size:12px;line-height:1.8;margin-top:4px">
          20日收益 ${(p.ret_20 * 100).toFixed(1)}% · 日均振幅 ${(p.avg_daily_amp * 100).toFixed(1)}%
          <br>VWAP偏离 ${(p.vwap_dev_avg * 100).toFixed(2)}% · 量能比 ${fmt(p.vol_ratio_5_20)}
          <br>参数 止盈/止损 ${(a.params && a.params.target * 100).toFixed(1)}% · RSI ${a.params && a.params.rsi_low}/${a.params && a.params.rsi_high} · 评分≥${a.params && a.params.score}
          <br>验证胜率 ${test.win_rate ? (test.win_rate * 100).toFixed(1) + "%" : "--"} · ${a.improved ? "优于默认" : "使用默认参数"}
        </div>
      </div>`;
    });
    $("t-analysis").innerHTML = analysisCards.length
      ? `<div class="summary-grid">${analysisCards.join("")}</div>`
      : '<div class="empty">添加持仓后自动分析</div>';
    $("t-signals").innerHTML = table([
      { key: "time", label: "时间", align: "left" },
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "side", label: "方向", format: (v) => v === "buy" ? "买入" : "卖出", cls: (v) => v === "buy" ? "up" : "down" },
      { key: "price", label: "价格", format: (v) => fmt(v) },
      { key: "reason", label: "原因", align: "left" },
    ], d.signals || []);
    $("t-trades").innerHTML = table([
      { key: "time", label: "时间", align: "left" },
      { key: "code", label: "代码", align: "left" },
      { key: "name", label: "名称", align: "left" },
      { key: "side", label: "方向", format: (v) => v === "buy" ? "买入" : "卖出", cls: (v) => v === "buy" ? "up" : "down" },
      { key: "price", label: "价格", format: (v) => fmt(v) },
      { key: "qty", label: "数量", format: (v) => fmt(v, 0) },
    ], d.trades || []);
    if (d.monitoring) {
      setTimeout(loadTState, 5000);
    }
  } catch (e) {
    $("t-holdings").innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function postT(url, body) {
  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    loadTState();
  } catch (e) {
    $("t-meta").textContent = "操作失败";
  }
}

const VIEW_LOADERS = {
  overview: loadOverview,
  pools: loadPools,
  boards: loadBoards,
  hot: loadHot,
  lhb: loadLhb,
  gap: loadGap,
  do_t: loadTState,
  stock: loadStock,
  options: loadOptions,
  health: loadStrategyHealth,
};

const LOADED = {};

function switchView(name, force) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $("view-" + name).classList.add("active");
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === name));
  if (force || !LOADED[name]) {
    LOADED[name] = true;
    VIEW_LOADERS[name]();
  }
}

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => switchView(t.dataset.view)));

$("refresh").addEventListener("click", () => {
  const active = document.querySelector(".tab.active").dataset.view;
  switchView(active, true);
  loadOverview();
});

$("board-load").addEventListener("click", loadBoards);
$("stock-load").addEventListener("click", loadStock);
$("stock-code").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock(); });
$("option-load").addEventListener("click", loadOptions);
$("retrain-btn").addEventListener("click", triggerRetrain);
$("gap-run").addEventListener("click", () => loadGap(true));
$("t-add").addEventListener("click", () => postT("/api/t/holdings", {
  code: $("t-code").value.trim(),
  name: $("t-name").value.trim(),
  cost: parseFloat($("t-cost").value || 0),
  qty: parseInt($("t-qty").value || 0, 10),
}));
$("t-start").addEventListener("click", () => postT("/api/t/start"));
$("t-stop").addEventListener("click", () => postT("/api/t/stop"));
$("t-check").addEventListener("click", () => postT("/api/t/check"));

loadOverview();
loadGap();
refreshRetrainStatus();
setInterval(loadOverview, 120000);
