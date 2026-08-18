/* ============================================================
   AI Scientist 前端（原生 JS）
   后端契约：
   - POST /api/run            提交研究任务（后台执行，返回 job_id）
   - POST /api/feedback       提交反馈触发迭代（后台执行，返回 job_id）
   - GET  /api/job/{id}       轮询任务状态（真实阶段 explorer/scientist/critic）
   - POST /api/job/{id}/cancel  取消任务
   - GET  /api/jobs           活跃任务列表（刷新恢复用）
   - GET  /api/snapshots?project_id=   全部轮次快照（按项目）
   - GET  /api/chart/{name}?project_id= 5 个图表接口（按项目）
   - GET  /api/health         健康检查
   ============================================================ */

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

/* ================= 常量 ================= */
const ROUNDS = ["V1", "V2", "V3"];
const API_TIMEOUT_MS = 300000; // 兜底超时
const POLL_INTERVAL_MS = 2000; // 任务状态轮询间隔
const VIEW_KEY = "ai_scientist.view.v1";
const SIDEBAR_KEY = "ai_scientist.sidebar.collapsed.v1";
const DIM_LABELS = {
  evidence: "证据支撑度", falsifiability: "可证伪性", consistency: "理论一致性",
  novelty: "新颖度", cross_domain: "跨域适配度",
};
const DIM_ORDER = ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"];
const SAMPLE_QUESTIONS = [
  { id: 1, category: "高温超导", title: "如何提升 YBCO 体系的超导转变温度(Tc)？", summary: "基于氧含量调控、高压诱导与人工钉扎中心的证据链，探索 CuO2 面结构与 Tc 的关系", q: "关于高温超导材料的研究：如何提升 YBCO 体系的超导转变温度(Tc)？" },
  { id: 2, category: "统计物理", title: "重整化群如何统一描述相变临界行为？", summary: "从 Onsager 二维伊辛模型严格解到 Wilson RG 理论，探索临界指数普适类", q: "重整化群方法如何统一描述不同体系相变的临界行为？" },
  { id: 3, category: "神经科学", title: "神经元雪崩的临界性是否优化大脑信息处理？", summary: "基于 Beggs & Plenz 的功率律雪崩证据，探索临界态与计算能力的关系", q: "神经元雪崩现象中的临界性如何支撑大脑信息处理的最优化？" },
  { id: 4, category: "复杂网络", title: "小世界拓扑如何加速疾病传播与信息扩散？", summary: "基于 Watts & Strogatz 小世界模型，探索网络拓扑对传播动力学的影响", q: "小世界网络拓扑如何加速流行病传播？如何设计有效的干预策略？" },
  { id: 5, category: "因果推断", title: "如何从观测数据中稳健识别因果效应？", summary: "基于 Pearl 因果框架与 do-calculus，探索混杂因素控制与反事实推理", q: "如何从观测数据中稳健地识别因果效应并排除混杂因素？" },
  { id: 6, category: "高温超导", title: "BaZrO3 掺杂如何提升 YBCO 临界电流密度？", summary: "人工钉扎中心对磁通运动的抑制作用及其对 Tc 的副作用边界", q: "在 YBCO 中引入 BaZrO3 人工钉扎中心，如何在提升临界电流密度的同时保持超导转变温度？" },
  { id: 7, category: "统计物理", title: "Onsager 严格解如何约束二维相变理论？", summary: "二维伊辛模型严格解与相变普适类的关系", q: "Onsager 对二维伊辛模型的严格解如何约束二维体系的相变理论？" },
  { id: 8, category: "因果推断", title: "后门准则如何指导调整集选择？", summary: "Pearl 因果图模型中的后门准则与混杂因素控制", q: "Pearl 因果图模型中的后门准则如何指导调整集的选择以消除混杂偏差？" },
];

/* ================= 状态 ================= */
let snapshots = {};            // {V1: snapshot, ...}（当前查看项目的轮次）
let currentRound = null;       // 当前查看的轮次
let appStatus = "idle";        // idle | loading | ready | error（当前查看项目的状态）
let currentQuestion = "";
let chartsRendered = false;
let loadingStageTimer = null;  // loading 卡片计时器（仅剩余秒表）
let jobs = {};                 // {project_id: {job_id, project_id, question, round, feedback, status, stage, progress}}
let currentJobPid = null;      // 当前查看的 loading 卡片对应的 project_id
let pollTimers = {};           // {project_id: timerId}
let pollInFlight = {};         // {project_id: boolean}，避免慢请求造成重叠轮询
const chartInstances = new Map();
let chartResizeBound = false;

/* ---------- 历史项目（localStorage 持久化，以 project_id 独立归档） ---------- */
let historyStore = {};         // {project_id: {question, rounds: {V1: snap,...}, updatedAt, running}}
let currentProjectKey = null;  // 当前查看项目的 project_id
const HISTORY_KEY = "ai_scientist.history.v1";
const DELETED_KEY = "ai_scientist.deleted.v1";
let deletedKeys = new Set(); // 被用户删除的 project_id「墓碑」：刷新时不再从后端重新导入
function snapshotProjectId(snap) {
  return snap?.project_id || `legacy:${snap?.question || "unknown"}`;
}
function isBackendProjectId(projectId) {
  return !!projectId && !String(projectId).startsWith("legacy:");
}
function loadHistoryStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY)) || {};
    const migrated = {};
    Object.entries(raw).forEach(([key, value]) => {
      if (!value || typeof value !== "object") return;
      const firstSnap = Object.values(value.rounds || {})[0];
      const pid = value.project_id || firstSnap?.project_id || (key.startsWith("legacy:") ? key : `legacy:${key}`);
      migrated[pid] = { ...value, project_id: pid, question: value.question || firstSnap?.question || key.replace(/^legacy:/, "") };
    });
    return migrated;
  } catch { return {}; }
}
function saveHistoryStore() {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(historyStore)); } catch { /* 超容量时静默 */ }
}
function loadDeletedKeys() {
  try { return new Set(JSON.parse(localStorage.getItem(DELETED_KEY)) || []); } catch { return new Set(); }
}
function saveDeletedKeys() {
  try { localStorage.setItem(DELETED_KEY, JSON.stringify([...deletedKeys])); } catch { /* 静默 */ }
}
function upsertSnapshot(snap) {
  if (!snap || !snap.round || !snap.question) return;
  const pid = snapshotProjectId(snap);
  const proj = historyStore[pid] || (historyStore[pid] = { project_id: pid, question: snap.question, rounds: {}, updatedAt: "" });
  proj.question = snap.question;
  proj.rounds[snap.round] = snap;
  proj.updatedAt = snap.timestamp || "";
  proj.running = false; // 有轮次数据说明已不再处于「研究中」状态
  proj.project_id = pid;
  saveHistoryStore();
}
function cleanupRunningProject(projectId, restore = null) {
  const proj = historyStore[projectId];
  if (proj && !Object.keys(proj.rounds || {}).length) {
    // 失败/取消且没有任何轮次数据：移除占位条目；若同问题原本有历史数据则恢复
    if (restore && Object.keys(restore.rounds || {}).length) historyStore[projectId] = restore;
    else delete historyStore[projectId];
    saveHistoryStore();
    renderHistoryList();
  }
}
function projectList() {
  return Object.entries(historyStore)
    .map(([project_id, p]) => ({ project_id, question: p.question || "未命名研究", rounds: p.rounds || {}, updatedAt: p.updatedAt, running: !!p.running }))
    .sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
}
function latestRoundOf(proj) {
  return ROUNDS.filter(r => proj.rounds[r]).pop();
}
function generateProjectId() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return "p-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}
function saveViewKey() { try { localStorage.setItem(VIEW_KEY, currentProjectKey || ""); } catch { /* 静默 */ } }
function loadViewKey() { try { return localStorage.getItem(VIEW_KEY) || null; } catch { return null; } }
function setSidebarCollapsed(collapsed) {
  const isCollapsed = !!collapsed;
  document.querySelector(".app").classList.toggle("sidebar-collapsed", isCollapsed);
  const btn = $("#sidebarToggle");
  btn.setAttribute("aria-expanded", String(!isCollapsed));
  btn.setAttribute("aria-label", isCollapsed ? "显示研究档案" : "隐藏研究档案");
  btn.title = isCollapsed ? "显示侧边栏" : "隐藏侧边栏";
  try { localStorage.setItem(SIDEBAR_KEY, isCollapsed ? "1" : "0"); } catch { /* 静默 */ }
}
function loadSidebarCollapsed() {
  try { return localStorage.getItem(SIDEBAR_KEY) === "1"; } catch { return false; }
}

/* ================= 工具函数 ================= */
function esc(text = "") {
  return String(text).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
}
function motionEnabled() {
  return !!window.gsap && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
function animateIn(element, { y = 10, duration = .42, delay = 0 } = {}) {
  if (!motionEnabled() || !element) return;
  window.gsap.fromTo(element, { autoAlpha: 0, y }, { autoAlpha: 1, y: 0, duration, delay, ease: "power3.out", clearProps: "transform,opacity,visibility" });
  // 兜底：动画被浏览器节流或未播放时，强制显示内容（交互不能依赖动画）
  setTimeout(() => {
    if (element && element.isConnected && window.gsap) window.gsap.set(element, { clearProps: "all" });
  }, 1600 + delay * 1000);
}
function fmtTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function fmtScore(v, digits = 2) {
  return v == null ? "--" : Number(v).toFixed(digits);
}
function evidenceYear(evidence) {
  const direct = String(evidence?.year || "").match(/\b(?:18|19|20|21)\d{2}\b/);
  if (direct) return direct[0];
  const fromSource = String(evidence?.source || "").match(/\b(?:18|19|20|21)\d{2}\b/);
  return fromSource ? fromSource[0] : "";
}

/* ---------- 极简 Markdown 渲染（详细评审用） ---------- */
function md(src) {
  // LLM 偶尔把换行写成字面量 \n（JSON 双重转义），先还原成真实换行
  const lines = String(src || "").replace(/\\n/g, "\n").split(/\r?\n/);
  let html = "";
  let listType = null; // "ul" | "ol"
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

/* ================= API 层 ================= */
async function apiFetch(url, options = {}, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const externalSignal = options.signal || null;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
      signal: externalSignal && window.AbortSignal?.any
        ? window.AbortSignal.any([controller.signal, externalSignal])
        : (externalSignal || controller.signal),
    });
    let body = null;
    try { body = await res.json(); } catch { /* 非 JSON 响应 */ }
    if (!res.ok) {
      let detail = "请求失败";
      if (body && body.detail) {
        // FastAPI 422 校验错误 detail 是数组；普通错误是字符串
        detail = Array.isArray(body.detail)
          ? body.detail.map(d => `${(d.loc || []).join(".") || "字段"} ${d.msg || ""}`).join("；")
          : String(body.detail);
      } else if (res.status === 400) {
        detail = "已达到最大迭代次数（V3），无法继续迭代";
      }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return body;
  } catch (e) {
    if (e.name === "AbortError") {
      const err = new Error("请求超时，请稍后重试");
      err.aborted = true;
      throw err;
    }
    if (e instanceof TypeError) {
      throw new Error("网络连接失败，请检查后端服务是否已启动");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
/* 提交任务：后台执行，立即返回 job_id / project_id / round_label */
async function apiCreateJob(question, feedback, round, projectId) {
  if (feedback) {
    return apiFetch("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ question, feedback, current_round: round, project_id: projectId }),
    }, 15000);
  }
  return apiFetch("/api/run", {
    method: "POST",
    body: JSON.stringify({ question, initial_round: round, project_id: projectId }),
  }, 15000);
}
async function apiJobStatus(jobId) {
  return apiFetch(`/api/job/${jobId}`, {}, 8000);
}
async function apiCancelJob(jobId) {
  return apiFetch(`/api/job/${jobId}/cancel`, { method: "POST" }, 8000);
}
async function apiJobs() {
  return apiFetch("/api/jobs", {}, 8000);
}

/* ================= 后台任务轮询（真实阶段） ================= */
function startPolling(job) {
  const pid = job.project_id;
  stopPolling(pid);
  const tick = async () => {
    if (!jobs[pid] || !["queued", "running"].includes(jobs[pid].status)) return;
    await pollJob(pid, job.job_id);
    if (jobs[pid] && ["queued", "running"].includes(jobs[pid].status)) {
      pollTimers[pid] = setTimeout(tick, POLL_INTERVAL_MS);
    }
  };
  tick(); // 立即先查一次；本次完成后才安排下一次，避免请求重叠
}
function stopPolling(pid) {
  if (pollTimers[pid]) { clearTimeout(pollTimers[pid]); delete pollTimers[pid]; }
  delete pollInFlight[pid];
}
async function pollJob(pid, jobId) {
  if (pollInFlight[pid]) return;
  pollInFlight[pid] = true;
  try {
    const res = await apiJobStatus(jobId);
    if (res && res.data) applyJobStatus(res.data);
  } catch (e) {
    if (e.status === 404) {
      // 服务重启导致任务丢失：停轮询并清理 running 占位
      stopPolling(pid);
      const q = jobs[pid]?.question;
      delete jobs[pid];
      if (historyStore[pid]) {
        historyStore[pid].running = false;
        cleanupRunningProject(pid);
      }
      if (currentJobPid === pid) { currentJobPid = null; setStopBtn(false); }
      renderHistoryList();
    }
    // 其余网络错误忽略，等待下一次轮询
  } finally {
    delete pollInFlight[pid];
  }
}
function applyJobStatus(d) {
  if (!d) return;
  const pid = d.project_id;
  const proj = historyStore[pid];
  jobs[pid] = { ...(jobs[pid] || {}), ...d, question: d.question, round: d.round_label || (jobs[pid] || {}).round };
  if (proj) {
    proj.project_id = pid;
    proj.round = d.round_label || proj.round;
    proj.running = d.status === "running" || d.status === "queued";
  }
  if (d.status === "done") {
    onJobFinished(d);
  } else if (d.status === "error") {
    onJobError(d);
  } else if (d.status === "cancelled") {
    onJobCancelled(d);
  } else {
    // queued / running：若当前查看该项目，用真实阶段刷新 loading 卡片
    if (currentProjectKey === pid) updateLoadingStage(d);
    renderHistoryList();
  }
}

/* ================= 加载动画（真实阶段 + 秒表） ================= */
function setStopBtn(visible) {
  const btn = $("#stopBtn");
  btn.style.display = visible ? "" : "none";
  btn.disabled = !visible;
}
function showLoading(roundLabel, projectId, job) {
  disposeAllCharts();
  appStatus = "loading";
  currentJobPid = projectId || null;
  setWorkspaceMode(true);
  setStopBtn(true);
  setComposerEnabled(false, "loading");
  // 顶栏跟随当前研究（loading 期间也显示问题，避免停留在欢迎占位）
  $("#topQid").textContent = roundLabel || "AI";
  $("#topTitle").textContent = currentQuestion || "正在生成研究…";
  $("#topCategory").style.display = "none";
  $("#reportBtn").style.display = "none";
  const chat = $("#chatInner");
  chat.innerHTML = `
  <div class="loading-wrap" id="loadingWrap">
    <div class="loading-card">
      <p class="loading-title">正在生成 ${esc(roundLabel)}</p>
      <p class="loading-sub">多智能体流水线执行中 · 预计 1-3 分钟</p>
      <div class="stage-list">
        <div class="stage" data-stage="explorer"><div class="s-name"><span class="s-ico">探</span>探索者 Explorer</div><div class="s-desc">向量检索文献 · 提炼问题骨架与证据</div></div>
        <div class="stage" data-stage="scientist"><div class="s-name"><span class="s-ico">科</span>科学家 Scientist</div><div class="s-desc">生成可证伪假设 · 三级实验计划</div></div>
        <div class="stage" data-stage="critic"><div class="s-name"><span class="s-ico">评</span>评审官 Critic</div><div class="s-desc">五维评分 · 反事实攻击 · 缺陷诊断</div></div>
      </div>
      <div class="loading-bar"><i id="loadingBarFill"></i></div>
      <div class="loading-meta"><span id="loadingStageText">流水线启动中…</span><span id="loadingElapsed">00:00</span></div>
    </div>
  </div>`;
  animateIn($("#loadingWrap"), { y: 16, duration: .45 });

  const startedAt = Date.now();
  const elapsedEl = $("#loadingElapsed");
  stopLoadingAnim();
  loadingStageTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - startedAt) / 1000);
    elapsedEl.textContent = `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
  }, 1000);

  // 应用已知阶段（排队中或已有真实 stage）
  updateLoadingStage(job || { status: "queued" });
}
function updateLoadingStage(d) {
  const chat = $("#chatInner");
  if (!chat) return;
  const stage = d?.stage;
  const order = ["explorer", "scientist", "critic"];
  let activeIdx = -1;
  let text = "流水线启动中…";
  let pct = 4;
  if (d?.status === "queued") {
    text = "排队等待中…（最多 2 个研究并行执行）";
    pct = 4;
  } else if (stage) {
    activeIdx = order.indexOf(stage);
    text = { explorer: "探索者 文献挖掘中…", scientist: "科学家 生成假设中…", critic: "评审官 评审中…" }[stage] || "流水线执行中…";
    pct = stage === "explorer" ? 20 : stage === "scientist" ? 55 : 85;
  }
  order.forEach((k, i) => {
    const el = chat.querySelector(`[data-stage="${k}"]`);
    if (!el) return;
    el.classList.toggle("running", i === activeIdx);
    el.classList.toggle("done", i < activeIdx);
  });
  const stageText = $("#loadingStageText");
  if (stageText) stageText.textContent = text;
  const barFill = $("#loadingBarFill");
  if (barFill) {
    if (motionEnabled()) window.gsap.to(barFill, { width: `${pct}%`, duration: .6, ease: "power2.out" });
    else barFill.style.width = `${pct}%`;
  }
}
function stopLoadingAnim() {
  if (loadingStageTimer) { clearInterval(loadingStageTimer); loadingStageTimer = null; }
}

/* ================= 工作台渲染 ================= */
function setWorkspaceMode(active) {
  $(".app").classList.toggle("has-research", !!active);
  if (motionEnabled() && active) {
    const composer = $(".composer-wrap");
    window.gsap.fromTo(composer, { autoAlpha: 0, y: 18 }, { autoAlpha: 1, y: 0, duration: .42, ease: "power3.out", clearProps: "transform,opacity,visibility" });
    // 兜底：动画未播放时强制显示输入区
    setTimeout(() => { if (window.gsap) window.gsap.set(composer, { clearProps: "all" }); }, 1500);
  }
}
function setComposerEnabled(enabled, mode = "feedback") {
  $("#expertInput").disabled = !enabled;
  $("#sendBtn").disabled = !enabled;
  $("#composerBox").classList.toggle("needs-human", mode === "v3-max" || mode === "readonly");
  const prompt = $("#composerPrompt");
  if (mode === "loading") {
    prompt.textContent = "PIPELINE RUNNING";
    $("#expertInput").placeholder = "多智能体流水线执行中，请稍候…";
    $("#composerHint").textContent = "Explorer → Scientist → Critic · 预计 1-3 分钟";
  } else if (mode === "readonly") {
    prompt.textContent = "HISTORY PROJECT";
    $("#expertInput").placeholder = "历史项目（只读）—— 该项目数据保存在本地，如需继续迭代请开始新研究…";
    $("#composerHint").textContent = "当前查看的是历史研究档案，仅支持查看与导出";
  } else if (mode === "v3-max") {
    prompt.textContent = "MAX ROUND REACHED";
    $("#expertInput").placeholder = "已达到最大迭代次数 V3，无法继续迭代…";
    $("#composerHint").textContent = "V3 为最后一轮；如需继续研究请点击左侧「新建研究」";
  } else if (mode === "feedback") {
    prompt.textContent = "EXPERT INPUT";
    $("#expertInput").placeholder = "向 AI Scientist 提出专家意见（≥3 字），触发下一轮迭代…";
    $("#composerHint").textContent = "专家反馈将触发下一轮全链路迭代（V1 → V2 → V3）";
  }
}
function updateTopbar() {
  $("#topQid").textContent = currentRound || "AI";
  $("#topTitle").textContent = currentQuestion || "输入一个科学问题开始研究";
  const tag = $("#topCategory");
  if (currentRound && snapshots[currentRound]) {
    tag.style.display = "";
    tag.textContent = `${currentRound} · 综合 ${fmtScore(snapshots[currentRound].overall_score)}`;
  } else {
    tag.style.display = "none";
  }
  $("#reportBtn").style.display = currentRound ? "" : "none";
}

function renderHistoryList() {
  const q = ($("#historySearch").value || "").trim().toLowerCase();
  const list = $("#historyList");
  const projects = projectList();
  if (!projects.length) {
    list.innerHTML = `<div class="history-empty">暂无历史研究</div>`;
    return;
  }
  const rows = projects.filter(p => !q || p.question.toLowerCase().includes(q));
  if (!rows.length) {
    list.innerHTML = `<div class="history-empty">没有匹配的历史研究</div>`;
    return;
  }
  list.innerHTML = `
    <div class="date-label"><span>本地持久化研究档案</span><span class="history-swipe-hint">← 左滑显示删除</span></div>
    ${rows.map((p, idx) => {
      const lr = latestRoundOf(p);
      const snap = lr ? p.rounds[lr] : null;
      const isActive = p.project_id === currentProjectKey;
      const job = p.project_id ? jobs[p.project_id] : null;
      const isBackend = isBackendProjectId(p.project_id) || (job && ["queued", "running"].includes(job.status));
      const isRunning = (p.running && !lr) || (job && ["queued", "running"].includes(job.status));
      return `<div class="history-item-wrap" data-idx="${idx}">
        <button class="history-item ${isActive ? "active" : ""}">
          <span class="dot ${isRunning ? "needs_human" : isActive ? "running" : "completed"}"></span>
          <b>${esc(p.question.slice(0, 18))}${p.question.length > 18 ? "…" : ""}</b>
          <small>${isRunning ? "研究中 · 流水线执行中…" : snap ? `${lr} · 综合 ${fmtScore(snap.overall_score)}` : "暂无轮次记录"}${isBackend ? " · 服务端" : ""}</small>
        </button>
        <button class="history-del" type="button" title="删除该历史研究" aria-label="删除该历史研究" tabindex="-1">删除</button>
      </div>`;
    }).join("")}`;
  const visible = rows;
  $$(".history-item-wrap").forEach(wrap => {
    const item = visible[Number(wrap.dataset.idx)];
    if (!item) return;
    const historyItem = wrap.querySelector(".history-item");
    const deleteBtn = wrap.querySelector(".history-del");
    bindHistorySwipe(wrap, historyItem, deleteBtn);
    historyItem.onclick = event => {
      if (wrap.dataset.suppressClick === "1") { wrap.dataset.suppressClick = "0"; event.preventDefault(); return; }
      if (wrap.classList.contains("revealed")) { setHistorySwipeOpen(wrap, false); return; }
      closeMobileSidebar(false);
      switchProject(item.project_id);
    };
    deleteBtn.onclick = event => {
      event.stopPropagation();
      setHistorySwipeOpen(wrap, false);
      deleteProject(item.project_id);
    };
  });
}

function setHistorySwipeOpen(wrap, open) {
  if (open) {
    $$(".history-item-wrap.revealed").forEach(other => { if (other !== wrap) setHistorySwipeOpen(other, false); });
  }
  wrap.classList.toggle("revealed", open);
  const deleteBtn = wrap.querySelector(".history-del");
  if (deleteBtn) deleteBtn.tabIndex = open ? 0 : -1;
}

function bindHistorySwipe(wrap, item, deleteBtn) {
  let startX = 0;
  let startY = 0;
  let baseX = 0;
  let currentX = 0;
  let tracking = false;
  let horizontal = false;
  let wheelDistance = 0;
  let wheelResetTimer = null;
  const revealWidth = 66;
  const finish = () => {
    if (!tracking) return;
    tracking = false;
    const pointerId = Number(item.dataset.pointerId);
    if (Number.isFinite(pointerId)) item.releasePointerCapture?.(pointerId);
    item.style.removeProperty("transition");
    item.style.removeProperty("transform");
    if (horizontal) {
      setHistorySwipeOpen(wrap, currentX < -revealWidth * .45);
      wrap.dataset.suppressClick = "1";
      setTimeout(() => { wrap.dataset.suppressClick = "0"; }, 0);
    }
  };
  item.addEventListener("pointerdown", event => {
    if (!event.isPrimary || event.button > 0) return;
    tracking = true;
    horizontal = false;
    startX = event.clientX;
    startY = event.clientY;
    baseX = wrap.classList.contains("revealed") ? -revealWidth : 0;
    currentX = baseX;
    item.dataset.pointerId = String(event.pointerId);
    item.setPointerCapture?.(event.pointerId);
    item.style.transition = "none";
  });
  item.addEventListener("pointermove", event => {
    if (!tracking) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (!horizontal && Math.abs(dx) > 5 && Math.abs(dx) > Math.abs(dy)) horizontal = true;
    if (!horizontal) return;
    currentX = Math.max(-revealWidth, Math.min(0, baseX + dx));
    item.style.transform = `translateX(${currentX}px)`;
  });
  item.addEventListener("pointerup", finish);
  item.addEventListener("pointercancel", finish);
  item.addEventListener("wheel", event => {
    if (Math.abs(event.deltaX) < 4 || Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
    event.preventDefault();
    wheelDistance += event.deltaX;
    clearTimeout(wheelResetTimer);
    wheelResetTimer = setTimeout(() => { wheelDistance = 0; }, 140);
    if (wheelDistance > 18) {
      setHistorySwipeOpen(wrap, true);
      wheelDistance = 0;
    } else if (wheelDistance < -18) {
      setHistorySwipeOpen(wrap, false);
      wheelDistance = 0;
    }
  }, { passive: false });
  deleteBtn.addEventListener("keydown", event => {
    if (event.key === "Escape") { setHistorySwipeOpen(wrap, false); item.focus(); }
  });
}

function renderWorkspace() {
  const snap = snapshots[currentRound];
  if (!snap) return;
  disposeAllCharts();
  const activeJob = currentProjectKey ? jobs[currentProjectKey] : null;
  const isRunning = activeJob && ["queued", "running"].includes(activeJob.status);
  setStopBtn(!!isRunning);
  currentJobPid = isRunning ? currentProjectKey : null;
  setWorkspaceMode(true);
  const chat = $("#chatInner");
  const score = snap.overall_score;
  const scoreCls = score >= 8 ? "#2c716b" : score >= 6 ? "#4fa99e" : "#b27432";
  chat.innerHTML = `
  <div class="workspace" id="workspaceRoot">
    <div class="workspace-head">
      <div>
        <h2>${esc(snap.question)}</h2>
        <p class="ws-question">${fmtTime(snap.timestamp)} · Explorer → Scientist → Critic 全链路快照</p>
      </div>
      <div class="ws-score">
        <div class="big" style="color:${scoreCls}">${fmtScore(score)}</div>
        <div class="lbl"><b>综合得分</b>颗粒度 ${fmtScore(snap.granularity_score)}<br>${(snap.granularity_stats?.L1 ?? 0)} / ${(snap.granularity_stats?.L2 ?? 0)} / ${(snap.granularity_stats?.L3 ?? 0)} (L1/L2/L3)</div>
      </div>
    </div>
    <div class="round-tabs" id="roundTabs">
      ${ROUNDS.filter(r => snapshots[r]).map(r => {
        const s = snapshots[r];
        return `<button class="round-tab ${r === currentRound ? "active" : ""} done" data-round="${r}">
          <span class="rt-dot"></span>${r}
          <span class="rt-score">${fmtScore(s.overall_score)}</span>
        </button>`;
      }).join("")}
    </div>
    <div class="content-tabs" id="contentTabs">
      <button class="content-tab active" data-tab="explorer">探索者 · 证据</button>
      <button class="content-tab" data-tab="scientist">科学家 · 假设</button>
      <button class="content-tab" data-tab="critic">评审官 · 评分</button>
      <button class="content-tab" data-tab="charts">迭代图表</button>
    </div>
    <div id="tabPanels">
      <div class="tab-panel active" data-panel="explorer">${renderExplorer(snap)}</div>
      <div class="tab-panel" data-panel="scientist">${renderScientist(snap)}</div>
      <div class="tab-panel" data-panel="critic">${renderCritic(snap)}</div>
      <div class="tab-panel" data-panel="charts"><div id="chartsContainer">${renderChartsPlaceholder()}</div></div>
    </div>
  </div>`;

  // 轮次切换
  $$("#roundTabs .round-tab").forEach(btn => {
    btn.onclick = () => {
      if (snapshots[btn.dataset.round]) switchRound(btn.dataset.round);
    };
  });
  // Tab 切换
  $$("#contentTabs .content-tab").forEach(btn => {
    btn.onclick = () => switchTab(btn.dataset.tab);
  });
  // 报告按钮
  $("#reportBtn").onclick = () => openReport();
  // 报告 + Markdown 下载入口（评审 Tab 下方也放一份）
  const reportBar = document.createElement("div");
  reportBar.className = "panel-card workspace-report-actions";
  reportBar.style.cssText = "display:flex;gap:8px;align-items:center;justify-content:flex-end;padding:12px 16px";
  reportBar.innerHTML = `
    <span style="margin-right:auto;font-size:11px;color:var(--muted)">本轮快照完整数据已保存（JSON + SQLite）</span>
    <button class="report-secondary" id="inlineReportBtn">查看完整报告</button>
    <button class="report-secondary" id="inlineMdBtn">下载 Markdown</button>`;
  $("#tabPanels").append(reportBar);
  $("#inlineReportBtn").onclick = () => openReport();
  $("#inlineMdBtn").onclick = () => downloadMarkdown();

  updateTopbar();
  renderHistoryList();
  animateIn($("#workspaceRoot"), { y: 14, duration: .5 });
}

function switchRound(round) {
  if (!snapshots[round]) return;
  currentRound = round;
  chartsRendered = false;
  renderWorkspace();
  refreshComposer();
}

/* 切换历史项目：工作台整体切换到该项目的轮次数据 */
function switchProject(projectId) {
  const proj = historyStore[projectId];
  if (!proj) return;
  currentProjectKey = projectId;
  currentQuestion = proj.question;
  const job = proj.project_id ? jobs[proj.project_id] : null;
  const isRunning = (proj.running && !latestRoundOf(proj)) || (job && ["queued", "running"].includes(job.status));
  const lr = latestRoundOf(proj);
  if (isRunning) {
    // 生成中：无论是否已有旧轮次，都恢复该项目自己的进度与取消入口
    snapshots = {};
    currentRound = null;
    chartsRendered = false;
    setWorkspaceMode(true);
    showLoading(job?.round || proj.round || "V1", proj.project_id, job);
    refreshComposer();
    document.querySelector(".chat-wrap").scrollTo({ top: 0, behavior: "auto" });
    saveViewKey();
    return;
  }
  if (!lr) return;
  snapshots = Object.assign({}, proj.rounds);
  currentRound = lr;
  appStatus = "ready";
  chartsRendered = false;
  setWorkspaceMode(true);
  renderWorkspace();
  refreshComposer();
  document.querySelector(".chat-wrap").scrollTo({ top: 0, behavior: "auto" });
  saveViewKey();
}

/* 删除历史项目（同时停轮询/取消后端任务） */
function deleteProject(projectId) {
  const proj = historyStore[projectId];
  if (!proj) return;
  if (!window.confirm(`确定删除该历史研究？\n「${proj.question}」将从本地档案移除，无法恢复。`)) return;
  if (proj.project_id) {
    stopPolling(proj.project_id);
    const job = jobs[proj.project_id];
    if (job && ["queued", "running"].includes(job.status)) apiCancelJob(job.job_id).catch(() => {});
    delete jobs[proj.project_id];
  }
  delete historyStore[projectId];
  deletedKeys.add(projectId);
  saveHistoryStore();
  saveDeletedKeys();
  // 删除的是当前查看的项目：切到最近一个剩余项目，没有则回欢迎页
  if (currentProjectKey === projectId) {
    currentProjectKey = null;
    currentQuestion = "";
    snapshots = {};
    currentRound = null;
    chartsRendered = false;
    currentJobPid = null;
    setStopBtn(false);
    stopLoadingAnim();
    saveViewKey();
    const remaining = projectList().find(p => latestRoundOf(p));
    if (remaining) {
      switchProject(remaining.project_id);
    } else {
      appStatus = "idle";
      setWorkspaceMode(false);
      showWelcomeView();
      updateTopbar();
    }
  }
  renderHistoryList();
}

/* 欢迎视图（首页 / 取消 / 删除最后一个项目时复用） */
function showWelcomeView() {
  $("#chatInner").innerHTML = `<div class="welcome" style="margin:0 auto">
    <h1>让每一个研究假设<br>都经得起推敲。</h1>
    <p class="welcome-copy">输入一个科学问题。多智能体系统将组织证据、提出可验证假设，并完成五维评审与多轮迭代。</p>
    <div class="welcome-actions"><button class="primary-btn" id="welcomeStart2" type="button">开始一项研究 <span aria-hidden="true">→</span></button></div>
  </div>`;
  $("#welcomeStart2").onclick = openQuestionModal;
}

function returnHome() {
  closeMobileSidebar(false);
  stopLoadingAnim();
  currentProjectKey = null;
  currentQuestion = "";
  snapshots = {};
  currentRound = null;
  currentJobPid = null;
  chartsRendered = false;
  appStatus = "idle";
  setStopBtn(false);
  setWorkspaceMode(false);
  showWelcomeView();
  updateTopbar();
  renderHistoryList();
  saveViewKey();
  $("#chatWrap").scrollTo({ top: 0, behavior: "auto" });
}

/* 输入区状态统一收口：按项目运行状态与轮次开放反馈 */
function refreshComposer() {
  const proj = historyStore[currentProjectKey];
  if (!proj) return;
  const job = proj.project_id ? jobs[proj.project_id] : null;
  const isRunning = (proj.running || (job && ["queued", "running"].includes(job.status)));
  if (isRunning) { setComposerEnabled(false, "loading"); return; }
  if (appStatus !== "ready") { setComposerEnabled(false); return; }
  if (!isBackendProjectId(proj.project_id)) { setComposerEnabled(false, "readonly"); return; }
  const lr = latestRoundOf(proj);
  setComposerEnabled(lr !== "V3", lr === "V3" ? "v3-max" : "feedback");
}

function switchTab(tab) {
  $$("#contentTabs .content-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$("#tabPanels .tab-panel").forEach(p => p.classList.toggle("active", p.dataset.panel === tab));
  if (tab === "charts" && !chartsRendered) renderCharts();
  if (tab === "critic") renderCriticRadar();
}

/* Critic Tab 内的五维雷达（面板渲染后才初始化，避免隐藏元素 0 尺寸） */
function renderCriticRadar() {
  const snap = snapshots[currentRound];
  if (!snap || !window.echarts) return;
  const dom = document.getElementById("criticRadar");
  if (!dom) return;
  const scores = (snap.agent_critic || {}).scores || {};
  const values = DIM_ORDER.map(d => scores[d] ?? 0);
  const instance = window.echarts.getInstanceByDom(dom) || window.echarts.init(dom);
  registerChart("criticRadar", instance);
  instance.setOption({
    tooltip: {},
    radar: {
      indicator: DIM_ORDER.map(d => ({ name: DIM_LABELS[d], max: 10 })),
      radius: "62%", center: ["50%", "54%"],
      axisName: { color: "#5c6b68", fontSize: 10 },
      splitArea: { areaStyle: { color: ["rgba(79,169,158,.03)", "rgba(79,169,158,.07)"] } },
      splitLine: { lineStyle: { color: "#dde3da" } },
      axisLine: { lineStyle: { color: "#dde3da" } },
    },
    series: [{
      type: "radar",
      data: [{ name: snap.round, value: values, areaStyle: { opacity: .14 }, lineStyle: { width: 2, color: "#2c716b" }, itemStyle: { color: "#2c716b" } }],
    }],
  }, true);
  instance.resize();
}

/* ================= 四个 Tab 的渲染 ================= */
function renderExplorer(snap) {
  const e = snap.agent_explorer || {};
  const evidence = e.evidence_list || [];
  const gaps = e.knowledge_gaps || [];
  const analogies = e.analogies || [];
  return `
  <div class="panel-card">
    <h3>问题骨架</h3>
    <p>${esc(e.problem_skelton || "（未提供）")}</p>
  </div>
  <div class="panel-card">
    <h3>证据列表 <span style="font-size:10px;color:var(--muted);font-weight:400">来自 Chroma 向量库检索</span></h3>
    ${evidence.length ? `<table class="evidence-table">
      <thead><tr><th>证据陈述</th><th>来源</th></tr></thead>
      <tbody>${evidence.map(ev => `<tr>
        <td>${esc(ev.claim)}</td>
        <td class="src">${esc(ev.source || "未知")}</td>
      </tr>`).join("")}</tbody>
    </table>` : `<div class="empty-state">未检索到相关文献，仅基于跨域类比推理</div>`}
  </div>
  <div class="panel-card">
    <h3 class="red">知识缺口</h3>
    ${gaps.length ? `<div class="gap-list">${gaps.map(g => `<div class="gap-item"><span class="gap-ico">!</span><span>${esc(g)}</span></div>`).join("")}</div>` : `<div class="empty-state">暂无识别到知识缺口</div>`}
  </div>
  <div class="panel-card">
    <h3 class="pink">跨域类比线索</h3>
    ${analogies.length ? `<div class="analogy-wrap">${analogies.map(a => `
      <details class="analogy-item">
        <summary><span class="field-chip">${esc(a.field || "未知领域")}</span><span class="analogy-title">${esc(a.phenomenon || "")}</span></summary>
        <div class="analogy-body"><p><b>映射关系：</b>${esc(a.mapping_relation || "")}</p></div>
      </details>`).join("")}</div>` : `<div class="empty-state">暂无跨域类比</div>`}
  </div>`;
}

function renderScientist(snap) {
  const s = snap.agent_scientist || {};
  const hyps = s.hypotheses || [];
  const cmp = s.cross_hypothesis_comparison || "";
  const fb = (snap.human_feedback || []).filter(f => f && f.content);
  return `
  ${fb.length ? `<div class="panel-card fb-history">
    <h3 class="pink">本轮专家反馈</h3>
    ${fb.map(f => `<div class="fb-item"><span class="fb-ico">💬</span><span>${esc(f.content)}</span></div>`).join("")}
  </div>` : ""}
  <div class="hypo-grid">
    ${hyps.map((h, i) => {
      const tagCls = `hypo-tag h${(h.id || "H1").slice(1) || 1}`;
      return `<article class="hypo-card">
      <header class="hypo-head">
        <div class="hypo-top"><span class="${tagCls}">${esc(h.id)}</span><span class="hypo-src">${esc(h.source || "")}</span></div>
        <h3>${esc(h.statement)}</h3>
      </header>
      <p class="hypo-reason"><b style="color:#3f5553">推论逻辑：</b>${esc(h.supporting_reasoning || "")}</p>
      <div class="hypo-falsify"><b>⚡ 可证伪条件</b>${esc(h.falsification_condition || "")}</div>
      <div class="hypo-plan">
        <div class="plan-step l1"><span class="lv">L1</span><span><b>概念方向：</b>${esc((h.plan || {}).L1_conceptual || "")}</span></div>
        <div class="plan-step l2"><span class="lv">L2</span><span><b>量化指标：</b>${esc((h.plan || {}).L2_quantitative || "")}</span></div>
        <div class="plan-step l3"><span class="lv">L3</span><span><b>容错方案：</b>${esc((h.plan || {}).L3_robustness || "")}</span></div>
      </div>
      <div class="hypo-vc">
        <div class="vc-box confirm"><b>✓ 成立条件</b>${esc((h.verification_criteria || {}).confirm || "")}</div>
        <div class="vc-box reject"><b>✗ 推翻条件</b>${esc((h.verification_criteria || {}).reject || "")}</div>
      </div>
      </article>`;
    }).join("")}
  </div>
  <div class="panel-card compare-card">
    <h3>假设间对比</h3>
    <p>${esc(cmp || "（未提供）")}</p>
  </div>`;
}

function renderCritic(snap) {
  const c = snap.agent_critic || {};
  const scores = c.scores || {};
  const missing = c.missing_evidences || [];
  return `
  <div class="critic-grid">
    <div class="critic-column critic-left">
      <div class="panel-card critic-score" style="margin-bottom:0">
        <h3>五维评分</h3>
        <div id="criticRadar" class="chart-box" style="height:270px"></div>
        <table class="score-table">
          ${DIM_ORDER.map(d => {
            const v = scores[d];
            return `<tr><td>${DIM_LABELS[d]}</td><td>${fmtScore(v, 1)}</td></tr>
            <tr><td colspan="2"><div class="score-bar"><i style="width:${(v || 0) * 10}%"></i></div></td></tr>`;
          }).join("")}
        </table>
      </div>
      <div class="panel-card critic-review" style="margin-bottom:0">
        <h3>详细评审意见</h3>
        <div class="md-body">${md(c.detailed_review)}</div>
      </div>
    </div>
    <div class="critic-side">
      <div class="overall-stat" style="margin-bottom:14px">
        <div class="num">${fmtScore(snap.overall_score)}</div>
        <div class="cap">综合得分（加权）</div>
        <div class="gran">颗粒度 ${fmtScore(snap.granularity_score)}</div>
      </div>
      <div class="panel-card" style="margin-bottom:14px">
        <h3 class="red">致命缺陷</h3>
        <div class="flaw-alert"><span class="fa-ico">⚠</span><span>${esc(c.top_flaw || "（未提供）")}</span></div>
      </div>
      <div class="panel-card" style="margin-bottom:14px">
        <h3 class="pink">反事实攻击</h3>
        <div class="cf-card"><b>COUNTERFACTUAL</b>${esc(c.counterfactual || "（未提供）")}</div>
      </div>
      <div class="panel-card" style="margin-bottom:0">
        <h3 class="red">缺失证据</h3>
        ${missing.length ? `<div class="gap-list">${missing.map(m => `<div class="gap-item"><span class="gap-ico">!</span><span>${esc(m)}</span></div>`).join("")}</div>` : `<div class="empty-state">暂无缺失证据</div>`}
      </div>
    </div>
  </div>`;
}

function renderChartsPlaceholder() {
  return `
  <div class="charts-grid">
    <div class="chart-card"><h3>综合得分趋势 <small>折线</small></h3><div class="chart-box" id="chartOverall"></div></div>
    <div class="chart-card"><h3>五维雷达对比 <small>雷达</small></h3><div class="chart-box" id="chartRadarAll"></div></div>
    <div class="chart-card"><h3>计划颗粒度 <small>堆叠柱</small></h3><div class="chart-box" id="chartGranularity"></div></div>
    <div class="chart-card"><h3>缺陷修复瀑布 <small>瀑布</small></h3><div class="chart-box" id="chartWaterfall"></div></div>
    <div class="chart-card wide"><h3>反事实风险收敛 <small>折线</small></h3><div class="chart-box" id="chartRisk"></div></div>
  </div>`;
}

/* ================= 图表渲染（懒加载，进入 Tab 时请求接口） ================= */
/* 迭代图表跟随当前查看轮次截断：看 V1 只显示 V1，看 V2 显示 V1+V2，看 V3 才显示三轮对比 */
function roundsUpToCurrent() {
  const idx = ROUNDS.indexOf(currentRound);
  return ROUNDS.slice(0, idx < 0 ? 1 : idx + 1);
}
function clipByRounds(xAxis, pick) {
  const upto = roundsUpToCurrent();
  const keep = (xAxis || []).map((r, i) => (upto.includes(r) ? i : -1)).filter(i => i >= 0);
  return { xAxis: (xAxis || []).filter(r => upto.includes(r)), keep };
}
function clipOverall(d) {
  const { xAxis, keep } = clipByRounds(d?.xAxis);
  return {
    xAxis,
    series: {
      overall_score: keep.map(i => d?.series?.overall_score?.[i] ?? 0),
      granularity_score: keep.map(i => d?.series?.granularity_score?.[i] ?? 0),
    },
  };
}
function clipRadar(d) {
  const upto = roundsUpToCurrent();
  return {
    dimensions: d?.dimensions || [],
    series: Object.fromEntries(Object.entries(d?.series || {}).filter(([r]) => upto.includes(r))),
  };
}
function clipGranularity(d) {
  const { xAxis, keep } = clipByRounds(d?.xAxis);
  return {
    xAxis,
    L1: keep.map(i => d?.L1?.[i] ?? 0),
    L2: keep.map(i => d?.L2?.[i] ?? 0),
    L3: keep.map(i => d?.L3?.[i] ?? 0),
  };
}
function clipWaterfall(d) {
  const upto = roundsUpToCurrent();
  const steps = (d?.steps || []).filter(s => upto.includes(s.to_round));
  let end = d?.start_score ?? 0;
  steps.forEach(s => { end = Math.round((end + (s.delta || 0)) * 100) / 100; });
  return { start_score: d?.start_score ?? 0, steps, end_score: end };
}
function clipRisk(d) {
  const { xAxis, keep } = clipByRounds(d?.xAxis);
  return {
    xAxis,
    risk_index: keep.map(i => d?.risk_index?.[i] ?? 0),
    level: keep.map(i => d?.level?.[i] || ""),
  };
}

/* 历史项目（无 project_id，非服务端）没有图表接口数据 → 用本地快照推导出与后端接口相同的数据结构 */
function chartsDataFromSnapshots() {
  const rounds = ROUNDS.filter(r => snapshots[r]);
  const list = rounds.map(r => snapshots[r]);
  const dimScores = s => DIM_ORDER.map(k => (s.agent_critic?.scores || {})[k] ?? 0);
  const steps = [];
  for (let i = 1; i < list.length; i++) {
    const prev = list[i - 1], curr = list[i];
    steps.push({
      label: (curr.agent_critic?.top_flaw || "").slice(0, 20) + "...",
      delta: Math.round((curr.overall_score - prev.overall_score) * 100) / 100,
      from_round: prev.round,
      to_round: curr.round,
    });
  }
  // 与后端 get_chart_risk 相同的启发式：counterfactual 越短风险越高
  const risk_index = list.map(s => {
    const cf = (s.agent_critic || {}).counterfactual || "";
    return Math.round(Math.max(0, Math.min(10, 10 - cf.length / 20)) * 100) / 100;
  });
  return {
    overall: { xAxis: rounds, series: { overall_score: list.map(s => s.overall_score), granularity_score: list.map(s => s.granularity_score ?? 0) } },
    radar: { dimensions: DIM_ORDER, series: Object.fromEntries(list.map(s => [s.round, dimScores(s)])) },
    granularity: { xAxis: rounds, L1: list.map(s => s.granularity_stats?.L1 ?? 0), L2: list.map(s => s.granularity_stats?.L2 ?? 0), L3: list.map(s => s.granularity_stats?.L3 ?? 0) },
    waterfall: { start_score: list[0]?.overall_score ?? 0, steps, end_score: list[list.length - 1]?.overall_score ?? 0 },
    risk: { xAxis: rounds, risk_index, level: risk_index.map(r => (r > 6 ? "高危" : r > 3 ? "中危" : "低危")) },
  };
}
async function renderCharts() {
  if (chartsRendered || !window.echarts) return;
  chartsRendered = true;
  const proj = historyStore[currentProjectKey];
  const projectId = isBackendProjectId(proj?.project_id) ? proj.project_id : null;
  const viewedAtStart = currentProjectKey; // 防串画：请求期间切走了项目就不渲染
  const mkEmpty = id => { const el = document.getElementById(id); if (el) el.innerHTML = `<div class="chart-empty">暂无数据</div>`; };
  // 轮次切换会重建 DOM，先释放旧实例避免泄漏
  ["chartOverall", "chartRadarAll", "chartGranularity", "chartWaterfall", "chartRisk"].forEach(id => {
    const el = document.getElementById(id);
    if (el && window.echarts) window.echarts.dispose(el);
  });
  try {
    let overall, radar, gran, waterfall, risk;
    if (projectId) {
      // 服务端项目：走图表接口（按项目隔离），再按当前轮次截断
      const p = `?project_id=${encodeURIComponent(projectId)}`;
      [overall, radar, gran, waterfall, risk] = await Promise.all([
        apiFetch(`/api/chart/overall${p}`, {}, 10000),
        apiFetch(`/api/chart/radar${p}`, {}, 10000),
        apiFetch(`/api/chart/granularity${p}`, {}, 10000),
        apiFetch(`/api/chart/waterfall${p}`, {}, 10000),
        apiFetch(`/api/chart/risk${p}`, {}, 10000),
      ]);
      overall = clipOverall(overall?.data);
      radar = clipRadar(radar?.data);
      gran = clipGranularity(gran?.data);
      waterfall = clipWaterfall(waterfall?.data);
      risk = clipRisk(risk?.data);
    } else {
      // 历史项目：本地快照推导（同样按当前轮次截断）
      const d = chartsDataFromSnapshots();
      overall = clipOverall(d.overall);
      radar = clipRadar(d.radar);
      gran = clipGranularity(d.granularity);
      waterfall = clipWaterfall(d.waterfall);
      risk = clipRisk(d.risk);
    }
    if (currentProjectKey !== viewedAtStart) return; // 中途切换了项目：丢弃本次渲染（chartsRendered 由 switchProject 重置）
    buildOverallChart(overall);
    buildRadarChart(radar);
    buildGranularityChart(gran);
    buildWaterfallChart(waterfall);
    buildRiskChart(risk);
  } catch (e) {
    if (currentProjectKey === viewedAtStart) {
      ["chartOverall", "chartRadarAll", "chartGranularity", "chartWaterfall", "chartRisk"].forEach(mkEmpty);
    }
    chartsRendered = false; // 失败后允许重试（修复「图表偶尔出不来」）
    console.error("图表加载失败:", e);
  }
}

function registerChart(key, instance) {
  chartInstances.set(key, instance);
  if (!chartResizeBound) {
    window.addEventListener("resize", () => {
      chartInstances.forEach(chart => {
        if (chart && !chart.isDisposed()) chart.resize();
      });
    });
    chartResizeBound = true;
  }
}
function disposeAllCharts() {
  chartInstances.forEach(chart => {
    if (chart && !chart.isDisposed()) chart.dispose();
  });
  chartInstances.clear();
}
function chartBase(el, option) {
  const dom = document.getElementById(el);
  if (!dom) return;
  const instance = window.echarts.getInstanceByDom(dom) || window.echarts.init(dom);
  instance.setOption(option, true);
  registerChart(el, instance);
}
const CHART_AXIS = { axisLine: { lineStyle: { color: "#c9cfc9" } }, axisLabel: { color: "#7a8583", fontSize: 10 } };
const CHART_SPLIT = { lineStyle: { color: "#e8ebe4" } };

function buildOverallChart(d) {
  if (!d || !d.xAxis?.length) { const el = document.getElementById("chartOverall"); if (el) el.innerHTML = `<div class="chart-empty">暂无数据</div>`; return; }
  chartBase("chartOverall", {
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { fontSize: 10, color: "#5c6b68" } },
    grid: { left: 38, right: 18, top: 32, bottom: 26 },
    xAxis: { type: "category", data: d.xAxis, ...CHART_AXIS },
    yAxis: { type: "value", min: 0, max: 10, ...CHART_AXIS, splitLine: CHART_SPLIT },
    series: [
      { name: "综合得分", type: "line", smooth: true, data: d.series?.overall_score || [], lineStyle: { width: 2.5, color: "#2c716b" }, itemStyle: { color: "#2c716b" }, symbolSize: 7 },
      { name: "颗粒度得分", type: "line", smooth: true, data: d.series?.granularity_score || [], lineStyle: { width: 2, color: "#c38442", type: "dashed" }, itemStyle: { color: "#c38442" }, symbolSize: 6 },
    ],
  });
}
function buildRadarChart(d) {
  if (!d || !d.dimensions?.length || !Object.keys(d.series || {}).length) { const el = document.getElementById("chartRadarAll"); if (el) el.innerHTML = `<div class="chart-empty">暂无数据</div>`; return; }
  const colors = { V1: "#2c716b", V2: "#4f7ea6", V3: "#a06d2b" };
  chartBase("chartRadarAll", {
    tooltip: {},
    legend: { top: 0, textStyle: { fontSize: 10 } },
    radar: {
      indicator: d.dimensions.map(x => ({ name: DIM_LABELS[x] || x, max: 10 })),
      radius: "62%", center: ["50%", "56%"],
      axisName: { color: "#5c6b68", fontSize: 10 },
      splitArea: { areaStyle: { color: ["rgba(79,169,158,.03)", "rgba(79,169,158,.07)"] } },
      splitLine: { lineStyle: { color: "#dde3da" } },
      axisLine: { lineStyle: { color: "#dde3da" } },
    },
    series: [{ type: "radar", data: Object.entries(d.series).map(([r, vals]) => ({ name: r, value: vals, areaStyle: { opacity: .12 }, lineStyle: { width: 2, color: colors[r] }, itemStyle: { color: colors[r] } })) }],
  });
}
function buildGranularityChart(d) {
  if (!d || !d.xAxis?.length) { const el = document.getElementById("chartGranularity"); if (el) el.innerHTML = `<div class="chart-empty">暂无数据</div>`; return; }
  chartBase("chartGranularity", {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, textStyle: { fontSize: 10 } },
    grid: { left: 38, right: 18, top: 32, bottom: 26 },
    xAxis: { type: "category", data: d.xAxis, ...CHART_AXIS },
    yAxis: { type: "value", minInterval: 1, ...CHART_AXIS, splitLine: CHART_SPLIT },
    series: [
      { name: "L1 概念", type: "bar", stack: "total", data: d.L1 || [], itemStyle: { color: "#7fb8c9" }, barMaxWidth: 46 },
      { name: "L2 量化", type: "bar", stack: "total", data: d.L2 || [], itemStyle: { color: "#4fa99e" }, barMaxWidth: 46 },
      { name: "L3 容错", type: "bar", stack: "total", data: d.L3 || [], itemStyle: { color: "#c38442" }, barMaxWidth: 46 },
    ],
  });
}
function buildWaterfallChart(d) {
  const el = document.getElementById("chartWaterfall");
  if (!el) return;
  if (!d || !d.steps?.length) { el.innerHTML = `<div class="chart-empty">至少需要两轮数据（V1→V2 迭代后生成）</div>`; return; }
  const labels = [d.steps[0].from_round, ...d.steps.map(s => s.to_round)];
  const totals = [d.start_score, ...d.steps.map(s => Math.round((d.start_score + (s.delta || 0)) * 100) / 100)];
  const helper = [], values = [];
  d.steps.forEach((s, i) => {
    values.push(Number(((s.delta || 0)).toFixed(2)));
    helper.push(totals[i] - Math.max(0, s.delta || 0));
  });
  values.push(Number(d.end_score.toFixed(2)));
  helper.push(0);
  chartBase("chartWaterfall", {
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      formatter: params => {
        const p = params.find(x => x.seriesName === "增量");
        if (!p) return "";
        return `${p.name}<br/>得分变化：${p.value > 0 ? "+" : ""}${p.value}`;
      },
    },
    grid: { left: 38, right: 18, top: 22, bottom: 26 },
    xAxis: { type: "category", data: labels, ...CHART_AXIS },
    yAxis: { type: "value", min: 0, max: 10, ...CHART_AXIS, splitLine: CHART_SPLIT },
    series: [
      { name: "基座", type: "bar", stack: "wf", data: helper, itemStyle: { color: "transparent" }, emphasis: { itemStyle: { color: "transparent" } }, tooltip: { show: false } },
      { name: "增量", type: "bar", stack: "wf", data: values, barMaxWidth: 44, label: { show: true, position: "top", fontSize: 10, color: "#5c6b68" }, itemStyle: { color: p => (p.value >= 0 ? "#4fa99e" : "#c26a5c") } },
    ],
  });
}
function buildRiskChart(d) {
  if (!d || !d.xAxis?.length) { const el = document.getElementById("chartRisk"); if (el) el.innerHTML = `<div class="chart-empty">暂无数据</div>`; return; }
  const colorOf = lv => (lv === "高危" ? "#c26a5c" : lv === "中危" ? "#c38442" : "#4fa99e");
  chartBase("chartRisk", {
    tooltip: {
      trigger: "axis",
      formatter: params => {
        const p = params[0];
        const lv = (d.level || [])[p.dataIndex] || "—";
        return `${p.name}<br/>风险指数：${p.value}<br/>等级：${lv}`;
      },
    },
    grid: { left: 38, right: 18, top: 22, bottom: 26 },
    xAxis: { type: "category", data: d.xAxis, ...CHART_AXIS },
    yAxis: { type: "value", min: 0, max: 10, ...CHART_AXIS, splitLine: CHART_SPLIT },
    series: [{
      name: "风险指数", type: "line", smooth: true, data: d.risk_index || [],
      lineStyle: { width: 2.5, color: "#b27432" },
      itemStyle: { color: p => colorOf((d.level || [])[p.dataIndex]) },
      symbolSize: 9,
      label: { show: true, formatter: p => (d.level || [])[p.dataIndex] || "", fontSize: 9, color: "#6d5a3c" },
    }],
  });
}

/* ================= 报告与导出 ================= */
function openReport() {
  if (currentRound) {
    const proj = historyStore[currentProjectKey];
    const pid = isBackendProjectId(proj?.project_id) ? proj.project_id : "";
    window.open(`/static/report.html?round=${encodeURIComponent(currentRound)}&question=${encodeURIComponent(currentQuestion)}&project_id=${encodeURIComponent(pid)}`, "_blank");
  }
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
  (e.evidence_list || []).forEach(ev => {
    const year = evidenceYear(ev);
    const source = ev.source || "未知";
    const yearSuffix = year && !String(source).includes(year) ? `, ${year}` : "";
    L.push(`- ${ev.claim}（来源：${source}${yearSuffix}）`);
  });
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
  (s.hypotheses || []).forEach((h, i) => {
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
function downloadMarkdown() {
  const snap = snapshots[currentRound];
  if (!snap) return;
  const allRounds = ROUNDS.filter(r => snapshots[r]).map(r => snapshots[r]);
  const text = snapshotToMarkdown(snap, allRounds);
  const blob = new Blob(["﻿" + text], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `ai-scientist-report-${currentRound}.md`;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

/* ================= 错误与结果处理 ================= */
function showError(message, { retry = null } = {}) {
  appStatus = "error";
  stopLoadingAnim();
  setStopBtn(false);
  const chat = $("#chatInner");
  const escaped = esc(message);
  chat.innerHTML = `
  <div class="error-card">
    <h3>研究流程出错</h3>
    <p>${escaped}</p>
    ${retry ? `<div class="err-actions"><button class="retry-btn" id="retryBtn">重试</button></div>` : ""}
  </div>`;
  if (retry) $("#retryBtn").onclick = retry;
  updateTopbar();
}
/* 任务完成：归档快照；仅当正查看该项目时才渲染工作区（核心非劫持修复） */
function onJobFinished(d) {
  const pid = d.project_id;
  stopPolling(pid);
  const snap = d?.result;
  if (!snap || !snap.round) { showError("后端返回数据异常"); return; }
  if (!snap.project_id) snap.project_id = pid;
  if (jobs[pid]) jobs[pid].status = "done";
  if (deletedKeys.has(pid)) return; // 项目已被删除：不再导入
  upsertSnapshot(snap);
  renderHistoryList();
  if (currentProjectKey === pid) {
    const proj = historyStore[pid];
    snapshots = Object.assign({}, proj?.rounds || {});
    currentRound = snap.round;
    currentQuestion = snap.question;
    appStatus = "ready";
    chartsRendered = false;
    setStopBtn(false);
    stopLoadingAnim();
    currentJobPid = null;
    setWorkspaceMode(true);
    renderWorkspace();
    refreshComposer();
    document.querySelector(".chat-wrap").scrollTo({ top: 0, behavior: "auto" });
  } else {
    // 后台完成：当前不在该项目，不劫持视图，仅刷新侧栏
    if (currentJobPid === pid) { currentJobPid = null; setStopBtn(false); }
    addSystemToast(`研究完成：${(snap.question || "").slice(0, 20)}…（${snap.round} · 综合 ${fmtScore(snap.overall_score)}）`);
  }
}
function onJobError(d) {
  const pid = d.project_id;
  stopPolling(pid);
  const prevJob = jobs[pid];
  const q = d.question || prevJob?.question;
  const round = d.round_label || prevJob?.round || "V1";
  const feedback = prevJob?.feedback || null;
  if (jobs[pid]) jobs[pid].status = "error";
  if (q) {
    const proj = historyStore[pid];
    if (proj && !Object.keys(proj.rounds || {}).length) { delete historyStore[pid]; saveHistoryStore(); }
  }
  renderHistoryList();
  if (currentProjectKey === pid) {
    currentJobPid = null;
    setStopBtn(false);
    stopLoadingAnim();
    showError(d.error || "研究流程出错", {
      retry: () => {
        const fromRound = prevJob?.from_round || "V1";
        showLoading(round, pid, null);
        enqueueJob(q, fromRound, feedback, pid).catch(e2 => showError(e2.message || "重试失败"));
      },
    });
    setComposerEnabled(false);
  } else {
    addSystemToast(`研究失败：${(q || "").slice(0, 20)}…`);
  }
}
function onJobCancelled(d) {
  const pid = d.project_id;
  stopPolling(pid);
  if (jobs[pid]) jobs[pid].status = "cancelled";
  const q = d.question;
  cleanupRunningProject(pid);
  renderHistoryList();
  if (currentProjectKey === pid) {
    currentJobPid = null;
    setStopBtn(false);
    stopLoadingAnim();
    const proj = historyStore[pid];
    if (proj && latestRoundOf(proj)) {
      switchProject(pid); // 已有轮次：回到该项目的旧结果
    } else {
      appStatus = "idle";
      currentQuestion = "";
      snapshots = {};
      currentRound = null;
      chartsRendered = false;
      setWorkspaceMode(false);
      showWelcomeView();
      updateTopbar();
    }
  }
}

/* ================= 任务提交 ================= */
/* 提交任务到后端并开始轮询；jobs 与 historyStore 占位同步更新 */
function enqueueJob(question, round, feedback, projectId) {
  return apiCreateJob(question, feedback, round, projectId).then(d => {
    const job = {
      job_id: d.job_id,
      project_id: d.project_id,
      question,
      round: d.round_label,   // 后端返回的目标轮次（正在生成）
      from_round: round,      // 提交时传给后端的轮次引用（current_round / initial_round），重试用
      feedback,
      status: "queued",
      stage: null,
      progress: null,
    };
    jobs[d.project_id] = job;
    const proj = historyStore[d.project_id];
    if (proj) {
      proj.project_id = d.project_id;
      proj.running = true;
      proj.round = d.round_label;
    }
    saveHistoryStore();
    renderHistoryList();
    startPolling(job);
    return d;
  });
}

/* ================= 研究启动 / 反馈 ================= */
async function startResearch(question) {
  const q = (question || "").trim();
  if (q.length < 5) {
    $("#customQuestionHint").textContent = "问题至少 5 个字符（当前 " + q.length + " 个）";
    $("#customQuestionHint").classList.add("bad");
    return;
  }
  closeQuestionModal();
  const projectId = generateProjectId();
  currentQuestion = q;
  snapshots = {};          // 新研究：清空工作区，成功后由 onJobFinished 重新赋值
  currentRound = null;
  // 点「开始研究」的瞬间就在历史档案里创建「研究中」条目（失败/取消时自动移除）
  historyStore[projectId] = { question: q, rounds: {}, updatedAt: new Date().toISOString(), running: true, project_id: projectId, round: "V1" };
  deletedKeys.delete(projectId);
  saveHistoryStore();
  saveDeletedKeys();
  currentProjectKey = projectId;   // 侧边栏高亮运行中的研究
  renderHistoryList();
  setWorkspaceMode(true);
  $("#chatInner").innerHTML = "";
  showLoading("V1", projectId, null);
  saveViewKey();
  try {
    await enqueueJob(q, "V1", null, projectId);
  } catch (e) {
    // 入队失败：清理占位并恢复旧数据
    if (historyStore[projectId] && !Object.keys(historyStore[projectId].rounds).length) {
      delete historyStore[projectId];
      saveHistoryStore();
    }
    stopLoadingAnim();
    setStopBtn(false);
    currentJobPid = null;
    renderHistoryList();
    showError(e.message || "运行失败");
  }
}

async function submitFeedback() {
  const text = $("#expertInput").value.trim();
  if (!text || text.length < 3) return;
  const proj = historyStore[currentProjectKey];
  if (!proj) return;
  if (!isBackendProjectId(proj.project_id)) { addSystemToast("历史项目仅支持查看，如需迭代请新建研究"); return; }
  const fromRound = latestRoundOf(proj) || currentRound;
  if (!fromRound || fromRound === "V3") return;
  const nextRound = `V${Number(fromRound[1]) + 1}`; // 仅用于展示「正在生成 Vx」
  const question = currentQuestion;
  showLoading(nextRound, proj.project_id, null);
  saveViewKey();
  try {
    // 后端按 current_round 自行 +1：这里传当前轮次，避免二次递增导致跳轮（V1 直接变 V3）
    await enqueueJob(question, fromRound, text, proj.project_id);
    $("#expertInput").value = "";
  } catch (e) {
    if (e.status === 400) {
      addSystemToast(e.message || "已达到最大迭代次数 V3");
    } else {
      showError(e.message || "反馈处理失败");
      setComposerEnabled(false);
      return;
    }
    // 回到 ready 视图
    stopLoadingAnim();
    setStopBtn(false);
    currentJobPid = null;
    if (currentProjectKey === proj.project_id) {
      appStatus = "ready";
      renderWorkspace();
      refreshComposer();
    }
  }
}

/* ================= 问题选择弹窗 ================= */
function loadQuestions() {
  const q = ($("#questionSearch").value || "").trim();
  const cat = $("#questionCategory").value;
  const rows = SAMPLE_QUESTIONS.filter(x =>
    (cat === "全部" || x.category === cat) &&
    (!q || x.title.includes(q) || x.q.includes(q) || x.category.includes(q)));
  $("#questionCount").textContent = `找到 ${rows.length} 个示例问题 · 亦可输入自定义问题`;
  $("#questionGrid").innerHTML = rows.map(item => `
  <article class="question-card">
    <div class="qno">${esc(item.category)}</div>
    <h4>${esc(item.title)}</h4>
    <p>${esc(item.summary)}</p>
    <footer><span>示例 #${item.id}</span><button data-id="${item.id}">开始研究 →</button></footer>
  </article>`).join("");
  if (!$("#questionGrid").dataset.bound) {
    $("#questionGrid").dataset.bound = "true";
    $("#questionGrid").addEventListener("click", event => {
      const button = event.target.closest("button[data-id]");
      if (!button) return;
      const item = SAMPLE_QUESTIONS.find(x => String(x.id) === String(button.dataset.id));
      if (item) startResearch(item.q).catch(err => showError(err.message || "运行失败"));
    });
  }
  if (motionEnabled()) {
    window.gsap.fromTo("#questionGrid .question-card", { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: .3, stagger: .025, ease: "power2.out", clearProps: "transform,opacity,visibility" });
  }
}
let questionModalTrigger = null;
function openQuestionModal() {
  questionModalTrigger = document.activeElement?.closest(".left") ? $("#mobileNavBtn") : document.activeElement;
  closeMobileSidebar(false);
  $("#questionModalBg").classList.add("open");
  const modal = $("#questionModal");
  modal.inert = false;
  modal.setAttribute("aria-hidden", "false");
  modal.classList.add("open"); // 弹窗可见性由 CSS .open 控制，不依赖 JS 动画
  $(".app").inert = true;
  document.body.classList.add("dialog-open");
  $("#overwriteWarning").style.display = Object.keys(snapshots).length ? "" : "none";
  $("#historyEntryBtn").style.display = projectList().length ? "" : "none";
  loadQuestions();
  requestAnimationFrame(() => $("#questionSearch").focus());
}
function closeQuestionModal() {
  const modal = $("#questionModal");
  $("#questionModalBg").classList.remove("open");
  modal.classList.remove("open");
  modal.inert = true;
  modal.setAttribute("aria-hidden", "true");
  $(".app").inert = false;
  document.body.classList.remove("dialog-open");
  questionModalTrigger?.focus();
}

/* ================= 移动端侧边栏 ================= */
function openMobileSidebar() {
  if (!window.matchMedia("(max-width: 780px)").matches) return;
  $(".left").classList.add("open");
  $("#mobileSidebarBg").classList.add("open");
  $("#mobileNavBtn").setAttribute("aria-expanded", "true");
}
function closeMobileSidebar(restoreFocus = true) {
  const wasOpen = $(".left").classList.contains("open");
  $(".left").classList.remove("open");
  $("#mobileSidebarBg").classList.remove("open");
  $("#mobileNavBtn").setAttribute("aria-expanded", "false");
  if (wasOpen && restoreFocus) $("#mobileNavBtn").focus();
}

/* ================= 欢迎页动画（沿用参考项目实现） ================= */
function splitWelcomeHeadline() {
  const headline = $(".welcome h1");
  if (!headline || headline.dataset.split) return headline;
  const walker = document.createTreeWalker(headline, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const fragment = document.createDocumentFragment();
    [...node.textContent].forEach(character => {
      const span = document.createElement("span");
      span.className = "headline-char";
      span.textContent = character === " " ? " " : character;
      fragment.append(span);
    });
    node.replaceWith(fragment);
  });
  headline.dataset.split = "true";
  return headline;
}
function animateWelcome() {
  if (!motionEnabled()) return;
  const welcome = $(".welcome");
  if (!welcome || welcome.dataset.animated) return;
  welcome.dataset.animated = "true";
  const headline = splitWelcomeHeadline();
  const copy = welcome.querySelector(".welcome-copy");
  // 注意：不动 .welcome-actions —— 按钮必须从一开始就可见可点，入场动画只做标题和文案
  const chars = [...headline.querySelectorAll(".headline-char")];
  window.gsap.set([copy].filter(Boolean), { autoAlpha: 0, y: 14 });
  window.gsap.set(chars, { autoAlpha: 0, y: 24, rotateX: -68, transformOrigin: "50% 100%" });
  window.gsap.timeline({ defaults: { ease: "power3.out" } })
    .to(chars, { autoAlpha: 1, y: 0, rotateX: 0, duration: .5, stagger: .028, clearProps: "transform,opacity,visibility" })
    .to(copy, { autoAlpha: 1, y: 0, duration: .28, clearProps: "transform,opacity,visibility" }, "-=.12");
  // 兜底：动画被浏览器节流或未播放时，强制显示标题和文案
  setTimeout(() => {
    if (window.gsap) window.gsap.set([copy, ...chars].filter(Boolean), { clearProps: "all" });
  }, 2600);
}
function setupWelcomeScene() {
  const scene = $("#welcomeScene");
  if (!scene || scene.dataset.bound) return;
  scene.dataset.bound = "true";
  // 无障碍：用户偏好减少动态效果时暂停视频
  const video = scene.querySelector(".welcome-scene-video");
  if (video && window.matchMedia("(prefers-reduced-motion: reduce)").matches) video.pause();
}
function animateShell() {
  if (!motionEnabled()) return;
  const groups = [$(".brand"), $(".new-btn"), $(".left-search"), $(".history-title"), $(".history"), $(".left-footer"), $(".topbar")].filter(Boolean);
  window.gsap.fromTo(groups, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: .42, stagger: .045, ease: "power2.out", clearProps: "transform,opacity,visibility" });
  // 兜底：动画未播放时强制显示侧边栏与顶栏
  setTimeout(() => { if (window.gsap) window.gsap.set(groups, { clearProps: "all" }); }, 2000);
}

/* ================= 事件绑定 ================= */
$("#newResearchBtn").onclick = openQuestionModal;
$("#welcomeStart").onclick = openQuestionModal;
$("#mobileNewResearchBtn").onclick = openQuestionModal;
$("#mobileNavBtn").onclick = openMobileSidebar;
$("#mobileSidebarClose").onclick = () => closeMobileSidebar();
$("#mobileSidebarBg").onclick = () => closeMobileSidebar();
$("#sidebarToggle").onclick = () => setSidebarCollapsed(!document.querySelector(".app").classList.contains("sidebar-collapsed"));
$("#homeBtn").onclick = returnHome;
$("#closeQuestionModal").onclick = closeQuestionModal;
$("#questionModalBg").onclick = closeQuestionModal;
$("#questionSearch").addEventListener("input", () => { clearTimeout(window._qs); window._qs = setTimeout(loadQuestions, 180); });
$("#questionCategory").onchange = loadQuestions;
$("#historySearch").addEventListener("input", renderHistoryList);
$("#sendBtn").onclick = () => submitFeedback().catch(err => showError(err.message || "反馈处理失败"));
$("#expertInput").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitFeedback(); } });
$("#customStartBtn").onclick = () => startResearch($("#customQuestion").value).catch(err => showError(err.message || "运行失败"));
$("#historyEntryBtn").onclick = () => { closeQuestionModal(); enterWorkspace(); };
$("#customQuestion").addEventListener("input", () => {
  $("#customQuestionHint").textContent = `至少 5 个字符（当前 ${$("#customQuestion").value.length} 个）`;
  $("#customQuestionHint").classList.toggle("bad", $("#customQuestion").value.trim().length < 5);
});
$("#stopBtn").onclick = async () => {
  const job = currentJobPid ? jobs[currentJobPid] : null;
  if (job && ["queued", "running"].includes(job.status)) {
    try {
      await apiCancelJob(job.job_id);
      addSystemToast("已请求取消，正在停止…");
    } catch { addSystemToast("取消失败，请稍后重试"); }
  }
};
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if ($("#questionModal").classList.contains("open")) closeQuestionModal();
  else closeMobileSidebar();
});

/* 轻量提示条 */
function addSystemToast(text, duration = 2600) {
  const existing = document.getElementById("sysToast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.id = "sysToast";
  toast.style.cssText = "position:fixed;left:50%;bottom:150px;transform:translateX(-50%);z-index:50;background:#111a20;color:#e8f1ee;padding:9px 16px;border-radius:8px;font-size:11px;box-shadow:0 10px 30px rgba(0,0,0,.25);max-width:86vw;line-height:1.5";
  toast.textContent = text;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

/* 全局错误可见化：任何页面 JS 错误都以提示条形式暴露（含出错位置，方便排查） */
window.addEventListener("error", e => {
  try {
    const where = e.filename ? " @ " + String(e.filename).split("/").pop() + ":" + e.lineno : "";
    addSystemToast("页面错误: " + (e.message || "未知错误") + where, 8000);
  } catch { /* ignore */ }
});
window.addEventListener("unhandledrejection", e => {
  try {
    const r = e.reason;
    const msg = (r && r.message) || String(r || "未知");
    const stackLine = r && r.stack ? String(r.stack).split("\n").filter(l => l.includes("app.js")).slice(0, 2).join(" → ") : "";
    addSystemToast("异步错误: " + msg + (stackLine ? " | " + stackLine : ""), 8000);
  } catch { /* ignore */ }
});

/* ================= 启动 ================= */
/* 首页逻辑：首次进入先显示欢迎界面；刷新时恢复上次查看的项目与运行中的任务 */
function enterWorkspace() {
  const projects = projectList();
  const target = projects.find(p => latestRoundOf(p)); // 跳过正在生成的占位条目
  if (!target) {
    addSystemToast("暂无已完成的研究，请先开始一项研究");
    return;
  }
  switchProject(target.project_id);
}
async function init() {
  setWorkspaceMode(false);
  setSidebarCollapsed(loadSidebarCollapsed());
  animateShell();
  setupWelcomeScene();
  animateWelcome();

  // 健康检查
  try {
    const health = await apiFetch("/api/health", {}, 8000);
    $("#apiStatus").innerHTML = `<i></i> ${health.status === "ok" ? "API 在线" : "API 异常"}`;
    $("#footerStatus").style.background = "#6ac394";
    $("#footerModel").textContent = `model: ${health.model || "--"}` + (health.api_key_configured ? "" : " · 未配置 Key");
  } catch {
    $("#apiStatus").innerHTML = `<i style="background:#c26a5c"></i> API 离线`;
    $("#footerStatus").style.background = "#c26a5c";
  }

  // 恢复本地历史 + 服务端快照合并归档
  historyStore = loadHistoryStore();
  deletedKeys = loadDeletedKeys();
  try {
    const res = await apiFetch("/api/snapshots", {}, 8000);
    const items = (res && res.data) || [];
    // 被用户删除的问题打上墓碑，刷新时不再从后端重新导入
    items.forEach(s => {
      if (s && s.round && !deletedKeys.has(snapshotProjectId(s)) && !deletedKeys.has(s.question)) upsertSnapshot(s);
    });
  } catch {
    // 服务端不可用时仍展示本地历史
  }

  // 恢复刷新前仍在运行的任务（真实阶段轮询 + running 占位）
  try {
    const jobsRes = await apiFetch("/api/jobs", {}, 8000);
    (jobsRes?.data || []).forEach(j => {
      if (!j || !j.project_id) return;
      j.round = j.round_label || j.round; // 后端字段 round_label → 前端统一用 round
      jobs[j.project_id] = j;
      const q = j.question;
      if (historyStore[j.project_id]) {
        historyStore[j.project_id].running = true;
        historyStore[j.project_id].round = j.round_label;
        historyStore[j.project_id].question = q;
      } else if (!deletedKeys.has(j.project_id) && !deletedKeys.has(q)) {
        historyStore[j.project_id] = { question: q, rounds: {}, updatedAt: new Date().toISOString(), running: true, project_id: j.project_id, round: j.round_label };
      }
      startPolling(j);
    });
    saveHistoryStore();
    renderHistoryList();
  } catch { /* 恢复失败不阻塞 */ }

  // 刷新恢复：跳回上次查看的项目（生成中的会显示其 loading 卡片）
  const viewKey = loadViewKey();
  if (viewKey && historyStore[viewKey]) {
    switchProject(viewKey);
  } else if (viewKey) {
    const migratedView = projectList().find(p => p.question === viewKey);
    if (migratedView) switchProject(migratedView.project_id);
  }

  // 视频自动播放兜底：部分浏览器 autoplay 属性不生效时显式触发
  const video = document.querySelector(".welcome-scene-video");
  if (video) {
    const tryPlay = () => { const p = video.play(); if (p && p.catch) p.catch(() => {}); };
    if (video.readyState >= 2) tryPlay();
    else video.addEventListener("canplay", tryPlay, { once: true });
    // 5 秒后仍没播起来再兜底一次（例如已缓存/已暂停的情况）
    setTimeout(() => { if (video.paused) tryPlay(); }, 5000);
  }
}
init();
