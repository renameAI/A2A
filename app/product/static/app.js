/* A2A 매칭엔진 v0 프론트 — 한 사이클: 자료 → 프로필 → 후보 → 판단 → 초안/협상 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { assets: [], dialogue: [], companyId: null, intent: null, judged: {},
  loopEvents: [] };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function showError(sel, msg) {
  const el = $(sel);
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideError(sel) {
  const el = $(sel);
  el.textContent = "";
  el.classList.add("hidden");
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data.error || { code: res.status, message: "요청 실패" };
    throw Object.assign(new Error(err.message), err);
  }
  return data;
}

/* ═══════════════════════════════════════════════════════════════
   파이프라인 DAG 시각화 — 각 모듈의 실행 과정을 노드 그래프로.
   backend의 node_start/node_end 이벤트로 상태·소요시간을 그리고,
   노드 클릭 시 해당 구간 로그만 필터한다.
   ═══════════════════════════════════════════════════════════════ */

const PIPELINES = {
  onboard: {
    title: "Represent — 자료 → 프로필 + 상(像)",
    nodes: [
      { id: "fetch",       col: 0, row: 0, icon: "📥", label: "자료 수집·청킹",   desc: "URL 크롤(robots·캐시)·PDF·SNS → 출처 라벨 청크" },
      { id: "llm.reason",  col: 1, row: 0, icon: "🧠", label: "다층 독해 (추론)", desc: "reasoning ON — 5층 독해로 회사의 상(像) 구축" },
      { id: "llm.format",  col: 2, row: 0, icon: "🧩", label: "구조화",           desc: "스키마 강제 + sanitize 정화 + grounding 검증" },
      { id: "mock.parse",  col: 1, row: 2, icon: "📋", label: "Mock 파서",        desc: "LLM 키 없음 — '키: 값' 구조화 텍스트 파서" },
      { id: "gate",        col: 3, row: 0, icon: "🚧", label: "최소 프로필 게이트", desc: "REP-06 — 문제·솔루션·타겟·VP 미달 시 409" },
      { id: "audit",       col: 4, row: 0, icon: "🗂", label: "감사 로그",        desc: "SYS-04 — audit/*.jsonl 축적" },
    ],
    edges: [["fetch", "llm.reason"], ["llm.reason", "llm.format"], ["llm.format", "gate"],
            ["fetch", "mock.parse"], ["mock.parse", "gate"], ["gate", "audit"]],
  },
  match: {
    title: "Retrieve — 보완성 검색 (유사도 아님)",
    nodes: [
      { id: "synth",  col: 0, row: 0, icon: "🎯", label: "이상적 상대상 합성", desc: "1단 — 내 솔루션이 푸는 문제를 '겪는' 상대의 상" },
      { id: "search", col: 1, row: 0, icon: "🔍", label: "하이브리드 검색",   desc: "2단 — 벡터 유사 + 온톨로지 보정 + 경쟁사 강등" },
    ],
    edges: [["synth", "search"]],
  },
  judge: {
    title: "Judge — 점수가 아닌 구조화 판단",
    nodes: [
      { id: "gate.dealbreaker", col: 0, row: 0, icon: "🚫", label: "결격 게이트",   desc: "JDG-04 — deal-breaker 하드 차단 (항상 규칙)" },
      { id: "llm.reason",       col: 1, row: 0, icon: "🧠", label: "깊은 추론",     desc: "reasoning ON — 상 재구성 → 차원 판정 → 딜 구조" },
      { id: "llm.format",       col: 2, row: 0, icon: "🧩", label: "구조화",        desc: "스키마 강제 — 판단을 JudgeResult 계약으로" },
      { id: "rules.judge",      col: 1, row: 1, icon: "📐", label: "규칙 판단",     desc: "Mock — bigram 보완성 + 온톨로지 규칙" },
      { id: "validate",         col: 3, row: 0, icon: "✅", label: "차원 계약 검증", desc: "JDG-02 — sell 5차원 / buy 7차원 누락 검사" },
      { id: "audit",            col: 4, row: 0, icon: "🗂", label: "감사 로그",     desc: "SYS-04 — 입력·궤적·결정 저장 (재학습용)" },
    ],
    edges: [["gate.dealbreaker", "llm.reason"], ["llm.reason", "llm.format"],
            ["llm.format", "validate"], ["validate", "audit"],
            ["gate.dealbreaker", "rules.judge"], ["rules.judge", "audit"]],
  },
  compose: {
    title: "Compose — 수신자가 사는 가치의 언어로",
    nodes: [
      { id: "compose.llm",      col: 0, row: 0, icon: "✍️", label: "생성 (LLM)",    desc: "fit_reasons에서만 주장 — claim_trace 추적" },
      { id: "llm.format",       col: 1, row: 0, icon: "🧩", label: "구조화",        desc: "스키마 강제 — 변형 A/B + 근거 매핑" },
      { id: "compose.template", col: 0, row: 1, icon: "📋", label: "생성 (템플릿)", desc: "Mock — Judge 근거 기반 템플릿" },
      { id: "sendgate",         col: 2, row: 0, icon: "🔒", label: "사람 승인 게이트", desc: "CMP-06 — send_blocked, 발송은 항상 사람" },
    ],
    edges: [["compose.llm", "llm.format"], ["llm.format", "sendgate"],
            ["compose.template", "sendgate"]],
  },
  negotiate: { title: "A2A 협상 — 제안 ↔ 검토 ↔ 재제안 (7-A)", dynamic: true },
  consult: {
    title: "Consultant — 진단 인터뷰 (기획서 9장)",
    nodes: [
      { id: "consult",    col: 0, row: 0, icon: "🎙", label: "인터뷰 턴",  desc: "슬롯 공백 분석 → 질문·선택지 설계 (회사의 상에서 도출)" },
      { id: "llm.format", col: 1, row: 0, icon: "🧩", label: "구조화",     desc: "질문·선택지·슬롯을 스키마로 강제" },
    ],
    edges: [["consult", "llm.format"]],
  },
};

const NODE_W = 170, NODE_H = 64, GAP_X = 56, GAP_Y = 30, PAD = 14;

function nodeInstances(logs) {
  /* node_start/node_end 이벤트 → 인스턴스 목록. 같은 id 반복 실행 지원(협상 라운드) +
     parent 추적(전역 콜스택 top) — 중첩 노드가 어느 부모 아래인지 그래프로 이어준다. */
  const openById = {}, callStack = [], instances = [];
  for (const e of logs) {
    if (e.type === "node_start") {
      const parent = callStack.length ? callStack[callStack.length - 1] : null;
      const inst = { id: e.node, label: e.stage, start: e.t, depth: e.depth || 1,
                     status: "running", parent };
      (openById[e.node] = openById[e.node] || []).push(inst);
      callStack.push(inst);
      instances.push(inst);
    } else if (e.type === "node_end") {
      const stack = openById[e.node] || [];
      const inst = stack.pop();
      if (inst) {
        inst.end = e.t;
        inst.status = e.status === "ok" ? "ok" : "error";
        const idx = callStack.lastIndexOf(inst);
        if (idx >= 0) callStack.splice(idx, 1);
      }
    }
  }
  return instances;
}

function fmtElapsed(inst, job) {
  if (inst.end != null) return `${(inst.end - inst.start).toFixed(1)}s`;
  if (inst.status === "running") return `${Math.max(job.elapsed - inst.start, 0).toFixed(1)}s…`;
  return "";
}

const STATUS_KO = { pending: "대기", running: "실행 중", ok: "완료",
                    error: "실패", skipped: "건너뜀" };

function svgNode(x, y, node, inst, job) {
  const status = inst ? inst.status
    : (job.status === "running" || job.status === "queued") ? "pending" : "skipped";
  const elapsed = inst ? fmtElapsed(inst, job) : "";
  const sub = `${STATUS_KO[status]}${elapsed ? " · " + elapsed : ""}`;
  return `<g class="pnode pn-${status}" data-node="${esc(node.id)}" transform="translate(${x},${y})">
    <title>${esc(node.desc || node.label)}</title>
    <rect width="${NODE_W}" height="${NODE_H}" rx="10"></rect>
    <text x="12" y="24" class="pn-label">${node.icon || "▫"} ${esc(node.label)}</text>
    <text x="12" y="46" class="pn-sub">${esc(sub)}</text>
    <circle class="pn-port" cx="0" cy="${NODE_H / 2}" r="3.5"></circle>
    <circle class="pn-port" cx="${NODE_W}" cy="${NODE_H / 2}" r="3.5"></circle>
    <circle class="pn-port" cx="${NODE_W / 2}" cy="0" r="3.5"></circle>
    <circle class="pn-port" cx="${NODE_W / 2}" cy="${NODE_H}" r="3.5"></circle>
  </g>`;
}

function svgEdge(x1, y1, x2, y2, cls, vertical = false) {
  if (vertical) {
    const my = (y1 + y2) / 2;
    return `<path class="pedge ${cls}" d="M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}"></path>`;
  }
  const mx = (x1 + x2) / 2;
  return `<path class="pedge ${cls}" d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}"></path>`;
}

function renderPipeline(pipeBox, kind, job) {
  const def = PIPELINES[kind];
  if (!def) return;
  const instances = nodeInstances(job.logs);

  let nodes, edges, states = {}, parentOf = {};
  if (def.dynamic) {
    /* 협상: 라운드(부모)마다 컬럼 하나 — 그 라운드 내부 판단 노드는 같은 컬럼 아래로
       쌓아 부모-자식 연결선으로 잇는다 (round → gate.dealbreaker → rules.judge → audit). */
    const rootIndex = new Map();   // root inst → column
    let nextCol = 0;
    for (const inst of instances) {
      if (!inst.parent) rootIndex.set(inst, nextCol++);
    }
    const rowCounter = {};   // col → 다음 행 번호
    nodes = instances.map((inst, i) => {
      let root = inst, ancestors = [];
      while (root.parent) { ancestors.push(root); root = root.parent; }
      const col = rootIndex.get(root);
      const row = inst.parent ? (rowCounter[col] = (rowCounter[col] || 1)) : 0;
      if (inst.parent) rowCounter[col] = row + 1;
      const id = `i${i}`;
      if (inst.parent) parentOf[id] = instances.indexOf(inst.parent);
      return { id, col, row, icon: inst.parent ? "└" : "🔁", label: inst.label, inst };
    });
    edges = [];
    const roots = nodes.filter((n) => n.row === 0);
    for (let i = 1; i < roots.length; i++) edges.push([roots[i - 1].id, roots[i].id, false]);
    const byInstIdx = Object.fromEntries(nodes.map((n, i) => [i, n.id]));
    for (const [childId, parentInstIdx] of Object.entries(parentOf)) {
      edges.push([byInstIdx[parentInstIdx], childId, true]);
    }
  } else {
    /* 정적 그래프: 같은 id 첫 인스턴스를 상태로 매핑 */
    for (const inst of instances) if (!states[inst.id]) states[inst.id] = inst;
    nodes = def.nodes.map((n) => ({ ...n, inst: states[n.id] }));
    edges = def.edges.map(([a, b]) => [a, b, false]);
  }

  const pos = {};
  let maxX = 0, maxY = 0;
  for (const n of nodes) {
    pos[n.id] = { x: PAD + n.col * (NODE_W + GAP_X), y: PAD + n.row * (NODE_H + GAP_Y) };
    maxX = Math.max(maxX, pos[n.id].x + NODE_W);
    maxY = Math.max(maxY, pos[n.id].y + NODE_H);
  }

  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const edgeSvg = edges.map(([a, b, vertical]) => {
    const A = pos[a], B = pos[b];
    if (!A || !B) return "";
    const sa = byId[a].inst?.status, sb = byId[b].inst?.status;
    const cls = (sa === "ok" && (sb === "ok" || sb === "error")) ? "pe-done"
      : (sa === "ok" || sa === "running") && sb === "running" ? "pe-active"
      : sa === "ok" && !sb && (job.status === "running") ? "pe-active" : "pe-idle";
    return vertical
      ? svgEdge(A.x + NODE_W / 2, A.y + NODE_H, B.x + NODE_W / 2, B.y, cls, true)
      : svgEdge(A.x + NODE_W, A.y + NODE_H / 2, B.x, B.y + NODE_H / 2, cls);
  }).join("");

  const nodeSvg = nodes.map((n) => svgNode(pos[n.id].x, pos[n.id].y, n, n.inst, job)).join("");
  const totalTime = job.status === "done" || job.status === "error"
    ? (job.logs.length ? job.logs[job.logs.length - 1].t.toFixed(1) : "0") : job.elapsed.toFixed(1);

  pipeBox.classList.remove("hidden");
  pipeBox.innerHTML = `
    <div class="pipe-head">
      <span>⚙️ ${esc(def.title)}</span>
      <span class="pipe-total">총 ${totalTime}s · ${STATUS_KO[job.status === "queued" ? "pending" : job.status] || job.status}</span>
    </div>
    <div class="pipe-scroll"><svg width="${maxX + PAD}" height="${maxY + PAD}"
      viewBox="0 0 ${maxX + PAD} ${maxY + PAD}">${edgeSvg}${nodeSvg}</svg></div>
    <div class="pipe-hint">노드를 클릭하면 해당 구간의 로그만 필터됩니다 · 점선 = 이번 실행에서 건너뜀</div>`;

  /* 노드 클릭 → 로그 필터 */
  const logBox = pipeBox._logBox;
  pipeBox.querySelectorAll(".pnode").forEach((g) => {
    g.addEventListener("click", () => {
      const nodeId = def.dynamic
        ? (nodes.find((n) => n.id === g.dataset.node)?.inst || {}).id
        : g.dataset.node;
      if (!logBox) return;
      logBox._filter = logBox._filter === nodeId ? null : nodeId;
      renderLogs(logBox, logBox._logs || [], logBox._status || "done");
    });
  });
}

function ensurePipeline(logBox, kind) {
  if (logBox._pipe) return logBox._pipe;
  const pipe = document.createElement("div");
  pipe.className = "pipebox hidden";
  pipe._logBox = logBox;
  logBox.parentNode.insertBefore(pipe, logBox);
  logBox._pipe = pipe;
  return pipe;
}

/* ── 비동기 job + 라이브 진행 로그 ──────────────────────────────
   LLM 작업(K-EXAONE reasoning)은 수 분 걸린다 — job을 던지고
   1.2초마다 폴링하며 엔진의 사고 과정을 DAG + logBox에 실시간 렌더한다. */

function renderLogs(logBox, logs, status) {
  if (!logBox) return;
  logBox.classList.remove("hidden");
  logBox._logs = logs; logBox._status = status;
  const filter = logBox._filter;
  const shown = filter ? logs.filter((l) => l.node === filter) : logs;
  const filterChip = filter
    ? `<span class="log-filter">노드 필터: ${esc(filter)} ✕</span>` : "";
  const lines = shown.map((l) => {
    const cls = l.type === "node_start" ? " log-nstart"
      : l.type === "node_end" ? (l.status === "ok" ? " log-nok" : " log-nerr") : "";
    return `<div class="log-line${cls}"><span class="log-t">${l.t.toFixed(1)}s</span>` +
      `<span class="log-stage">[${esc(l.stage)}]</span> ${esc(l.message)}</div>`;
  }).join("");
  const spinner = status === "running" || status === "queued"
    ? `<div class="log-line log-wait">⏳ 진행 중... (K-EXAONE reasoning은 수 분 걸릴 수 있어요)</div>` : "";
  logBox.innerHTML = `<div class="log-head">엔진 진행 로그 ${filterChip}</div>${lines}${spinner}`;
  const chip = logBox.querySelector(".log-filter");
  if (chip) chip.onclick = (e) => {
    e.stopPropagation(); logBox._filter = null;
    renderLogs(logBox, logBox._logs, logBox._status);
  };
  logBox.scrollTop = logBox.scrollHeight;
}

async function runJob(path, body, logBox, kind) {
  const pipe = kind && logBox ? ensurePipeline(logBox, kind) : null;
  const { job_id } = await api(path, { method: "POST", body: JSON.stringify(body) });
  while (true) {
    const job = await api(`/product/jobs/${job_id}`);
    renderA2ALoop(kind || path, job);
    if (pipe) renderPipeline(pipe, kind, job);
    renderLogs(logBox, job.logs, job.status);
    if (job.status === "done") return job.result;
    if (job.status === "error") {
      throw Object.assign(new Error(job.error.message), job.error);
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
}

/* ── A2A 소통 루프 — product job을 A2A Task lifecycle로 같은 화면에 표시 ── */

const A2A_STAGE = {
  onboard: "profile/represent",
  consult: "consult",
  match: "retrieve",
  judge: "judge",
  compose: "compose",
  negotiate: "negotiate",
};

function renderA2ALoop(kind, job) {
  const box = $("#a2a-loop");
  if (!box) return;
  box.classList.remove("hidden");
  const stateName = job.a2a_state || (job.status === "done" ? "completed" : job.status);
  const label = A2A_STAGE[kind] || kind;
  $("#loop-current").textContent = `${label}: ${stateName}`;
  $("#loop-current").className = `badge loop-${stateName}`;

  document.querySelectorAll("#loop-lifecycle span").forEach((el) => {
    const st = el.dataset.st;
    el.classList.toggle("on", st === stateName ||
      (st === "working" && ["submitted", "working"].includes(stateName)) ||
      (st === "completed" && stateName === "completed"));
  });

  const last = state.loopEvents[state.loopEvents.length - 1];
  const sig = `${job.job_id}:${stateName}:${job.logs.length}`;
  if (!last || last.sig !== sig) {
    state.loopEvents.push({
      sig,
      t: new Date().toLocaleTimeString(),
      jobId: job.job_id,
      label,
      state: stateName,
      logs: job.logs.length,
    });
    state.loopEvents = state.loopEvents.slice(-12);
  }

  $("#loop-events").innerHTML = state.loopEvents.map((e) =>
    `<div class="loop-event loop-${esc(e.state)}">
      <b>${esc(e.label)}</b><span>${esc(e.state)}</span>
      <small>${esc(e.t)} · job ${esc(e.jobId)} · logs ${e.logs}</small>
    </div>`).join("");
}

/* ── ① 자료 입력 ─────────────────────────────────────────────── */

const ASSET_LABEL = { website: "웹사이트", article: "기사", instagram: "인스타그램",
                      text: "텍스트", ir_deck: "IR덱 PDF" };

function addAssetRow(type, preset = {}) {
  const row = document.createElement("div");
  row.className = "asset-row";
  row.dataset.type = type;
  const isText = type === "text";
  const isPdf = type === "ir_deck";
  row.innerHTML = `<span class="tag">${ASSET_LABEL[type]}</span>` +
    (isText
      ? `<textarea rows="4" placeholder="회사 소개·문제·솔루션·타겟 등을 자유롭게 붙여넣으세요"></textarea>`
      : `<input value="${esc(preset.url || "")}" ${isPdf ? "readonly" : ""}
           placeholder="${type === "instagram" ? "https://instagram.com/계정 또는 @핸들" : "https://..."}">`) +
    `<button type="button" class="del" title="삭제">✕</button>`;
  row.querySelector(".del").onclick = () => { row.remove(); updateChecklist(); };
  row.addEventListener("input", updateChecklist);
  $("#assets").appendChild(row);
  updateChecklist();
}

document.querySelectorAll("[data-add]").forEach((b) =>
  b.onclick = () => addAssetRow(b.dataset.add));

$("#pdf-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/product/upload", { method: "POST", body: form });
  const data = await res.json();
  if (!res.ok) { alert(data.error?.message || "업로드 실패"); return; }
  addAssetRow("ir_deck", { url: data.path });
  e.target.value = "";
};

function collectAssets() {
  return [...document.querySelectorAll(".asset-row")].map((row) => {
    const type = row.dataset.type;
    const field = row.querySelector("input, textarea");
    const value = field.value.trim();
    if (!value) return null;
    return type === "text" ? { type, content: value } : { type, content: "", url: value };
  }).filter(Boolean).map((a) => a.type === "text" ? a : { type: a.type, content: "", url: a.url });
}

function upsertDialogue(q, a) {   // 라운드 넘어가도 답변 유지 (런타임 엔티티 저장)
  const hit = state.dialogue.find((d) => d.q === q);
  if (hit) hit.a = a; else state.dialogue.push({ q, a });
}

function collectDialogue() {
  // 이번에 입력한 보강 답변을 state.dialogue에 누적 (재분석 시 이전 답변 유실 방지)
  document.querySelectorAll("#questions input").forEach((input) => {
    if (input.value.trim()) upsertDialogue(input.dataset.q, input.value.trim());
  });
  const dialogue = [...state.dialogue];
  const ws = $("#w-sell").value, wb = $("#w-buy").value;
  if (ws) dialogue.push({ q: "판매의향", a: ws });
  if (wb) dialogue.push({ q: "구매의향", a: wb });
  return dialogue;
}

function collectPrivateState() {
  return $("#private-state").value.split("\n").map((line) => {
    const idx = line.indexOf(":");
    if (idx < 1) return null;
    return { key: line.slice(0, idx).trim(), value: line.slice(idx + 1).trim(),
             source: "observed" };
  }).filter((i) => i && i.key && i.value);
}

async function onboard() {
  const assets = collectAssets();
  if (!assets.length) { showError("#onboard-error", "자료를 1건 이상 입력해주세요."); return; }
  hideError("#onboard-error");
  const btn = $("#btn-onboard"); btn.disabled = true;
  try {
    // content 없는 URL 자산은 서버가 수집(fetch)한다
    const body = {
      assets: assets.map((a) => a.url ? { type: a.type, content: "", url: a.url } : a),
      dialogue: collectDialogue(),
      private_state: collectPrivateState(),
      company_id: state.companyId,   // 재분석이면 같은 회사를 갱신 (REP-09)
    };
    const data = await runJob("/product/onboard", body, $("#onboard-log"), "onboard");
    state.companyId = data.company_id;
    $("#questions-panel").classList.add("hidden");
    renderProfile(data);
    $("#step2").classList.remove("hidden");
    $("#step-consult").classList.remove("hidden");
    $("#step3").classList.remove("hidden");
    $("#engine-mode").textContent = `engine: ${data.engine_mode}`;
    $("#engine-mode").className = `badge mode-${data.engine_mode}`;
    updateChecklist(data.profile);
    computeMinStatus(data.open_questions);
    if (data.open_questions.length) showQuestions(data.open_questions);
    renderMinProgress();
    if (data.question_pin_count > 0) {
      $("#step-evidence").classList.remove("hidden");
      await loadEvidence();
    }
  } catch (err) {
    if (err.code === "profile_below_minimum") {
      computeMinStatus(err.details.open_questions);
      showQuestions(err.details.open_questions, err.details.clarify);
      renderMinProgress();
      showError("#onboard-error",
        "최소 프로필 기준 미달 — 아래 보강 질문에 답하면 매칭 풀에 들어갈 수 있어요.");
    } else {
      showError("#onboard-error", `${err.code || ""} ${err.message}`);
    }
  } finally { btn.disabled = false; }
}   // eslint-disable-line
$("#btn-onboard").onclick = onboard;
$("#btn-reanalyze").onclick = onboard;

function showQuestions(questions, clarify) {
  // 질문 원문을 data-q로 그대로 보관 — 필드 매핑은 백엔드가 단독으로 한다
  const byQ = {};
  (clarify || []).forEach((item) => { byQ[item.question] = item; });
  $("#questions").innerHTML = questions.map((q) => {
    const prior = state.dialogue.find((d) => d.q === q);   // 이전 답변 복원
    const c = byQ[q];
    const chips = c ? `<div class="clarify-why">${esc(c.why)}</div>` +
      `<div class="clarify-opts">` + c.options.map((o) =>
        `<button type="button" class="opt-chip" data-val="${esc(o.label)}">` +
        `${esc(o.label)}<small>${esc(o.hint)}</small></button>`).join("") +
      `</div>` : "";
    return `<div class="q-block"><label class="block">${esc(q)}${chips}` +
      `<input data-q="${esc(q)}" value="${esc(prior ? prior.a : "")}"` +
      ` placeholder="${c ? "다 아니면 직접 입력" : "답변"}"></label></div>`;
  }).join("");
  document.querySelectorAll("#questions input").forEach((input) => {
    input.addEventListener("input", () => {   // 직접 입력하면 칩 선택 해제
      input.closest(".q-block").querySelectorAll(".opt-chip.sel")
        .forEach((c) => { if (c.dataset.val !== input.value) c.classList.remove("sel"); });
      renderMinProgress();
    });
  });
  document.querySelectorAll("#questions .opt-chip").forEach((chip) => {
    chip.onclick = () => {   // 칩 탭 = 답변 입력 (한 질문에 하나만)
      const block = chip.closest(".q-block");
      block.querySelectorAll(".opt-chip").forEach((c) => c.classList.remove("sel"));
      chip.classList.add("sel");
      const input = block.querySelector("input");
      input.value = chip.dataset.val;
      renderMinProgress();
    };
  });
  $("#questions-panel").classList.remove("hidden");
}

/* ── 최소 프로필 진행바 (무엇이 채워지고 무엇이 남았는지) ─────────── */

const MIN_FIELDS = [
  { key: "problem", label: "문제" },
  { key: "solution", label: "솔루션" },
  { key: "target", label: "타겟" },
  { key: "value_prop", label: "가치 제안" },
];

function classifyQuestion(q) {   // 보강 질문 원문 → 최소 필드 (구체 키워드부터)
  if (q.includes("가치")) return "value_prop";   // '문제해결 중' 포함 → '문제'보다 먼저
  if (q.includes("방식") || q.includes("해결하나요")) return "solution";
  if (q.includes("팔고") || q.includes("타겟")) return "target";
  if (q.includes("문제")) return "problem";
  return null;                   // 의향 등 최소 필드 아님
}

// open_questions에 남아있으면 미충족, 아니면 충족 (게이트 통과 시 전부 충족)
function computeMinStatus(openQuestions) {
  const status = { problem: "filled", solution: "filled",
                   target: "filled", value_prop: "filled" };
  (openQuestions || []).forEach((q) => {
    const f = classifyQuestion(q);
    if (f) status[f] = "remaining";
  });
  state.minStatus = status;
}

function renderMinProgress() {
  const box = $("#min-progress");
  if (!box || !state.minStatus) return;
  // 입력 중인 답변은 '채우는 중'으로 낙관적 표시
  const typing = {};
  document.querySelectorAll("#questions input").forEach((input) => {
    if (!input.value.trim()) return;
    const f = classifyQuestion(input.dataset.q || "");
    if (f) typing[f] = true;
  });
  let done = 0;
  const chips = MIN_FIELDS.map(({ key, label }) => {
    let st = state.minStatus[key];
    if (st !== "filled" && typing[key]) st = "typing";
    if (st === "filled") done += 1;
    const mark = st === "filled" ? "✓" : st === "typing" ? "…" : "○";
    return `<span class="mp-chip mp-${st}">${mark} ${label}</span>`;
  }).join("");
  const pct = Math.round((done / MIN_FIELDS.length) * 100);
  box.innerHTML =
    `<div class="mp-head">최소 프로필 <b>${done}/${MIN_FIELDS.length}</b> 충족` +
    `<span class="mp-hint">✓ 채워짐 · … 채우는 중 · ○ 남음</span></div>` +
    `<div class="mp-bar"><div class="mp-fill" style="width:${pct}%"></div></div>` +
    `<div class="mp-chips">${chips}</div>`;
  box.classList.remove("hidden");
}

/* ── ②++ 근거 시각화 (bbox) — IR덱 원문 위 AI가 본 위치 + 댓글 강제 ─── */

async function loadEvidence() {
  hideError("#evidence-error");
  try {
    const data = await api(`/product/companies/${state.companyId}/evidence`);
    renderEvidencePages(data);
  } catch (err) {
    showError("#evidence-error", `${err.code || ""} ${err.message}`);
  }
}

function renderEvidencePages(data) {
  const { pins, threads } = data;
  const openCount = threads.filter((t) => t.status === "open").length;
  const answered = data.answered_count || 0;
  // 소통 루프 — 답한 게 있으면 재분석 유도(답변을 엑사원에 되먹임해 프로필 개선)
  const reanalyze = answered > 0
    ? ` · <button type="button" id="btn-evidence-reanalyze" class="link-btn">답변 ${answered}개 반영해서 재분석 →</button>`
    : "";
  $("#evidence-summary").innerHTML =
    `AI 질문 <b>${pins.length}</b>개가 원문 위에 표시됨` +
    (openCount > 0
      ? ` · <span class="ev-warn">미응답 ${openCount}개 — 답하기 전엔 후보 발굴이 막혀요</span>`
      : ` · <span class="ev-ok">전부 답변됨 — 후보 발굴 가능</span>`) +
    reanalyze;
  const reBtn = $("#btn-evidence-reanalyze");
  if (reBtn) reBtn.onclick = onboard;   // 같은 company_id 재온보딩 → 서버가 답변 병합

  const threadByEvidence = {};
  threads.forEach((t) => { threadByEvidence[t.evidence_id] = t; });

  // asset_index·page별로 묶어 페이지 한 장에 질문 핀 여러 개를 겹쳐 그린다
  const byPage = {};
  pins.forEach((p) => {
    const key = `${p.asset_index}:${p.page}`;
    (byPage[key] = byPage[key] || []).push(p);
  });

  $("#evidence-pages").innerHTML = Object.entries(byPage).map(([key, boxes]) => {
    const [assetIdx, page] = key.split(":");
    const boxesHtml = boxes.map((p, idx) => {
      const thread = threadByEvidence[p.evidence_id];
      const resolved = thread && thread.status === "resolved";
      const style = `left:${p.box.xmin / 10}%;top:${p.box.ymin / 10}%;` +
        `width:${(p.box.xmax - p.box.xmin) / 10}%;height:${(p.box.ymax - p.box.ymin) / 10}%;`;
      return `<div class="ev-box ${resolved ? "ev-resolved" : "ev-open"}"
        style="${style}" data-evidence-id="${esc(p.evidence_id)}"
        title="${esc(p.question)}">${resolved ? "✓" : "?"}</div>`;
    }).join("");
    return `<div class="ev-page" data-asset="${assetIdx}" data-page="${page}">
      <div class="ev-page-frame">
        <img src="/product/pages/${state.companyId}/a${assetIdx}_p${page}.png">
        ${boxesHtml}
      </div>
      <div class="ev-thread hidden"></div>
    </div>`;
  }).join("");

  document.querySelectorAll(".ev-box").forEach((box) => {
    box.onclick = () => openThreadPanel(box, threadByEvidence);
  });
}

function openThreadPanel(box, threadByEvidence) {
  const evidenceId = box.dataset.evidenceId;
  const thread = threadByEvidence[evidenceId];
  if (!thread) return;
  const panel = box.closest(".ev-page").querySelector(".ev-thread");
  const isResolved = thread.status === "resolved";
  panel.innerHTML =
    `<div class="ev-thread-comments">` +
    thread.comments.map((c) =>
      `<div class="ev-comment ev-${c.author}"><b>${c.author === "ai" ? "AI 질문" : "내 답변"}</b> ${esc(c.text)}</div>`
    ).join("") + `</div>` +
    (isResolved ? "" :
      `<div class="ev-reply"><textarea rows="2" placeholder="이 질문에 답변해 주세요 — 답하면 재분석에 반영됩니다"></textarea>
       <button type="button" class="ev-reply-btn">답변 등록</button></div>`);
  panel.classList.remove("hidden");
  if (!isResolved) {
    panel.querySelector(".ev-reply-btn").onclick = async () => {
      const text = panel.querySelector("textarea").value.trim();
      if (!text) return;
      await api(`/product/companies/${state.companyId}/threads/${thread.thread_id}/reply`,
        { method: "POST", body: JSON.stringify({ text }) });
      await loadEvidence();   // 상태 갱신 — 핀 색·요약·매칭 게이트 전부 재렌더
    };
  }
}

/* ── ② 프로필 렌더 ───────────────────────────────────────────── */

function provBadge(field) {
  const conf = field.confidence != null ? ` ${Math.round(field.confidence * 100)}%` : "";
  const label = { stated: "확인됨", inferred: "추론됨" + conf, ask: "질문필요" }[field.provenance];
  return `<span class="prov ${field.provenance}">${label}</span>`;
}

function renderProfile(data) {
  const p = data.profile;
  const ev = data.evidence || {};
  const evChips = (name) => (ev[name] || []).map((c) => `<span class="evidence">근거: ${esc(c)}</span>`).join("");
  const vp = (list) => list.length ? list.map((v) => ({ revenue_growth: "매출", cost_reduction: "비용",
      impact: "임팩트", problem_solving: "문제해결" }[v])).join(", ") : "—";
  $("#profile-card").innerHTML = `
    <dl class="profile-grid">
      <dt>회사</dt><dd><b>${esc(p.basic.name)}</b> · ${esc(p.basic.country)}${p.basic.city ? " · " + esc(p.basic.city) : ""} · ${esc(p.basic.industry)}</dd>
      <dt>설명</dt><dd>${esc(p.description) || "—"}</dd>
      <dt>푸는 문제</dt><dd>${esc(p.problem_solved.value) || "—"} ${provBadge(p.problem_solved)}${evChips("problem_solved")}</dd>
      <dt>솔루션</dt><dd>${esc(p.solution.value) || "—"} ${provBadge(p.solution)}${evChips("solution")}</dd>
      <dt>타겟 고객</dt><dd>${esc(p.target_customer.value) || "—"} ${provBadge(p.target_customer)}${evChips("target_customer")}</dd>
      <dt>가치 제안</dt><dd>판매: ${vp(p.sell_value_props)} / 구매: ${vp(p.purchase_value_props)}</dd>
      <dt>레퍼런스</dt><dd>${p.references.map(esc).join(", ") || "없음 (첫 사례)"}</dd>
      <dt>협력 의향</dt><dd>판매: ${p.willingness_sell || "미상"} / 구매: ${p.willingness_purchase || "미상"}</dd>
      <dt>온톨로지</dt><dd>${data.ontology_anchors.map((a) => `<span class="points"><span>${esc(a.category)}: ${esc(a.value)}</span></span>`).join(" ")}</dd>
    </dl>` + renderPortrait(p.portrait);
}

const PORTRAIT_KO = { identity: "정체성", business_model: "수익 구조", edge: "차별화",
  stage_narrative: "단계와 절실함", assets: "가진 것", gaps: "결핍 (사는 쪽 얼굴)",
  risk_signals: "리스크 신호" };

function renderPortrait(pt) {
  if (!pt) return "";   // mock 모드에서는 상이 생성되지 않는다
  return `<div class="panel info" style="margin-top:16px">
    <h3 style="margin:0 0 8px">🔭 회사의 상(像) — 자료의 '결과'에서 역추론한 전략·처지 <small>(전체 추론됨 — 확인·교정해주세요)</small></h3>
    <dl class="profile-grid">${Object.entries(PORTRAIT_KO).map(([k, label]) =>
      `<dt>${label}</dt><dd>${esc(pt[k])}</dd>`).join("")}
    </dl></div>`;
}

/* ── ③ 후보 발굴 ─────────────────────────────────────────────── */

function collectIntent() {
  const vps = [...document.querySelectorAll(".vp-checks input:checked")].map((c) => c.value);
  return {
    value_props: vps.length ? vps : ["revenue_growth"],
    target_region: $("#intent-region").value.trim() || null,
    proposal_type: $("#intent-type").value || null,
  };
}

$("#btn-match").onclick = async () => {
  hideError("#match-error");
  const btn = $("#btn-match"); btn.disabled = true;
  state.intent = collectIntent();
  updateChecklist();
  try {
    const data = await runJob("/product/match",
      { company_id: state.companyId, intent: state.intent, pool: "external", k: 5 },
      $("#match-log"), "match");
    $("#synth").innerHTML = `<b>합성된 이상적 상대상</b> (검색어가 된 문장): ${esc(data.synthesized_counterpart)}`;
    $("#synth").classList.remove("hidden");
    renderCandidates(data.candidates);
  } catch (err) {
    showError("#match-error", err.code === "no_strong_candidate"
      ? "강한 후보 없음 — 엔진이 약한 후보를 억지로 채우지 않았어요. 의도(지역·가치제안)를 바꿔보세요."
      : err.code === "unclear_evidence_unresolved"
      ? "근거 시각화에 확인 필요한 항목이 남아있어요 — 위 '②++ 근거 시각화' 섹션에서 답변해 주세요."
      : `${err.code || ""} ${err.message}`);
    $("#candidates").innerHTML = "";
  } finally { btn.disabled = false; }
};

function renderCandidates(candidates) {
  $("#candidates").innerHTML = candidates.map((c) => `
    <div class="cand" id="cand-${esc(c.company_id)}">
      <div class="cand-head">
        <h3>${esc(c.name)} <small>(${esc(c.country)} · ${esc(c.pool)} 풀)</small></h3>
        <div class="score-bar" title="retrieval score ${c.retrieval_score}"><i style="width:${Math.min(c.retrieval_score * 100, 100)}%"></i></div>
        <button class="j-btn" data-id="${esc(c.company_id)}">판단 실행 (Judge)</button>
      </div>
      <div class="points">${c.match_points.map((p) => `<span>${esc(p)}</span>`).join("")}</div>
      <div class="summary">${esc(c.summary)}</div>
      <div class="judge-area"></div>
    </div>`).join("");
  document.querySelectorAll(".j-btn").forEach((b) => b.onclick = () => judgeCandidate(b.dataset.id, b));
}

/* ── ④ 판단 ─────────────────────────────────────────────────── */

function ensureLogBox(parent) {
  let box = parent.querySelector(":scope > .logbox");
  if (!box) {
    box = document.createElement("div");
    box.className = "logbox hidden";
    parent.prepend(box);
  }
  return box;
}

async function judgeCandidate(candidateId, btn) {
  btn.disabled = true; btn.textContent = "판단 중...";
  const area = $(`#cand-${CSS.escape(candidateId)} .judge-area`);
  const logBox = ensureLogBox(area);
  try {
    const data = await runJob("/product/judge",
      { company_id: state.companyId, candidate_id: candidateId,
        intent: state.intent || collectIntent() }, logBox, "judge");
    state.judged[candidateId] = data.judge_result;
    area.innerHTML = renderJudgment(data.judge_result, candidateId);
    if (logBox._pipe) area.prepend(logBox._pipe);   // 파이프박스도 함께 복원 (innerHTML로 유실 방지)
    area.prepend(logBox);                       // 로그는 결과 위에 유지 (접힘)
    logBox.classList.add("log-collapsed");
    logBox.onclick = () => logBox.classList.toggle("log-collapsed");
    area.querySelector(".c-btn").onclick = (e) => composeDraft(candidateId, e.target);
    area.querySelector(".n-btn").onclick = (e) => negotiateSim(candidateId, e.target);
  } catch (err) {
    const msg = err.code === "deal_breaker"
      ? `🚫 deal-breaker 결렬 — ${esc(err.details?.reason || err.message)} (사람에게 비노출 처리되는 매칭입니다)`
      : esc(`${err.code || ""} ${err.message}`);
    area.insertAdjacentHTML("beforeend", `<div class="error">${msg}</div>`);
  } finally { btn.disabled = false; btn.textContent = "판단 실행 (Judge)"; }
}

const DIM_KO = { industry_fit: "산업 적합성", purpose_alignment: "협업목적 정합",
  resource_complementarity: "자원 보완성", stage_compatibility: "사업단계 호환",
  demonstrability: "실증 가능성", substitute_comparison: "대체재 비교",
  opportunity_cost: "기회비용" };
const VERDICT_KO = { fit: "적합", caution: "주의", unfit: "부적합" };
const DECISION_KO = { recommend: "추천", conditional: "조건부 추천", hold: "보류", terminate: "결렬" };

function renderJudgment(jr, candidateId) {
  return `
    <p><span class="decision d-${jr.decision}">${DECISION_KO[jr.decision]}</span>
       <small>${esc(jr.decision_rationale)}</small></p>
    <table class="verdicts">${jr.category_judgments.map((d) => `
      <tr><td><b>${DIM_KO[d.dimension] || d.dimension}</b></td>
          <td class="v-${d.verdict}">${VERDICT_KO[d.verdict]}</td>
          <td>${esc(d.rationale)}</td></tr>`).join("")}
    </table>
    ${jr.risks.length ? `<div><b style="font-size:13px">확인 리스크</b>${jr.risks.map((r) => `
      <div class="risk-item"><span class="risk-tag rt-${r.type}">${{ precondition: "선결", profitability: "수익성", dismissed: "기각" }[r.type]}</span>${esc(r.description)}</div>`).join("")}</div>` : ""}
    ${jr.deal_structure ? `<p style="font-size:13px"><b>딜 구조:</b> ${esc(jr.deal_structure)}</p>` : ""}
    <details><summary>추론 궤적 (CoT)</summary><pre>${esc(jr.trajectory)}</pre></details>
    <div style="margin-top:12px">
      <button class="c-btn primary">⑤ 콜드메일 초안 (Compose)</button>
      <button class="n-btn">A2A 협상 시뮬레이션</button>
    </div>
    <div class="output-area"></div>`;
}

/* ── ⑤ 초안 · 협상 ──────────────────────────────────────────── */

async function composeDraft(candidateId, btn) {
  btn.disabled = true; btn.textContent = "작성 중...";
  const area = $(`#cand-${CSS.escape(candidateId)} .output-area`);
  const logBox = ensureLogBox(area);
  try {
    const data = await runJob("/product/compose",
      { company_id: state.companyId, candidate_id: candidateId,
        judge_result: state.judged[candidateId], mode: "outreach", variants: 2 }, logBox, "compose");
    area.innerHTML = data.messages.map((m) => `
      <div class="draft">
        <h4>변형 ${esc(m.variant_label)} — ${esc(m.title)}</h4>
        <pre>${esc(m.body)}</pre>
        <small>레퍼런스: ${esc(m.reference_used)} · 주장→근거 추적 ${m.claim_trace.length}건</small>
        <button onclick="navigator.clipboard.writeText(this.previousElementSibling.previousElementSibling.textContent)">복사</button>
      </div>`).join("") +
      `<div class="send-blocked">🔒 send_blocked — 엔진은 초안까지만 생성합니다. 발송은 검토 후 사람이 직접.</div>`;
    if (logBox._pipe) area.prepend(logBox._pipe);
    area.prepend(logBox);
    logBox.classList.add("log-collapsed");
    logBox.onclick = () => logBox.classList.toggle("log-collapsed");
  } catch (err) { area.insertAdjacentHTML("beforeend", `<div class="error">${esc(err.message)}</div>`); }
  finally { btn.disabled = false; btn.textContent = "⑤ 콜드메일 초안 (Compose)"; }
}

async function negotiateSim(candidateId, btn) {
  btn.disabled = true; btn.textContent = "협상 진행 중...";
  const area = $(`#cand-${CSS.escape(candidateId)} .output-area`);
  const logBox = ensureLogBox(area);
  try {
    const data = await runJob("/product/negotiate",
      { company_id: state.companyId, candidate_id: candidateId,
        intent: state.intent || collectIntent(), max_rounds: 3 }, logBox, "negotiate");
    const neg = data.negotiation;
    const RESP_KO = { accept: "✅ 수락", reject: "🚫 거절", counter: "↩ 거절+사유 → 재제안" };
    area.innerHTML = `
      <div class="draft">
        <h4>협상 결과 <span class="term t-${neg.termination}">${{ agreement: "합의", breakdown: "결렬", round_limit: "라운드 상한" }[neg.termination]}</span>
            <small>(${neg.rounds_used}라운드)</small></h4>
        ${neg.rounds.map((r) => `
          <div class="round"><span class="r-num">R${r.round}</span>
            <span>${RESP_KO[r.response]}${r.rejection ? ` — 막힌 차원: <b>${DIM_KO[r.rejection.dimension]}</b> (${r.rejection.recoverable ? "풀리는 거절" : "못 푸는 거절"})` : ""}
            ${r.knobs_adjusted.length ? `<br><small>손잡이 조정: ${r.knobs_adjusted.map((k) => `${esc(k.knob)}→${esc(k.to)}`).join(" · ")}</small>` : ""}</span>
          </div>`).join("")}
        ${data.buyer_simulated ? `<div class="sim-note">⚠ 구매자 사전정보는 시뮬레이션 가상 부여 [시뮬] — 실증은 파일럿에서 (정직 프레이밍)</div>` : ""}
      </div>`;
    if (logBox._pipe) area.prepend(logBox._pipe);
    area.prepend(logBox);
    logBox.classList.add("log-collapsed");
    logBox.onclick = () => logBox.classList.toggle("log-collapsed");
  } catch (err) { area.insertAdjacentHTML("beforeend", `<div class="error">${esc(err.message)}</div>`); }
  finally { btn.disabled = false; btn.textContent = "A2A 협상 시뮬레이션"; }
}

/* ── ②+ AI 컨설턴트 인터뷰 (CON-01~02) ─────────────────────────
   검증된 패턴: 한 번에 하나씩 + 회사 자료에서 도출한 4~6지선다 +
   10슬롯 확정 시 종료·가설 산출. 히스토리는 클라이언트가 보유(SYS-01). */

const SLOT_KO = { solution: "솔루션", pain_point: "pain point", segments: "세그먼트",
  market: "시장", recipient: "수신자", cta: "CTA", proof_points: "proof",
  assets: "제공물", risk: "리스크", follow_up: "후속 흐름" };
const consultState = { history: [], currentQ: null, selected: new Set() };

async function consultNext() {
  hideError("#consult-error");
  const btn = $("#btn-consult");
  btn.disabled = true; btn.textContent = "컨설턴트 생각 중...";
  try {
    const data = await runJob("/product/consult",
      { company_id: state.companyId, history: consultState.history },
      $("#consult-log"), "consult");
    renderConsultTurn(data);
  } catch (err) {
    showError("#consult-error", `${err.code || ""} ${err.message}`);
  } finally { btn.disabled = false; btn.textContent = "인터뷰 계속"; }
}

function renderConsultTurn(data) {
  // 슬롯 진행 표시
  const filled = Object.entries(data.filled || {}).filter(([, v]) => v);
  $("#slot-count").textContent = filled.length;
  $("#slot-names").textContent = filled.map(([k]) => SLOT_KO[k] || k).join(" · ");

  if (data.done) {
    $("#consult-qa").classList.add("hidden");
    $("#btn-consult").classList.add("hidden");
    $("#consult-result").classList.remove("hidden");
    const market = data.filled?.market || "";
    const region = ["유럽", "북미", "미국", "일본", "동남아", "베트남", "태국",
                    "싱가포르", "인도", "중국", "독일", "프랑스", "영국", "MENA"]
      .find((r) => market.includes(r));
    $("#consult-result").innerHTML =
      `<h3>🧭 최종 아웃리치 가설</h3><pre>${esc(data.hypothesis || "")}</pre>` +
      (region ? `<button id="btn-apply-hypo" class="primary">이 가설로 의도 채우기 (지역: ${esc(region)})</button>` : "");
    const apply = $("#btn-apply-hypo");
    if (apply) apply.onclick = () => {
      $("#intent-region").value = region;
      updateChecklist();
      document.querySelector("#step3").scrollIntoView({ block: "start" });
    };
    return;
  }

  consultState.currentQ = data.question;
  consultState.selected = new Set();
  const qa = $("#consult-qa");
  qa.classList.remove("hidden");
  qa.innerHTML = `
    <h3>Q${consultState.history.length + 1}. ${esc(data.question)}</h3>
    <div class="consult-why">💡 ${esc(data.why)}${data.allow_multi ? " (복수 선택 가능)" : ""}</div>
    <div class="consult-opts">${data.options.map((o, i) =>
      `<span class="opt-chip" data-i="${i}" data-label="${esc(o.label)}">${esc(o.label)}<small>${esc(o.hint)}</small></span>`).join("")}
    </div>
    <div class="consult-free">
      <input id="consult-free-input" placeholder="선택지에 없으면 직접 입력·수정하세요 (대표의 답이 우선입니다)">
      <button id="btn-consult-answer" class="primary">답변 제출</button>
    </div>`;
  qa.querySelectorAll(".opt-chip").forEach((chip) => {
    chip.onclick = () => {
      const label = chip.dataset.label;
      if (data.allow_multi) {
        chip.classList.toggle("sel");
        consultState.selected.has(label)
          ? consultState.selected.delete(label) : consultState.selected.add(label);
      } else {
        qa.querySelectorAll(".opt-chip").forEach((c) => c.classList.remove("sel"));
        chip.classList.add("sel");
        consultState.selected = new Set([label]);
      }
    };
  });
  qa.querySelector("#btn-consult-answer").onclick = () => {
    const free = qa.querySelector("#consult-free-input").value.trim();
    const parts = [...consultState.selected];
    if (free) parts.push(free);
    if (!parts.length) return;
    const answer = parts.join(", ");
    consultState.history.push({ question: consultState.currentQ, answer });
    $("#consult-history").insertAdjacentHTML("beforeend",
      `<div class="consult-turn"><div class="ct-q">Q${consultState.history.length}. ${esc(consultState.currentQ)}</div>
       <div class="ct-a">→ ${esc(answer)}</div></div>`);
    qa.classList.add("hidden");
    consultNext();
  };
}

$("#btn-consult").onclick = consultNext;

/* ── 체크리스트 라이브 갱신 ─────────────────────────────────── */

function setCheck(name, ok) {
  const li = document.querySelector(`#checklist [data-check="${name}"]`);
  if (li) li.classList.toggle("ok", !!ok);
}

function updateChecklist(profile) {
  setCheck("assets", document.querySelectorAll(".asset-row").length > 0 &&
    [...document.querySelectorAll(".asset-row input, .asset-row textarea")].some((f) => f.value.trim()));
  setCheck("private", collectPrivateState().length > 0);
  setCheck("willingness", $("#w-sell").value || $("#w-buy").value ||
    (profile && (profile.willingness_sell || profile.willingness_purchase)));
  setCheck("intent", state.intent && state.intent.target_region);
  if (profile) {
    setCheck("problem", profile.problem_solved.value);
    setCheck("solution", profile.solution.value);
    setCheck("target", profile.target_customer.value);
    setCheck("vp", profile.sell_value_props.length + profile.purchase_value_props.length > 0);
  }
}

["#w-sell", "#w-buy", "#private-state"].forEach((sel) =>
  $(sel).addEventListener("input", () => updateChecklist()));

/* 초기 상태: 웹사이트 + 텍스트 입력 한 줄씩 */
addAssetRow("website");
addAssetRow("text");
