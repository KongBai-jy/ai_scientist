/* ============================================================
   AI Scientist 科研报告页（纯前端渲染）
   数据来源：GET /api/snapshots（完全按后端接口，无额外后端依赖）
   支持：?round=V1|V2|V3 指定轮次，Markdown 下载，打印/PDF
   ============================================================ */

const $ = s => document.querySelector(s);
const ROUNDS = ["V1", "V2", "V3"];
const DIM_LABELS = {
  evidence: "证据支撑度", falsifiability: "可证伪性", consistency: "理论一致性",
  novelty: "新颖度", cross_domain: "跨域适配度",
};
const DIM_ORDER = ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"];

function esc(v = "") {
  return String(v).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
}
function fmtTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtScore(v, digits = 2) {
  return v == null ? "--" : Number(v).toFixed(digits);
}
function list(items, fallback = "暂无。") {
  return items?.length ? `<ul>${items.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>${fallback}</p>`;
}

/* ---------- 与主页面一致的 Markdown 渲染 ---------- */
function md(src) {
  // LLM 偶尔把换行写成字面量 \n（JSON 双重转义），先还原成真实换行
  const lines = String(src || "").replace(/\\n/g, "\n").split(/\r?\n/);
  let html = "";
  let listType = null;
  const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
  const inline = t => esc(t)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*$/.test(line)) { closeList(); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    const ul = line.match(/^[-*]\s+(.*)$/);
    if (ul) { if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; } html += `<li>${inline(ul[1])}</li>`; continue; }
    const ol = line.match(/^\d+[.、)]\s+(.*)$/);
    if (ol) { if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; } html += `<li>${inline(ol[1])}</li>`; continue; }
    const bq = line.match(/^>\s?(.*)$/);
    if (bq) { closeList(); html += `<blockquote>${inline(bq[1])}</blockquote>`; continue; }
    closeList();
    html += `<p>${inline(line)}</p>`;
  }
  closeList();
  return html || "<p>（暂无内容）</p>";
}

let state = { snapshots: [], current: null };

/* 历史项目数据源：与主页面共享 localStorage 档案（?question= 指定项目） */
const HISTORY_KEY = "ai_scientist.history.v1";
async function loadSnapshots() {
  const params = new URLSearchParams(location.search);
  const qParam = params.get("question");
  const pidParam = params.get("project_id");

  // 服务端项目（?project_id=）：直接从后端按项目读取快照
  if (pidParam) {
    const res = await fetch(`/api/snapshots?project_id=${encodeURIComponent(pidParam)}`);
    if (!res.ok) throw new Error("快照读取失败，请确认后端服务已启动");
    const body = await res.json();
    const items = (body && body.data) || [];
    items.sort((a, b) => ROUNDS.indexOf(a.round) - ROUNDS.indexOf(b.round));
    if (items.length) return items;
    // 后端无数据时回退到本地档案（?question= 路径）
  }

  // 历史项目：优先用本地 localStorage 档案
  if (qParam) {
    try {
      const store = JSON.parse(localStorage.getItem(HISTORY_KEY)) || {};
      const proj = store[pidParam] || store[qParam] || Object.values(store).find(p => p?.question === qParam);
      if (proj && proj.rounds) {
        const items = Object.values(proj.rounds).filter(s => s && s.round);
        items.sort((a, b) => ROUNDS.indexOf(a.round) - ROUNDS.indexOf(b.round));
        if (items.length) return items;
      }
    } catch { /* 本地无此项目，回退服务端 */ }
  }
  const res = await fetch("/api/snapshots");
  if (!res.ok) throw new Error("快照读取失败，请确认后端服务已启动");
  const body = await res.json();
  const items = (body && body.data) || [];
  items.sort((a, b) => ROUNDS.indexOf(a.round) - ROUNDS.indexOf(b.round));
  return items;
}

function render(snap, all) {
  const e = snap.agent_explorer || {}, s = snap.agent_scientist || {}, c = snap.agent_critic || {};
  const scores = c.scores || {};
  document.title = `AI Scientist 报告 · ${snap.round}`;
  $("#paper").innerHTML = `
  <div class="cover">
    <div class="brand">AI Scientist</div>
    <h1>科研报告</h1>
    <div class="subtitle">${snap.round} · ${esc(snap.question)}</div>
    <div class="meta">
      <div><span>综合得分</span><b>${fmtScore(snap.overall_score)}</b></div>
      <div><span>颗粒度得分</span><b>${fmtScore(snap.granularity_score)}</b></div>
      <div><span>快照时间</span><b>${fmtTime(snap.timestamp)}</b></div>
    </div>
  </div>

  <section>
    <h2>研究问题</h2>
    <h3>${esc(snap.question)}</h3>
  </section>

  <section>
    <h2>一、探索者 · 证据与类比</h2>
    <h3>问题骨架</h3>
    <p>${esc(e.problem_skelton || "")}</p>
    <h3>证据列表</h3>
    ${(e.evidence_list || []).filter(ev => ev.source && String(ev.source).trim()).length
      ? `<table class="evidence"><thead><tr><th>证据陈述</th><th>来源</th><th>年份</th></tr></thead><tbody>${e.evidence_list.filter(ev => ev.source && String(ev.source).trim()).map(ev => `<tr>
          <td>${esc(ev.claim)}</td><td>${esc(ev.source || "未知")}</td>
          <td>${ev.year && /^\d{4}$/.test(String(ev.year)) ? esc(ev.year) : "—"}</td>
        </tr>`).join("")}</tbody></table>`
      : "<p>未检索到相关文献证据。</p>"}
    <h3>知识缺口</h3>
    ${list(e.knowledge_gaps, "暂无识别到知识缺口。")}
    <h3>跨域类比</h3>
    ${(e.analogies || []).length
      ? `<div class="analogies">${e.analogies.map(a => `<div class="analogy"><b>【${esc(a.field || "未知领域")}】${esc(a.phenomenon || "")}</b><p>映射关系：${esc(a.mapping_relation || "")}</p></div>`).join("")}</div>`
      : "<p>暂无跨域类比。</p>"}
  </section>

  <section>
    <h2>二、科学家 · 候选假设</h2>
    ${(s.hypotheses || []).map(h => `
    <div class="hypothesis">
      <div class="hypo-head"><strong>${esc(h.id)}</strong><span>${esc(h.source || "")}</span></div>
      <blockquote>${esc(h.statement || "")}</blockquote>
      <p><b>推论逻辑：</b>${esc(h.supporting_reasoning || "")}</p>
      <p class="falsify"><b>可证伪条件：</b>${esc(h.falsification_condition || "")}</p>
      <div class="plan">
        <p><b>L1 概念方向：</b>${esc((h.plan || {}).L1_conceptual || "")}</p>
        <p><b>L2 量化指标：</b>${esc((h.plan || {}).L2_quantitative || "")}</p>
        <p><b>L3 容错方案：</b>${esc((h.plan || {}).L3_robustness || "")}</p>
      </div>
      <div class="vc">
        <p class="vc-confirm"><b>✓ 成立条件：</b>${esc((h.verification_criteria || {}).confirm || "")}</p>
        <p class="vc-reject"><b>✗ 推翻条件：</b>${esc((h.verification_criteria || {}).reject || "")}</p>
      </div>
    </div>`).join("")}
    <h3>假设间对比</h3>
    <p>${esc(s.cross_hypothesis_comparison || "")}</p>
  </section>

  <section>
    <h2>三、评审官 · 五维评审</h2>
    <div class="score-table">
      ${DIM_ORDER.map(d => `<div class="score-row"><span>${DIM_LABELS[d]}</span><div class="bar"><i style="width:${(scores[d] || 0) * 10}%"></i></div><b>${fmtScore(scores[d], 1)}</b></div>`).join("")}
    </div>
    <h3>致命缺陷</h3>
    <p class="flaw">${esc(c.top_flaw || "")}</p>
    <h3>反事实攻击</h3>
    <p>${esc(c.counterfactual || "")}</p>
    <h3>缺失证据</h3>
    ${list(c.missing_evidences, "暂无缺失证据。")}
    <h3>详细评审意见</h3>
    <div class="md">${md(c.detailed_review)}</div>
  </section>

  ${all.length > 1 ? `
  <section>
    <h2>四、迭代历程</h2>
    <div class="rounds-history">
      ${all.map(r => `<div class="rh-item"><span>${esc(r.round)}</span><b>${fmtScore(r.overall_score)}</b><small>${fmtTime(r.timestamp)}</small></div>`).join("")}
    </div>
  </section>` : ""}

  <section>
    <h2>专家反馈记录</h2>
    ${(snap.human_feedback || []).filter(f => f && f.content).length
      ? snap.human_feedback.filter(f => f && f.content).map(f => `<div class="feedback"><b>${esc(f.content)}</b></div>`).join("")
      : "<p>本轮无专家反馈（V1 首次运行）。</p>"}
  </section>

  <div class="footer-note">
    AI Scientist 自动生成 · 基座模型 Qwen · 多智能体协作（Explorer / Scientist / Critic）
  </div>`;
}

function snapshotToMarkdown(snap, allRounds) {
  const e = snap.agent_explorer || {}, s = snap.agent_scientist || {}, c = snap.agent_critic || {};
  const L = [];
  L.push(`# AI Scientist 科研报告 · ${snap.round}`, "");
  L.push(`> 生成时间：${fmtTime(snap.timestamp)}　|　综合得分：**${fmtScore(snap.overall_score)}**　|　颗粒度得分：${fmtScore(snap.granularity_score)}`, "");
  L.push(`## 研究问题`, "", snap.question, "");
  L.push(`## 一、探索者（Explorer）`, "");
  L.push(`### 问题骨架`, "", e.problem_skelton || "（未提供）", "");
  L.push(`### 证据列表`, "");
  const mdEvidence = (e.evidence_list || []).filter(ev => ev.source && String(ev.source).trim());
  if (mdEvidence.length) {
    mdEvidence.forEach(ev => L.push(`- ${ev.claim}（来源：${ev.source || "未知"}${ev.year && /^\d{4}$/.test(String(ev.year)) ? `, ${ev.year}` : ""}）`));
  } else {
    L.push("- （无证据）");
  }
  L.push("");
  L.push(`### 知识缺口`, "");
  (e.knowledge_gaps || []).forEach(g => L.push(`- ${g}`));
  if (!(e.knowledge_gaps || []).length) L.push("- 暂无");
  L.push("");
  L.push(`### 跨域类比`, "");
  (e.analogies || []).forEach(a => L.push(`- 【${a.field || "未知领域"}】${a.phenomenon || ""} —— 映射：${a.mapping_relation || ""}`));
  if (!(e.analogies || []).length) L.push("- 暂无");
  L.push("");
  L.push(`## 二、科学家（Scientist）`, "");
  (s.hypotheses || []).forEach(h => {
    L.push(`### ${h.id}　${h.statement || ""}`, "");
    L.push(`- **来源依据**：${h.source || ""}`);
    L.push(`- **推论逻辑**：${h.supporting_reasoning || ""}`);
    L.push(`- **可证伪条件**：${h.falsification_condition || ""}`);
    L.push(`- **L1 概念方向**：${(h.plan || {}).L1_conceptual || ""}`);
    L.push(`- **L2 量化指标**：${(h.plan || {}).L2_quantitative || ""}`);
    L.push(`- **L3 容错方案**：${(h.plan || {}).L3_robustness || ""}`);
    L.push(`- **成立条件**：${(h.verification_criteria || {}).confirm || ""}`);
    L.push(`- **推翻条件**：${(h.verification_criteria || {}).reject || ""}`, "");
  });
  L.push(`### 假设间对比`, "", s.cross_hypothesis_comparison || "（未提供）", "");
  L.push(`## 三、评审官（Critic）`, "");
  const sc = c.scores || {};
  L.push(`| 维度 | 评分 |`, `| --- | --- |`);
  DIM_ORDER.forEach(d => L.push(`| ${DIM_LABELS[d]} | ${fmtScore(sc[d], 1)} |`));
  L.push("");
  L.push(`### 致命缺陷`, "", c.top_flaw || "（未提供）", "");
  L.push(`### 反事实攻击`, "", c.counterfactual || "（未提供）", "");
  L.push(`### 缺失证据`, "");
  (c.missing_evidences || []).forEach(m => L.push(`- ${m}`));
  if (!(c.missing_evidences || []).length) L.push("- 暂无");
  L.push("");
  L.push(`### 详细评审意见`, "", c.detailed_review || "（未提供）", "");
  if (allRounds && allRounds.length > 1) {
    L.push(`## 四、迭代历程`, "", `| 轮次 | 综合得分 | 时间 |`, `| --- | --- | --- |`);
    allRounds.forEach(r => L.push(`| ${r.round} | ${fmtScore(r.overall_score)} | ${fmtTime(r.timestamp)} |`));
    L.push("");
  }
  L.push(`---`, "", `*本报告由 AI Scientist 系统自动生成（基座模型：Qwen，多智能体协作）*`, "");
  return L.join("\n");
}

(async () => {
  try {
    const all = await loadSnapshots();
    if (!all.length) { $("#paper").innerHTML = `<div class="loading">暂无研究快照，请先在研究台完成一轮研究。</div>`; return; }
    state.snapshots = all;
    const param = new URLSearchParams(location.search).get("round");
    const snap = all.find(x => x.round === param) || all[all.length - 1];
    state.current = snap;
    render(snap, all);
  } catch (e) {
    $("#paper").innerHTML = `<div class="loading">${esc(e.message)}</div>`;
  }
})();

$("#printReport").onclick = () => window.print();
$("#downloadMd").onclick = () => {
  if (!state.current) return;
  const text = snapshotToMarkdown(state.current, state.snapshots);
  const blob = new Blob(["﻿" + text], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `ai-scientist-report-${state.current.round}.md`;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
};
