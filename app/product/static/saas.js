/* rename. Lead 발굴 워크스페이스 — 실제 엔진 배선
 *
 * 붙인 것: /product/upload → /product/onboard(Represent) → /product/match(Retrieve)
 * 안 붙인 것: Compose. /product/compose는 judge_result가 필수인데 SaaS 경로는
 *   Judge를 호출하지 않는다(기획서 §2.3). 가짜 JudgeResult를 만들어 넘기면
 *   수행하지 않은 판정이 로그·UI에 남으므로(§8.1) Compose V2 전까지 비워 둔다.
 */
const API = "/product";
const $ = (s) => document.querySelector(s);
const msgs = $("#msgs");

const state = {
  companyId: null,
  profile: null,
  approved: false,
  dialogue: [],       // [{q, a}] — 보강 답변 누적
  questions: [],
  candidates: [],
  saved: new Set(),
  synth: "",
};

/* ────────── API ────────── */
async function api(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.message || "요청 실패"), { payload: j });
  return j;
}

/** job 폴링 — 생성 중 텍스트(live)를 콜백으로 흘린다. */
function pollJob(jobId, { onLive, onLog } = {}) {
  return new Promise((resolve, reject) => {
    let lastLog = 0;
    const tick = async () => {
      let j;
      try {
        j = await (await fetch(`${API}/jobs/${jobId}`)).json();
      } catch {
        return setTimeout(tick, 1500);          // 네트워크 흔들림은 재시도
      }
      if (onLog && j.logs) {
        j.logs.slice(lastLog).forEach((l) => onLog(l));
        lastLog = j.logs.length;
      }
      if (onLive && j.live) onLive(j.live);
      if (j.status === "succeeded") return resolve(j.result);
      if (j.status === "failed") return reject(Object.assign(new Error(
        j.error?.message || "실패"), { payload: j.error }));
      setTimeout(tick, 900);
    };
    tick();
  });
}

/* ────────── 채팅 렌더 ────────── */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const now = () => new Date().toLocaleTimeString("ko-KR",
  { hour: "numeric", minute: "2-digit" });

function agentMsg(html, { id } = {}) {
  const el = document.createElement("div");
  el.className = "msg";
  if (id) el.id = id;
  el.innerHTML = `<div class="ava agent">r.</div><div class="body">
    <div class="who">rename 에이전트<span class="tag">앱</span>
      <span class="t">${now()}</span></div>
    <div class="content">${html}</div></div>`;
  msgs.appendChild(el);
  scrollBottom();
  return el;
}
function userMsg(text) {
  const el = document.createElement("div");
  el.className = "msg";
  el.innerHTML = `<div class="ava user">보</div><div class="body">
    <div class="who">보람<span class="t">${now()}</span></div><p></p></div>`;
  el.querySelector("p").textContent = text;
  msgs.appendChild(el);
  scrollBottom();
  return el;
}
function systemLine(html) {
  const el = document.createElement("div");
  el.className = "stamp";
  el.innerHTML = `${html} <span class="t">${now()}</span>`;
  msgs.appendChild(el);
  scrollBottom();
  return el;
}
function scrollBottom() { msgs.scrollTop = msgs.scrollHeight; }

/** 진행 메시지 — 단계 바 + 생성 중 텍스트 꼬리 */
function progressMsg(steps, activeIdx) {
  const bar = steps.map((s, i) =>
    `<span class="stp ${i < activeIdx ? "done" : i === activeIdx ? "on" : ""}">${s}</span>`
  ).join("");
  const el = agentMsg(`
    <div class="prog"><div class="steps">${bar}</div></div>
    <p class="prog-line">준비 중<span class="typing"><i></i><i></i><i></i></span></p>
    <div class="live-tail" hidden></div>`);
  return {
    el,
    setStep(i) {
      el.querySelectorAll(".stp").forEach((s, k) => {
        s.classList.toggle("done", k < i);
        s.classList.toggle("on", k === i);
      });
    },
    line(t) {
      el.querySelector(".prog-line").innerHTML =
        `${esc(t)}<span class="typing"><i></i><i></i><i></i></span>`;
    },
    live(v) {
      const tail = el.querySelector(".live-tail");
      if (!v || v.quiet || !v.text) { tail.hidden = true; return; }
      tail.hidden = false;
      tail.textContent = v.text.slice(-360);
      scrollBottom();
    },
    done(t) {
      el.querySelector(".prog-line").textContent = t;
      el.querySelector(".live-tail").hidden = true;
    },
  };
}

/* ────────── 1단계: 자료 업로드 → Represent ────────── */
async function runOnboard(assets) {
  state.lastAssets = assets;   // 보강 답변 후 같은 자산으로 재분석
  const steps = ["프로필", "검색 기준", "후보 수집", "순위", "메일"];
  const p = progressMsg(steps, 0);
  p.line("자료를 읽고 있어요");
  try {
    const { job_id } = await api("/onboard", {
      assets,
      dialogue: state.dialogue,
      company_id: state.companyId,
    });
    const res = await pollJob(job_id, {
      onLive: p.live,
      onLog: (l) => p.line(l.message || l.stage || "분석 중"),
    });
    p.done("프로필을 만들었어요.");
    state.companyId = res.company_id;
    state.profile = res.profile;
    state.questions = res.open_questions || [];
    renderProfileCard(res);
    renderPanelProfile(res);
  } catch (e) {
    p.done("자료만으로는 프로필이 완성되지 않았어요.");
    const d = e.payload?.details || {};
    if (e.payload?.code === "profile_below_minimum") {
      state.questions = d.open_questions || [];
      askClarify(state.questions);
    } else {
      agentMsg(`<p>${esc(e.message)}</p>`);
    }
  }
}

/* 보강 질문 — 한 번에 하나씩 (질문 5공리의 원자성) */
function askClarify(questions) {
  if (!questions.length) {
    agentMsg("<p>자료가 부족한데 물어볼 항목을 못 찾았어요. 회사 설명을 직접 적어주세요.</p>");
    return;
  }
  const q = questions[0];
  agentMsg(`<p>${esc(q)}</p>
    <p style="color:var(--txt-3);font-size:12.5px;margin-top:4px">
      답을 적어주시면 자료와 함께 다시 읽을게요. 남은 질문 ${questions.length - 1}개.</p>`);
  state.pendingQuestion = q;
}

/* ────────── 프로필 카드 ────────── */
function fieldVal(f) { return f?.value || "확인 필요"; }
function provChip(f) {
  const p = f?.provenance;
  if (p === "stated") return `<span class="chip ok">확인됨</span>`;
  if (p === "inferred") return `<span class="chip inf">추론 ${(f.confidence ?? 0).toFixed(1)}</span>`;
  return `<span class="chip ask">확인 필요</span>`;
}

function renderProfileCard(res) {
  const pf = res.profile;
  agentMsg(`
    <p>${esc(pf.basic?.name || "회사")} 프로필이에요. 확인하고 승인해 주세요.</p>
    <div class="card">
      <div class="card-head">기업 프로필<span class="meta">${esc(pf.basic?.industry || "")} ·
        ${esc(pf.basic?.country || "")}</span></div>
      <div class="card-body">
        <dl class="kv">
          <dt>푸는 문제</dt><dd>${esc(fieldVal(pf.problem_solved))} ${provChip(pf.problem_solved)}</dd>
          <dt>솔루션</dt><dd>${esc(fieldVal(pf.solution))} ${provChip(pf.solution)}</dd>
          <dt>타겟 고객</dt><dd>${esc(fieldVal(pf.target_customer))} ${provChip(pf.target_customer)}</dd>
          ${pf.references?.length ? `<dt>레퍼런스</dt><dd>${esc(pf.references.slice(0, 3).join(", "))}</dd>` : ""}
        </dl>
        ${res.mined?.length ? `<div style="margin-top:10px" class="mined">
          <div class="mined-l">자료에서 그대로 뽑은 문장</div>
          ${res.mined.slice(0, 3).map((m) => `<div class="mined-i">${esc(m)}</div>`).join("")}
        </div>` : ""}
      </div>
      <div class="card-foot">
        <button class="btn pri" onclick="approveProfile(this)">프로필 승인</button>
        <button class="btn ghost" onclick="reOnboard()">자료 더 올리기</button>
        <span class="meta" style="margin-left:auto">${res.engine_mode === "llm" ? "K-EXAONE 분석" : esc(res.engine_mode)}</span>
      </div>
    </div>`);
}

window.approveProfile = function (btn) {
  btn.closest(".card-foot").innerHTML =
    `<span style="font-size:12px;color:var(--txt-2)">승인됨.</span>`;
  systemLine(`<b>보람</b>님이 기업 프로필을 승인했습니다`);
  state.approved = true;
  askBrief();
};

/* ────────── 2단계: Brief(Intent) ────────── */
function askBrief() {
  agentMsg(`
    <p>어떤 상대를 찾을까요? 아래를 채우면 검색 기준을 만들어 볼게요.</p>
    <div class="card">
      <div class="card-head">Lead Request</div>
      <div class="card-body">
        <div class="frm">
          <label>지역<input id="f-region" placeholder="일본" value="일본"></label>
          <label>상대 유형<input id="f-type" placeholder="독립 호텔" value="독립 호텔"></label>
          <label>제안 내용<input id="f-note" placeholder="객실 리노베이션과 운영 개선"
            value="객실 리노베이션과 운영 개선"></label>
          <label>찾을 수<input id="f-k" type="number" min="1" max="20" value="5"></label>
        </div>
        <div style="margin-top:10px">
          <span class="chip q" data-vp="revenue_growth">매출 성장</span>
          <span class="chip q" data-vp="cost_reduction">비용 절감</span>
          <span class="chip q" data-vp="impact">임팩트</span>
          <span class="chip q" data-vp="problem_solving">문제 해결</span>
        </div>
        <div style="font-size:11.5px;color:var(--txt-3);margin-top:6px">
          상대가 얻을 가치를 하나 이상 골라주세요.</div>
      </div>
      <div class="card-foot">
        <button class="btn pri" onclick="runMatch(this)">이 조건으로 후보 찾기</button>
      </div>
    </div>`);
  // 가치제안 토글
  msgs.querySelectorAll("[data-vp]").forEach((c) => {
    c.style.cursor = "pointer";
    c.addEventListener("click", () => c.classList.toggle("on"));
  });
  msgs.querySelector('[data-vp="revenue_growth"]').classList.add("on");
  renderPanelBrief();
}

/* ────────── 3단계: Retrieve ────────── */
window.runMatch = async function (btn) {
  const card = btn.closest(".card");
  const vps = [...card.querySelectorAll("[data-vp].on")].map((c) => c.dataset.vp);
  if (!vps.length) { alert("가치제안을 하나 이상 골라주세요."); return; }
  const intent = {
    value_props: vps,
    target_region: card.querySelector("#f-region").value.trim() || null,
    target_type: card.querySelector("#f-type").value.trim() || null,
    notes: card.querySelector("#f-note").value.trim() || null,
  };
  const k = Math.max(1, Math.min(20, +card.querySelector("#f-k").value || 5));
  state.intent = intent; state.k = k;
  btn.closest(".card-foot").innerHTML =
    `<span style="font-size:12px;color:var(--txt-2)">검색하고 있어요.</span>`;
  systemLine(`<b>보람</b>님이 검색 조건을 확정했습니다`);
  renderPanelBrief();

  const p = progressMsg(["프로필", "검색 기준", "후보 수집", "순위", "메일"], 1);
  p.line("상대의 상을 만들고 있어요");
  try {
    const { job_id } = await api("/match", {
      company_id: state.companyId, intent, k, allow_weak: true,
    });
    const res = await pollJob(job_id, {
      onLive: p.live,
      onLog: (l) => {
        const m = l.message || "";
        if (m.includes("검색")) p.setStep(2);
        p.line(m || "검색 중");
      },
    });
    p.setStep(3);
    p.done("순위를 매겼어요.");
    state.synth = res.synthesized_counterpart || "";
    state.candidates = res.candidates || [];
    if (state.synth) renderSearchBrief(state.synth);
    renderCandidates(state.candidates, res);
  } catch (e) {
    p.done("후보를 찾지 못했어요.");
    const code = e.payload?.code;
    if (code === "no_strong_candidate") {
      agentMsg(`<p>지금 조건에 맞는 상대가 풀에 없어요. 지역이나 상대 유형을 넓혀서
        다시 시도해 보시겠어요?</p>`);
    } else if (code === "unclear_evidence_unresolved") {
      agentMsg(`<p>근거가 불명확한 항목에 먼저 답해야 검색할 수 있어요.</p>`);
    } else {
      agentMsg(`<p>${esc(e.message)}</p>`);
    }
  }
};

function renderSearchBrief(synth) {
  agentMsg(`
    <p>이 문장을 기준으로 후보를 골랐어요.</p>
    <div class="card">
      <div class="card-head">검색 기준</div>
      <div class="card-body"><div class="persona">${esc(synth)}</div></div>
    </div>`);
  $("#pv-search-body").innerHTML = `<div class="persona">${esc(synth)}</div>`;
}

function renderCandidates(cands, res) {
  if (!cands.length) {
    agentMsg("<p>표시할 후보가 없어요.</p>");
    return;
  }
  const rows = cands.map((c, i) => {
    const weak = c.weak ? ` <span class="chip ask">임계 미만</span>` : "";
    const pts = (c.match_points || []).slice(0, 2).join(", ");
    return `<div class="cand-row" data-cid="${esc(c.company_id)}">
      <div class="rank">${i + 1}위</div>
      <div class="cand-main">
        <div class="cand-name">${esc(c.name || c.company_id)}
          <span class="loc">${esc(c.country || "")}</span>${weak}</div>
        <div class="cand-why">${esc(c.summary || "설명 없음")}</div>
        ${pts ? `<div class="cand-sig">${esc(pts)}</div>` : ""}
      </div>
      <div class="cand-acts">
        <button class="mini save" onclick="saveCand('${esc(c.company_id)}',this)">저장</button>
        <button class="mini x" onclick="this.closest('.cand-row').classList.toggle('excluded')">제외</button>
      </div>
    </div>`;
  }).join("");

  const lat = [];
  if (res.scorer_latency_ms) lat.push(`스코어러 ${res.scorer_latency_ms}ms`);
  if (res.api_latency_ms) lat.push(`API ${res.api_latency_ms}ms`);

  agentMsg(`
    <p>후보 ${cands.length}곳이에요. 저장한 후보만 다음 단계로 넘어갑니다.</p>
    <div class="card" style="max-width:680px">
      <div class="card-head">후보 ${cands.length}곳<span class="meta">${lat.join(" · ")}</span></div>
      <div class="card-body">${rows}</div>
    </div>
    <div class="reacts">
      <button class="react add">＋</button>
    </div>`);
  renderPanelCands();
}

/* ────────── 저장 / 메일 ────────── */
window.saveCand = function (cid, btn) {
  if (state.saved.has(cid)) { state.saved.delete(cid); btn.classList.remove("saved"); btn.textContent = "저장"; }
  else { state.saved.add(cid); btn.classList.add("saved"); btn.textContent = "저장됨"; }
  renderPanelCands();
};

window.draftMail = function (cid) {
  const c = state.candidates.find((x) => x.company_id === cid);
  agentMsg(`
    <p>${esc(c?.name || cid)} 앞 메일은 아직 만들 수 없어요.</p>
    <div class="card">
      <div class="card-head">메일 초안 준비 중</div>
      <div class="card-body" style="font-size:13px;color:var(--txt-2)">
        지금 엔진의 메일 생성은 적합도 판단 결과를 입력으로 받습니다. 이 화면은 판단을
        거치지 않는 흐름이라 판단 결과가 없어요. 없는 판정을 지어내서 넣으면 근거가
        틀린 메일이 나가므로, 후보의 수요 신호를 직접 입력으로 받는 생성기를 붙일
        때까지 비워 둡니다.
      </div>
    </div>`);
};

/* ────────── 우측 패널 ────────── */
function renderPanelProfile(res) {
  const pf = res.profile;
  $("#pv-prof").innerHTML = `
    <h3>${esc(pf.basic?.name || "기업")} 프로필</h3>
    <div class="box"><b>푸는 문제</b>${esc(fieldVal(pf.problem_solved))}
      <div style="margin-top:4px">${provChip(pf.problem_solved)}</div></div>
    <div class="box"><b>솔루션</b>${esc(fieldVal(pf.solution))}
      <div style="margin-top:4px">${provChip(pf.solution)}</div></div>
    <div class="box"><b>타겟</b>${esc(fieldVal(pf.target_customer))}
      <div style="margin-top:4px">${provChip(pf.target_customer)}</div></div>
    ${pf.portrait ? `<h3>회사의 상</h3>
      <div class="box"><b>단계와 절실함</b>${esc(pf.portrait.stage_narrative || "")}</div>
      <div class="box"><b>결핍</b>${esc(pf.portrait.gaps || "")}</div>` : ""}`;
}
function renderPanelBrief() {
  const i = state.intent;
  $("#pv-brief").innerHTML = i ? `
    <h3>Request Brief</h3>
    <dl class="kv">
      <dt>지역</dt><dd>${esc(i.target_region || "미지정")}</dd>
      <dt>상대 유형</dt><dd>${esc(i.target_type || "미지정")}</dd>
      <dt>제안</dt><dd>${esc(i.notes || "")}</dd>
      <dt>찾을 수</dt><dd>${state.k}곳</dd>
    </dl>
    <h3>검색 기준</h3><div id="pv-search-body"><div class="empty">검색을 시작하면 표시돼요</div></div>`
    : `<div class="empty">프로필을 승인하면 Brief를 만들어요</div>`;
}
function renderPanelCands() {
  $("#stat-total").textContent = state.candidates.length;
  $("#stat-saved").textContent = state.saved.size;
  const w = $("#panel-saved");
  if (!state.saved.size) {
    w.innerHTML = `<div class="empty">후보를 저장하면 여기에 쌓여요</div>`;
    return;
  }
  w.innerHTML = [...state.saved].map((cid) => {
    const c = state.candidates.find((x) => x.company_id === cid);
    return `<div class="box"><b>${esc(c?.name || cid)}</b>${esc(c?.country || "")}
      <div style="margin-top:6px"><button class="mini" onclick="draftMail('${esc(cid)}')">메일 초안</button></div>
    </div>`;
  }).join("");
}

/* ────────── 업로드 ────────── */
window.reOnboard = () => $("#file").click();

$("#file").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  e.target.value = "";
  userMsg(files.map((f) => f.name).join(", "));
  const assets = [];
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`${API}/upload`, { method: "POST", body: fd });
    if (!r.ok) { agentMsg("<p>PDF만 올릴 수 있어요.</p>"); return; }
    const { path } = await r.json();
    // Asset 계약: 업로드 파일은 url에 서버 경로를 담고 content는 빈 문자열
    assets.push({ type: "ir_deck", content: "", url: path });
  }
  runOnboard(assets);
});

/* ────────── 컴포저 ────────── */
const input = $("#input");
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("#send").addEventListener("click", send);

async function send() {
  const v = input.value.trim();
  if (!v) return;
  input.value = "";
  userMsg(v);

  // 보강 질문에 답하는 중이면 dialogue에 쌓고 재분석
  if (state.pendingQuestion) {
    state.dialogue.push({ q: state.pendingQuestion, a: v });
    state.pendingQuestion = null;
    state.questions = state.questions.slice(1);
    if (state.lastAssets) { runOnboard(state.lastAssets); return; }
  }
  if (!state.companyId) {
    agentMsg(`<p>먼저 회사 자료를 올려주세요. 왼쪽 아래 <b>＋</b>로 IR이나 회사소개서
      PDF를 올리면 읽고 프로필을 만들어요.</p>`);
    return;
  }
  agentMsg(`<p>메시지는 받았어요. 지금은 자료 업로드와 후보 검색만 엔진에 연결돼 있어서,
    자유 대화는 아직 응답하지 못해요.</p>`);
}

/* ────────── 패널 탭 ────────── */
document.querySelectorAll(".ptab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".ptab").forEach((x) => x.classList.remove("on"));
    document.querySelectorAll(".pv").forEach((x) => x.classList.remove("on"));
    t.classList.add("on");
    $("#" + t.dataset.pv).classList.add("on");
  });
});

/* ────────── 시작 ────────── */
agentMsg(`<p>안녕하세요. 회사 자료를 올리면 읽고 프로필을 만든 다음, 조건에 맞는 상대를
  찾아드려요. IR이나 회사소개서 PDF를 <b>＋</b>로 올려주세요.</p>`);
