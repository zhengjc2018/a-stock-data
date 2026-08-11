const $ = (id) => document.getElementById(id);

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)))
  ? "--" : Number(v).toFixed(d);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const signed = (v, d = 2) => (v === null || v === undefined ? "--" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);

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
      <div class="nm">${q.name || code}</div>
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
  const box = $("flow");
  if (!flow || !flow.rows || !flow.rows.length) {
    box.innerHTML = '<div class="empty">板块资金流暂不可用</div>';
    return;
  }
  const rows = flow.rows.map((r) => `
    <tr>
      <td>${r.rank}</td>
      <td class="nm">${r.name}</td>
      <td class="${cls(r.change_pct)}">${signed(r.change_pct)}%</td>
      <td class="${cls(r.main_net)}">${fmt(r.main_net / 1e8, 1)}亿</td>
      <td class="hide-sm ${cls(r.main_pct)}">${fmt(r.main_pct)}%</td>
      <td class="hide-sm lead" title="${r.leader || ""}">${r.leader || "--"}</td>
    </tr>`).join("");
  box.innerHTML = `
    <table class="flow-table">
      <thead><tr><th>#</th><th>行业</th><th>涨跌幅</th><th>主力净额</th><th class="hide-sm">净占比</th><th class="hide-sm">领涨股</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
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
    box.innerHTML = `<div class="empty">计算失败：${payload.last_err}<br><button style="margin-top:12px" onclick="loadGap(true)">重新计算</button></div>`;
    return;
  }
  if (!data || !data.candidates || !data.candidates.length) {
    setStatus("暂无推荐", "err");
    meta.textContent = data ? `扫描完成，硬过滤后无候选（${data.date}）` : "暂无数据";
    box.innerHTML = '<div class="empty">当前没有符合条件的次日高开候选</div>';
    return;
  }
  setStatus("已连接", "ok");
  const ranking = data.ranking === "model" ? "模型概率排序" : "规则评分排序";
  meta.textContent = `${data.date} · ${ranking} · 候选 ${data.total} 只 · 计算耗时 ${data.elapsed_sec}s`;
  const rows = data.candidates.map((c, i) => {
    const rankCls = i === 0 ? "top1" : i === 1 ? "top2" : i === 2 ? "top3" : "";
    const prob = c.prob !== null && c.prob !== undefined && !Number.isNaN(c.prob)
      ? (c.prob * 100).toFixed(1) + "%" : "--";
    return `
      <tr>
        <td><span class="rank ${rankCls}">${i + 1}</span></td>
        <td>${c.code}</td>
        <td class="nm">${c.name}</td>
        <td class="hide-sm">${c.industry || "--"}</td>
        <td>${fmt(c.price)}</td>
        <td class="${cls(c.change_pct)}">${signed(c.change_pct)}%</td>
        <td>${prob}</td>
        <td class="reason" title="${c.reason || ""}">${c.reason || "--"}</td>
      </tr>`;
  }).join("");
  box.innerHTML = `
    <table class="gap-table">
      <thead><tr>
        <th>#</th><th>代码</th><th>名称</th><th class="hide-sm">行业</th>
        <th>现价</th><th>今日</th><th>高开概率</th><th>入选理由</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function loadOverview() {
  try {
    const r = await fetch("/api/overview");
    const d = await r.json();
    setUpdated(d.ts);
    $("overview-time").textContent = new Date(d.ts * 1000).toLocaleString("zh-CN", { hour12: false });
    renderIndices(d.indices);
    renderChips(d.sentiment);
    renderFlow(d.board_flow);
    if (d.error) setStatus("部分接口异常", "err");
  } catch (e) {
    setStatus("连接失败", "err");
  }
}

async function loadGap(force) {
  if (force) {
    try { await fetch("/api/gap/refresh", { method: "POST" }); } catch (e) {}
  }
  try {
    const r = await fetch("/api/gap");
    renderGap(await r.json());
  } catch (e) {
    setStatus("连接失败", "err");
  }
}

$("refresh").addEventListener("click", () => {
  loadOverview();
  loadGap(true);
});

loadOverview();
loadGap();
setInterval(loadOverview, 120000);
