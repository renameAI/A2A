"use client";
/* rename. Lead 발굴 워크스페이스 — saas.html 이식 1차 (이슈 #6-F).
 *
 * 배선: /api → Next rewrites → 엔진 /saas. 인증은 dev 헤더(로컬)이며
 * SAAS_AUTH=firebase 전환 시 이 파일의 authHeaders()만 Firebase SDK 토큰으로
 * 바뀐다. 상태는 서버(SaasStore)가 원본 — 새로고침 시 /saas/lead-requests로
 * 복원한다 (saas.html의 메모리 상태 소실 문제 해소).
 */
import { useEffect, useRef, useState } from "react";

type Msg = { who: "agent" | "user" | "stamp"; text: string; jsx?: React.ReactNode };
type Cand = { company_id: string; name: string; name_ko?: string;
  what?: string; signal?: string; source_url: string;
  pain_signal: string; retrieval_score: number; weak: boolean;
  segment?: string; found_by?: string; ontology?: Ont | null };
type Ont = { axes: Record<string, { value: string; status: string }>;
  search_keywords: string[]; confirmed_ratio?: number;
  signals?: { category: string; evidence: string; observed_at: string }[];
  contacts?: { channel: string; value: string; role_hint: string }[] };
type Seg = { label: string; why: string };
type Draft = { subject: string; body: string;
  subject_ko?: string; body_ko?: string; warnings: string[] };
type KwRec = { query: string; score: number; why: string };
type Llm = { provider: "local" | "openai"; label: string; model: string;
  ready: { local: boolean; openai: boolean } };

const DEV_USER = "boram";
function authHeaders(): Record<string, string> {
  return { "X-Dev-User": DEV_USER, "Content-Type": "application/json" };
}

/** body 유무로 메서드를 추측하지 않는다 — /run·/search처럼 본문 없는 POST가
 *  GET으로 나가 404가 났다(실측). 메서드는 호출자가 명시한다. */
async function api(path: string, body?: unknown, method: "GET" | "POST" = "GET") {
  const m = body !== undefined ? "POST" : method;
  const r = await fetch(`/api/saas${path}`, {
    method: m,
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j?.error?.message || "요청 실패"),
    { payload: j?.error });
  return j;
}

async function pollJob(jobId: string): Promise<Record<string, unknown>> {
  for (;;) {
    const r = await fetch(`/api/product/jobs/${jobId}`, { headers: authHeaders() });
    const j = await r.json();
    if (j.status === "done") return j.result;
    if (j.status === "error") throw Object.assign(
      new Error(j.error?.message || "실패"), { payload: j.error });
    await new Promise((res) => setTimeout(res, 1200));
  }
}

export default function Page() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [session, setSession] = useState<string | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [cands, setCands] = useState<Cand[]>([]);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [recs, setRecs] = useState<KwRec[]>([]);
  const [llm, setLlm] = useState<Llm | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [keySaving, setKeySaving] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api("/settings/llm").then(setLlm).catch(() => {}); }, []);

  async function toggleLlm() {
    if (!llm || busy) return;
    if (llm.provider === "local" && !llm.ready.openai) {
      setKeyOpen(true);   // 키가 없으면 전환 대신 입력창을 연다
      return;
    }
    const next = llm.provider === "local" ? "openai" : "local";
    try {
      setLlm(await api("/settings/llm", { provider: next }));
      push({ who: "stamp", text: `모델을 ${next === "openai" ? "GPT Luna" : "EXAONE 로컬"}로 전환했습니다` });
    } catch (e) {
      push({ who: "agent", text: (e as Error).message });
    }
  }

  async function saveKey() {
    const k = keyInput.trim();
    if (!k || keySaving) return;
    setKeySaving(true);
    try {
      const res = await api("/settings/openai-key", { key: k });
      setKeyInput("");
      setKeyOpen(false);
      setLlm(res);   // 저장과 동시에 openai로 전환됨(백엔드 상태 반영)
      push({ who: "stamp",
        text: `OpenAI 키를 등록했습니다 (${res.masked}, ${res.persisted ? ".env에 저장됨" : "이번 실행에만 적용"})` });
      // 저장만으로는 provider가 안 바뀌므로 바로 전환까지 이어준다
      setLlm(await api("/settings/llm", { provider: "openai" }));
    } catch (e) {
      push({ who: "agent", text: (e as Error).message });
    } finally { setKeySaving(false); }
  }

  const push = (m: Msg) => setMsgs((xs) => [...xs, m]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  useEffect(() => {
    push({ who: "agent", text: "안녕하세요. 회사 소개 텍스트를 붙여넣으면 프로필을 만들고, 조건에 맞는 리드를 웹에서 찾아드려요." });
  }, []);

  /** PDF 업로드 → 엔진이 파싱할 Asset으로 변환.
   *  Asset 계약: 업로드 파일은 url에 서버 경로, content는 빈 문자열. */
  async function uploadFiles(files: File[]) {
    const assets: Array<Record<string, string>> = [];
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      // rewrites 프록시는 큰 multipart에서 500이 난다(실측 35MB) —
      // 스트리밍 Route Handler(app/api/upload/route.ts)를 쓴다.
      const r = await fetch("/api/upload", {
        method: "POST", headers: { "X-Dev-User": DEV_USER }, body: fd });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j?.error?.message || `${f.name} 업로드 실패 (PDF만 가능)`);
      }
      const { path } = await r.json();
      assets.push({ type: "ir_deck", content: "", url: path });
    }
    return assets;
  }

  async function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    if (!files.length || busy) return;
    push({ who: "user", text: files.map((f) => f.name).join(", ") });
    setBusy(true);
    try {
      const assets = await uploadFiles(files);
      push({ who: "agent", text: "자료를 읽고 있어요…" });
      await runOnboard("", assets);
    } catch (err) {
      push({ who: "agent", text: (err as Error).message });
      setBusy(false);
    }
  }

  async function runOnboard(text: string,
                            assets?: Array<Record<string, string>>) {
    setBusy(true);
    try {
      let sid = session;
      if (!sid) {
        const s = await api("/onboarding-sessions",
          { assets: assets ?? [{ type: "text", content: text }] });
        sid = s.session_id; setSession(sid);
      } else if (assets) {
        // 이미 세션이 있는데 자료가 더 오면 새 세션으로 시작한다 —
        // 기존 세션의 자산 목록을 갱신하는 API가 아직 없다(정직한 한계).
        const s = await api("/onboarding-sessions", { assets });
        sid = s.session_id; setSession(sid);
      } else {
        await api(`/onboarding-sessions/${sid}/messages`, { answer: text });
      }
      push({ who: "agent", text: "자료를 읽고 있어요…" });
      const { job_id } = await api(`/onboarding-sessions/${sid}/run`, undefined, "POST");
      const res = (await pollJob(job_id)) as {
        needs_answers: boolean;
        session: { current_questions: string[]; profile?: { basic: { name: string } } };
      };
      if (res.needs_answers) {
        setQuestions(res.session.current_questions);
        push({ who: "agent", text: res.session.current_questions[0]
          ?? "회사에 대해 더 알려주세요." });
      } else {
        const name = res.session.profile?.basic?.name ?? "회사";
        push({
          who: "agent", text: `${name} 프로필이 준비됐어요. 승인하면 리드 발굴을 시작할 수 있어요.`,
          jsx: <button className="btn pri" onClick={() => approve(sid!)}>프로필 승인</button>,
        });
      }
    } catch (e) {
      push({ who: "agent", text: (e as Error).message });
    } finally { setBusy(false); }
  }

  async function approve(sid: string) {
    const { version_id } = await api(`/onboarding-sessions/${sid}/approve`, undefined, "POST");
    setVersionId(version_id);
    push({ who: "stamp", text: "프로필을 승인했습니다" });
    push({
      who: "agent", text: "어떤 리드를 찾을까요?",
      jsx: <BriefForm onSubmit={(intent) => createRequest(version_id, intent)} />,
    });
  }

  async function createRequest(vid: string, intent: Record<string, unknown>) {
    setBusy(true);
    try {
      const doc = await api("/lead-requests", {
        title: String(intent.target_region || "") + " " + String(intent.target_type || "리드"),
        profile_version_id: vid, intent,
      });
      setRequestId(doc.request_id);
      push({ who: "stamp", text: "검색 조건을 확정했습니다" });
      push({ who: "agent", text: "검색 기준을 만들고 있어요…" });
      const b = await api(`/lead-requests/${doc.request_id}/search-brief`, undefined, "POST");
      const brief = (await pollJob(b.job_id)) as {
        search_brief: { synthesized_counterpart: string } };
      push({
        who: "agent", text: "이 기준으로 찾을게요.",
        jsx: (
          <div className="card">
            <div className="card-head">검색 기준</div>
            <div className="card-body">
              <div className="persona">{brief.search_brief.synthesized_counterpart}</div>
            </div>
            <div className="card-foot">
              <button className="btn pri"
                onClick={() => askSegments(doc.request_id)}>상대 업종 고르기</button>
            </div>
          </div>
        ),
      });
    } catch (e) { push({ who: "agent", text: (e as Error).message }); }
    finally { setBusy(false); }
  }

  /** 업종을 엔진이 정하지 않고 사용자에게 되묻는다.
   *  한 회사가 노릴 상대 업종은 원래 여러 개이고, 어느 쪽이 맞는지는 이 시장을
   *  아는 사람이 안다. 추측을 선택으로 바꾸는 단계다. */
  async function askSegments(rid: string) {
    setBusy(true);
    push({ who: "stamp", text: "검색 기준을 승인했습니다" });
    push({ who: "agent", text: "어느 업종을 상대로 찾을지 정해볼게요…" });
    try {
      const r = await api(`/lead-requests/${rid}/segments`, undefined, "POST");
      const res = (await pollJob(r.job_id)) as
        { segments: Seg[]; keyword_recommendations: KwRec[] };
      if (!res.segments.length) {
        push({ who: "agent", text: "업종 후보를 만들지 못했어요. 기준 그대로 검색할게요." });
        await runSearch(rid, [], []);
        return;
      }
      push({
        who: "agent",
        text: "이 중에서 찾을 업종을 고르세요. 여러 개 골라도 되고, 업종마다 따로 검색해요.",
        jsx: <SegmentPicker segments={res.segments} recs={res.keyword_recommendations}
          onSubmit={(segs, qs) => runSearch(rid, segs, qs)} />,
      });
    } catch (e) { push({ who: "agent", text: (e as Error).message }); }
    finally { setBusy(false); }
  }

  async function runSearch(rid: string, segments: string[], extra: string[]) {
    setBusy(true);
    push({ who: "user", text: segments.length
      ? segments.join(" · ") : "기준 그대로 검색" });
    push({ who: "agent", text: segments.length > 1
      ? `${segments.length}개 업종을 각각 검색하고 있어요…`
      : "웹에서 후보를 모으고 있어요…" });
    try {
      const s2 = await api(`/lead-requests/${rid}/search`,
        { segments, extra_queries: extra });
      const res = (await pollJob(s2.job_id)) as
        { candidates: Cand[]; keyword_recommendations: KwRec[] };
      setCands(res.candidates);
      setRecs(res.keyword_recommendations || []);
      const bySeg = new Map<string, number>();
      for (const c of res.candidates)
        bySeg.set(c.segment || "", (bySeg.get(c.segment || "") ?? 0) + 1);
      const brk = [...bySeg.entries()].filter(([k]) => k)
        .map(([k, n]) => `${k} ${n}곳`).join(" · ");
      push({ who: "agent", text: `후보 ${res.candidates.length}곳이에요.`
        + (brk ? ` (${brk})` : "")
        + " 저장한 후보만 메일 초안으로 이어져요." });
    } catch (e) {
      const code = (e as { payload?: { code?: string } }).payload?.code;
      push({ who: "agent", text: code === "cost_cap"
        ? (e as Error).message
        : `후보를 찾지 못했어요 — ${(e as Error).message}` });
    } finally { setBusy(false); }
  }

  async function draftMail(cid: string) {
    if (!requestId) return;
    setBusy(true);
    push({ who: "agent", text: "수요 신호를 정리하고 초안을 쓰고 있어요…" });
    try {
      const i = await api(`/lead-requests/${requestId}/candidates/${cid}/insight`, undefined, "POST");
      await pollJob(i.job_id);
      const c = await api(`/lead-requests/${requestId}/candidates/${cid}/compose`, undefined, "POST");
      const res = (await pollJob(c.job_id)) as { drafts: Draft[] };
      const d = res.drafts[0];
      push({
        who: "agent", text: "초안이에요. 발송은 직접 하셔야 해요.",
        jsx: <MailDraft d={d} />,
      });
    } catch (e) { push({ who: "agent", text: (e as Error).message }); }
    finally { setBusy(false); }
  }

  function send() {
    const v = input.trim();
    if (!v || busy) return;
    setInput("");
    push({ who: "user", text: v });
    if (!versionId) { runOnboard(v); return; }
    push({ who: "agent", text: "지금은 위 카드의 버튼으로 진행해 주세요 — 자유 대화 확장은 다음 이슈예요." });
  }

  return (
    <div className="app">
      <nav className="rail" aria-label="워크스페이스">
        <div className="ws" title="rename">r.</div>
        <div className="spacer" />
        <div className="me" title="보람">보</div>
      </nav>

      <aside className="side">
        <div className="side-head">
          <div className="brand">rename<em>.</em>
            <small>Lead 발굴 워크스페이스</small></div>
          <button className="btn-new" title="새 Lead Request"
            onClick={() => location.reload()}>+</button>
        </div>
        <div className="side-scroll">
          <div className="sec">
            <div className="sec-title"><span className="tri">▾</span> 진행 중 Request</div>
            {requestId ? (
              <button className="chan active">
                <span className="hash">#</span>
                <span className="nm">{requestId}</span>
                {cands.length > 0 && <span className="badge">{cands.length}</span>}
              </button>
            ) : <div className="empty">아직 없어요</div>}
          </div>
          <div className="sec">
            <div className="sec-title"><span className="tri">▾</span> 저장한 Lead</div>
            <button className="chan">
              <span className="hash">☆</span>
              <span className="nm">저장 {saved.size}곳</span></button>
          </div>
        </div>
        <div className="side-foot">
          <span className="dot-live" />
          rename 에이전트 온라인 · {llm?.label ?? "…"}
        </div>
      </aside>

      <main className="main">
        <header className="chat-head">
          <h1><span className="hash">#</span> lead-discovery</h1>
          {busy && <span className="pill run">작업 중</span>}
          <span className="topic">
            {versionId ? "프로필 승인됨 · 조건에 맞는 리드를 찾습니다"
              : "회사 소개를 붙여넣으면 프로필부터 만들어요"}
          </span>
          <div className="right">
            {llm && (
              <button className="llm-toggle" onClick={toggleLlm} disabled={busy}
                title={llm.ready.openai
                  ? "클릭해서 모델 전환"
                  : "클릭하면 OpenAI 키를 입력할 수 있어요"}>
                <span className={`opt ${llm.provider === "local" ? "on" : ""}`}>
                  EXAONE 로컬</span>
                <span className={`opt ${llm.provider === "openai" ? "on" : ""}`}>
                  GPT Luna{!llm.ready.openai && " 🔒"}</span>
              </button>
            )}
          </div>
        </header>
        <div className="msgs">
          <div className="day"><span>오늘</span></div>
          {msgs.map((m, i) => m.who === "stamp" ? (
            <div className="stamp" key={i}><b>보람</b>님이 {m.text}</div>
          ) : (
            <div className={`msg ${m.who === "user" ? "me" : "them"}`} key={i}>
              <div className={`ava ${m.who}`}>r.</div>
              <div className="body">
                {m.who === "agent" && (
                  <div className="who">rename 에이전트<span className="tag">앱</span></div>
                )}
                {m.text && (
                  <div className="bubble">
                    {m.text}
                    {busy && i === msgs.length - 1 && m.who === "agent" &&
                      <span className="typing"><i /><i /><i /></span>}
                  </div>
                )}
                {m.jsx && <div className="attach">{m.jsx}</div>}
              </div>
            </div>
          ))}
          {cands.map((c, i) => (
            <div className="msg them" key={c.company_id}>
              <div className="ava agent">r.</div>
              <div className="body">
                <div className="who">rename 에이전트<span className="tag">앱</span></div>
                <div className="bubble">
                  <b>{i + 1}위 · {c.name_ko && c.name_ko !== c.name
                    ? c.name_ko : c.name}</b>
                  {c.name_ko && c.name_ko !== c.name && (
                    <span className="orig"> {c.name}</span>)}
                  {c.segment && <span className="chip seg">{c.segment}</span>}
                  {c.weak && <span className="chip ask">임계 미만</span>}
                  {"\n"}{c.what || c.pain_signal.slice(0, 140)}
                  {c.signal ? `\n\n관측된 신호 — ${c.signal}` : ""}
                </div>
                {(c.ontology?.signals ?? []).length > 0 && (
                  <div className="sig-badges">
                    {c.ontology!.signals!.slice(0, 3).map((sg, i) => (
                      <span className={`sig-cat ${sg.category}`} key={i}
                        title={sg.evidence}>
                        {SIGNAL_KO[sg.category] ?? sg.category}</span>
                    ))}
                  </div>
                )}
                {c.ontology && <OntologyView ont={c.ontology} />}
                <div className="cand-acts">
                  <a className="mini" href={c.source_url} target="_blank"
                    rel="noreferrer">원문</a>
                  <button
                    className={`mini ${saved.has(c.company_id) ? "saved" : ""}`}
                    onClick={() => setSaved((sv) => {
                      const n = new Set(sv);
                      if (n.has(c.company_id)) n.delete(c.company_id);
                      else n.add(c.company_id);
                      return n;
                    })}>
                    {saved.has(c.company_id) ? "저장됨" : "저장"}
                  </button>
                  <button className="mini"
                    onClick={() => draftMail(c.company_id)}>메일 초안</button>
                </div>
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <div className="composer">
          <div className="comp-box">
            <textarea rows={1} value={input}
              placeholder={versionId
                ? "#lead-discovery 에 메시지 보내기"
                : "회사 소개를 붙여넣거나, 질문에 답해주세요"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }} />
            <div className="comp-bar">
              <div className="left">
                <input ref={fileRef} type="file" accept=".pdf" multiple hidden
                  onChange={onPickFiles} />
                <button className="icon-btn" title="자료 올리기 (PDF)"
                  onClick={() => fileRef.current?.click()}>＋</button>
              </div>
              <button className="send" onClick={send} disabled={!input.trim() || busy}
                title="보내기">➤</button>
            </div>
          </div>
          <div className="comp-hint">
            Enter 전송 · Shift+Enter 줄바꿈 · 메일은 초안까지만 — 발송은 항상 사람이 결정해요
          </div>
        </div>
      </main>

      {keyOpen && (
        <div className="modal-backdrop" onClick={() => setKeyOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>OpenAI API 키 입력</h2>
            <p className="modal-sub">여기 붙여넣은 키는 채팅에 남지 않고 엔진으로만 전달돼요.</p>
            <input
              type="password"
              autoFocus
              placeholder="sk-..."
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveKey(); }}
            />
            <div className="modal-actions">
              <button className="btn ghost" onClick={() => setKeyOpen(false)}>취소</button>
              <button className="btn pri" disabled={keySaving || !keyInput.trim()}
                onClick={saveKey}>{keySaving ? "저장 중…" : "저장하고 전환"}</button>
            </div>
          </div>
        </div>
      )}

      <aside className="panel">
        <div className="pstat">
          <div className="cell"><div className="n">{cands.length}</div>
            <div className="l">후보</div></div>
          <div className="cell"><div className="n">{saved.size}</div>
            <div className="l">저장</div></div>
        </div>
        <h3>저장한 후보</h3>
        {saved.size === 0
          ? <div className="empty">후보를 저장하면 여기에 쌓여요</div>
          : [...saved].map((cid) => {
              const c = cands.find((x) => x.company_id === cid);
              return <div className="box" key={cid}><b>{c?.name ?? cid}</b>
                <a href={c?.source_url} target="_blank" rel="noreferrer"
                  style={{ fontSize: 12 }}>{c?.source_url}</a></div>;
            })}
      </aside>
    </div>
  );
}

/** 메일 초안. 원문과 한국어 대역을 탭으로 오간다 —
 *  읽을 수 없는 메일을 승인해 보낼 수는 없다. 대역이 원문과 같으면
 *  (지정 언어가 한국어인 경우) 탭 자체를 띄우지 않는다. */
function MailDraft({ d }: { d: Draft }) {
  const hasKo = !!d.body_ko && d.body_ko !== d.body;
  const [ko, setKo] = useState(hasKo);   // 기본은 읽을 수 있는 쪽
  const sub = ko && hasKo ? (d.subject_ko || d.subject) : d.subject;
  const body = ko && hasKo ? (d.body_ko || d.body) : d.body;
  return (
    <div className="card">
      {hasKo && (
        <div className="mail-tabs">
          <button className={`mail-tab ${ko ? "on" : ""}`}
            onClick={() => setKo(true)}>한국어 대역</button>
          <button className={`mail-tab ${!ko ? "on" : ""}`}
            onClick={() => setKo(false)}>보낼 원문</button>
          {ko && <span className="mail-hint">이건 확인용이에요. 보내는 건 원문입니다.</span>}
        </div>
      )}
      <div className="mail-sub">{sub}</div>
      <div className="mail-body">{body}</div>
      {d.warnings.map((w, k) => (
        <div className="mail-note" key={k}><b>제외됨</b> {w}</div>))}
      <div className="card-foot">
        <button className="btn coral"
          onClick={() => navigator.clipboard.writeText(d.body)}>원문 복사</button>
        {hasKo && (
          <button className="btn"
            onClick={() => navigator.clipboard.writeText(d.body_ko!)}>대역 복사</button>
        )}
      </div>
    </div>
  );
}

const AXIS_KO: Record<string, string> = {
  value_chain_position: "가치사슬 위치", offering: "내놓는 것",
  demand_side: "필요로 하는 것", customer_base: "상대하는 고객",
  geography_scope: "지리 범위", scale_signal: "규모 신호",
  entry_path: "거래 시작 경로", differentiator: "구별되는 점",
  decision_structure: "누가 정하나", innovation_receptivity: "외부 협업 구조",
};

const SIGNAL_KO: Record<string, string> = {
  expansion: "확장", investment: "투자", leadership: "경영진",
  new_offering: "신규 사업", partnership: "제휴", procurement: "조달·모집",
  cost_cutting: "축소", other: "기타",
};

/** 기업마다 남는 판독. 접혀 있다가 펼치면 축과 근거 상태가 그대로 보인다 —
 *  판단 근거를 숨기지 않는 것이 judge와 같은 규율이다. */
function OntologyView({ ont }: { ont: Ont }) {
  const [open, setOpen] = useState(false);
  const known = Object.entries(ont.axes).filter(([, a]) => a.status !== "unknown");
  if (!known.length) return null;
  return (
    <div className="ont">
      <button className="ont-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} 판독 {known.length}/{Object.keys(ont.axes).length}축
        {ont.confirmed_ratio !== undefined &&
          <span className="ont-ratio">근거 확인 {Math.round(ont.confirmed_ratio * 100)}%</span>}
      </button>
      {open && (
        <div className="ont-body">
          {known.map(([k, a]) => (
            <div className="ont-row" key={k}>
              <span className={`ont-st ${a.status}`}>
                {a.status === "confirmed" ? "확인" : "추정"}</span>
              <span className="ont-k">{AXIS_KO[k] ?? k}</span>
              <span className="ont-v">{a.value}</span>
            </div>
          ))}
          {(ont.signals ?? []).length > 0 && (
            <div className="ont-sig-list">
              {ont.signals!.map((sg, i) => (
                <div className="ont-sig" key={i}>
                  <span className={`sig-cat ${sg.category}`}>
                    {SIGNAL_KO[sg.category] ?? sg.category}</span>
                  <span className="sig-ev">{sg.evidence}</span>
                  {sg.observed_at && <span className="sig-at">{sg.observed_at}</span>}
                </div>
              ))}
            </div>
          )}
          {(ont.contacts ?? []).length > 0 && (
            <div className="ont-ct-list">
              {ont.contacts!.map((ct, i) => (
                <div className="ont-ct" key={i}>
                  <span className="ct-ch">{ct.channel}</span>
                  {/^https?:/.test(ct.value)
                    ? <a href={ct.value} target="_blank" rel="noreferrer"
                        className="ct-v">{ct.value.replace(/^https?:\/\//, "").slice(0, 42)}</a>
                    : <span className="ct-v">{ct.value}</span>}
                  {ct.role_hint && <span className="ct-role">{ct.role_hint}</span>}
                </div>
              ))}
            </div>
          )}
          {ont.search_keywords.length > 0 && (
            <div className="ont-kw">이런 회사를 더 찾는 검색어 —{" "}
              {ont.search_keywords.join(" · ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

/** 업종 다중 선택 + 과거 실적 기반 키워드 추천.
 *  추천이 비어 있으면 그 자리를 비운다 — 이력이 없는데 그럴듯한 키워드를
 *  지어내면 추천이 아니라 또 하나의 추측이다. */
function SegmentPicker({ segments, recs, onSubmit }: {
  segments: Seg[]; recs: KwRec[];
  onSubmit: (segs: string[], extra: string[]) => void;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [kws, setKws] = useState<Set<string>>(new Set());
  const [done, setDone] = useState(false);
  const toggle = (set: Set<string>, fn: (s: Set<string>) => void, v: string) => {
    const n = new Set(set); n.has(v) ? n.delete(v) : n.add(v); fn(n);
  };
  return (
    <div className="card" style={{ maxWidth: 620 }}>
      <div className="card-head">상대 업종<span className="meta">여러 개 선택 가능</span></div>
      <div className="card-body">
        {segments.map((sg) => (
          <button key={sg.label} disabled={done}
            className={`seg-opt ${picked.has(sg.label) ? "on" : ""}`}
            onClick={() => toggle(picked, setPicked, sg.label)}>
            <span className="seg-lb">{sg.label}</span>
            <span className="seg-why">{sg.why}</span>
          </button>
        ))}
        {recs.length > 0 && (
          <div className="rec">
            <div className="rec-head">비슷한 기업을 찾았던 검색어</div>
            {recs.map((r) => (
              <button key={r.query} disabled={done}
                className={`rec-opt ${kws.has(r.query) ? "on" : ""}`}
                onClick={() => toggle(kws, setKws, r.query)}>
                <span className="rec-q">{r.query}</span>
                <span className="rec-why">{r.why}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="card-foot">
        <button className="btn pri" disabled={done || picked.size === 0}
          onClick={() => { setDone(true); onSubmit([...picked], [...kws]); }}>
          {picked.size ? `${picked.size}개 업종으로 검색` : "업종을 고르세요"}
        </button>
        <button className="btn" disabled={done}
          onClick={() => { setDone(true); onSubmit([], [...kws]); }}>
          업종 안 나누고 검색
        </button>
      </div>
    </div>
  );
}

function BriefForm({ onSubmit }:
  { onSubmit: (intent: Record<string, unknown>) => void }) {
  const [region, setRegion] = useState("일본");
  const [ttype, setTtype] = useState("독립 호텔");
  const [notes, setNotes] = useState("객실 리노베이션과 운영 개선");
  const [count, setCount] = useState(10);
  const [purpose, setPurpose] = useState<"revenue" | "poc">("revenue");
  return (
    <div className="card">
      <div className="card-head">Lead Request</div>
      <div className="card-body">
        {/* 목적이 다르면 판정 축이 다르다 — 매출은 "살 여력", PoC는 "실험할 구조" */}
        <div className="purpose-row">
          <button className={`purpose-opt ${purpose === "revenue" ? "on" : ""}`}
            onClick={() => setPurpose("revenue")}>
            <b>매출 리드</b><span>실제 구매·조달로 이어질 상대</span>
          </button>
          <button className={`purpose-opt ${purpose === "poc" ? "on" : ""}`}
            onClick={() => setPurpose("poc")}>
            <b>PoC 파트너</b><span>같이 실증·실험할 구조가 있는 상대</span>
          </button>
        </div>
        <div className="frm">
          <label>지역<input value={region}
            onChange={(e) => setRegion(e.target.value)} /></label>
          <label>상대 유형<input value={ttype}
            onChange={(e) => setTtype(e.target.value)} /></label>
          <label>제안 내용<input value={notes}
            onChange={(e) => setNotes(e.target.value)} /></label>
          <label>찾을 수<input type="number" min={1} max={30} value={count}
            onChange={(e) => setCount(+e.target.value || 10)} /></label>
        </div>
      </div>
      <div className="card-foot">
        <button className="btn pri" onClick={() => onSubmit({
          value_props: ["revenue_growth"], target_region: region,
          target_type: ttype, notes, lead_count: count, purpose,
        })}>이 조건으로 후보 찾기</button>
      </div>
    </div>
  );
}
