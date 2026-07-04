/* A2A 매칭엔진 v0 프론트 — 한 사이클: 자료 → 프로필 → 후보 → 판단 → 초안/협상 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { assets: [], dialogue: [], companyId: null, intent: null, judged: {} };

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

/* ── 비동기 job + 라이브 진행 로그 ──────────────────────────────
   LLM 작업(K-EXAONE reasoning)은 수 분 걸린다 — job을 던지고
   1.2초마다 폴링하며 엔진의 사고 과정을 logBox에 실시간 렌더한다. */

function renderLogs(logBox, logs, status) {
  if (!logBox) return;
  logBox.classList.remove("hidden");
  const lines = logs.map((l) =>
    `<div class="log-line"><span class="log-t">${l.t.toFixed(1)}s</span>` +
    `<span class="log-stage">[${esc(l.stage)}]</span> ${esc(l.message)}</div>`).join("");
  const spinner = status === "running" || status === "queued"
    ? `<div class="log-line log-wait">⏳ 진행 중... (K-EXAONE reasoning은 수 분 걸릴 수 있어요)</div>` : "";
  logBox.innerHTML = `<div class="log-head">엔진 진행 로그</div>${lines}${spinner}`;
  logBox.scrollTop = logBox.scrollHeight;
}

async function runJob(path, body, logBox) {
  const { job_id } = await api(path, { method: "POST", body: JSON.stringify(body) });
  while (true) {
    const job = await api(`/product/jobs/${job_id}`);
    renderLogs(logBox, job.logs, job.status);
    if (job.status === "done") return job.result;
    if (job.status === "error") {
      throw Object.assign(new Error(job.error.message), job.error);
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
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

function collectDialogue() {
  const dialogue = [...state.dialogue];
  const ws = $("#w-sell").value, wb = $("#w-buy").value;
  if (ws) dialogue.push({ q: "판매의향", a: ws });
  if (wb) dialogue.push({ q: "구매의향", a: wb });
  document.querySelectorAll("#questions input").forEach((input) => {
    if (input.value.trim()) dialogue.push({ q: input.dataset.q, a: input.value.trim() });
  });
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
    const data = await runJob("/product/onboard", body, $("#onboard-log"));
    state.companyId = data.company_id;
    $("#questions-panel").classList.add("hidden");
    renderProfile(data);
    $("#step2").classList.remove("hidden");
    $("#step3").classList.remove("hidden");
    $("#engine-mode").textContent = `engine: ${data.engine_mode}`;
    $("#engine-mode").className = `badge mode-${data.engine_mode}`;
    updateChecklist(data.profile);
    if (data.open_questions.length) showQuestions(data.open_questions);
  } catch (err) {
    if (err.code === "profile_below_minimum") {
      showQuestions(err.details.open_questions);
      showError("#onboard-error",
        "최소 프로필 기준 미달 — 아래 보강 질문에 답하면 매칭 풀에 들어갈 수 있어요.");
    } else {
      showError("#onboard-error", `${err.code || ""} ${err.message}`);
    }
  } finally { btn.disabled = false; }
}   // eslint-disable-line
$("#btn-onboard").onclick = onboard;
$("#btn-reanalyze").onclick = onboard;

function showQuestions(questions) {
  $("#questions").innerHTML = questions.map((q) =>
    `<label class="block">${esc(q)}<input data-q="${esc(q.split("?")[0].slice(0, 20))}" placeholder="답변"></label>`).join("");
  // 질문 키를 mock 파서가 이해하는 필드명으로 매핑
  const keyMap = [["문제", "문제"], ["해결", "솔루션"], ["방식", "솔루션"],
                  ["팔고", "타겟"], ["타겟", "타겟"], ["가치", "판매가치"], ["의향", "판매의향"]];
  document.querySelectorAll("#questions input").forEach((input) => {
    const q = input.closest("label").textContent;
    const hit = keyMap.find(([kw]) => q.includes(kw));
    if (hit) input.dataset.q = hit[1];
  });
  $("#questions-panel").classList.remove("hidden");
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
      $("#match-log"));
    $("#synth").innerHTML = `<b>합성된 이상적 상대상</b> (검색어가 된 문장): ${esc(data.synthesized_counterpart)}`;
    $("#synth").classList.remove("hidden");
    renderCandidates(data.candidates);
  } catch (err) {
    showError("#match-error", err.code === "no_strong_candidate"
      ? "강한 후보 없음 — 엔진이 약한 후보를 억지로 채우지 않았어요. 의도(지역·가치제안)를 바꿔보세요."
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
        intent: state.intent || collectIntent() }, logBox);
    state.judged[candidateId] = data.judge_result;
    area.innerHTML = renderJudgment(data.judge_result, candidateId);
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
        judge_result: state.judged[candidateId], mode: "outreach", variants: 2 }, logBox);
    area.innerHTML = data.messages.map((m) => `
      <div class="draft">
        <h4>변형 ${esc(m.variant_label)} — ${esc(m.title)}</h4>
        <pre>${esc(m.body)}</pre>
        <small>레퍼런스: ${esc(m.reference_used)} · 주장→근거 추적 ${m.claim_trace.length}건</small>
        <button onclick="navigator.clipboard.writeText(this.previousElementSibling.previousElementSibling.textContent)">복사</button>
      </div>`).join("") +
      `<div class="send-blocked">🔒 send_blocked — 엔진은 초안까지만 생성합니다. 발송은 검토 후 사람이 직접.</div>`;
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
        intent: state.intent || collectIntent(), max_rounds: 3 }, logBox);
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
    area.prepend(logBox);
    logBox.classList.add("log-collapsed");
    logBox.onclick = () => logBox.classList.toggle("log-collapsed");
  } catch (err) { area.insertAdjacentHTML("beforeend", `<div class="error">${esc(err.message)}</div>`); }
  finally { btn.disabled = false; btn.textContent = "A2A 협상 시뮬레이션"; }
}

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
