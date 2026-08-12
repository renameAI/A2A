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
type Cand = { company_id: string; name: string; source_url: string;
  pain_signal: string; retrieval_score: number; weak: boolean };
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
                onClick={() => runSearch(doc.request_id)}>이 기준으로 검색</button>
            </div>
          </div>
        ),
      });
    } catch (e) { push({ who: "agent", text: (e as Error).message }); }
    finally { setBusy(false); }
  }

  async function runSearch(rid: string) {
    setBusy(true);
    push({ who: "stamp", text: "검색 기준을 승인했습니다" });
    push({ who: "agent", text: "웹에서 후보를 모으고 있어요…" });
    try {
      const s = await api(`/lead-requests/${rid}/search`, undefined, "POST");
      const res = (await pollJob(s.job_id)) as { candidates: Cand[] };
      setCands(res.candidates);
      push({ who: "agent", text: `후보 ${res.candidates.length}곳이에요. 저장한 후보만 메일 초안으로 이어져요.` });
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
      const res = (await pollJob(c.job_id)) as {
        drafts: { subject: string; body: string; warnings: string[] }[] };
      const d = res.drafts[0];
      push({
        who: "agent", text: "초안이에요. 발송은 직접 하셔야 해요.",
        jsx: (
          <div className="card">
            <div className="mail-sub">{d.subject}</div>
            <div className="mail-body">{d.body}</div>
            {d.warnings.map((w, k) => (
              <div className="mail-note" key={k}><b>제외됨</b> {w}</div>))}
            <div className="card-foot">
              <button className="btn coral"
                onClick={() => navigator.clipboard.writeText(d.body)}>본문 복사</button>
            </div>
          </div>
        ),
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
            <div className="msg" key={i}>
              <div className={`ava ${m.who}`}>{m.who === "agent" ? "r." : "보"}</div>
              <div className="body">
                <div className="who">
                  {m.who === "agent" ? "rename 에이전트" : "보람"}
                  {m.who === "agent" && <span className="tag">앱</span>}
                </div>
                <p>{m.text}{busy && i === msgs.length - 1 && m.who === "agent" &&
                  <span className="typing"><i /><i /><i /></span>}</p>
                {m.jsx}
              </div>
            </div>
          ))}
          {cands.length > 0 && (
            <div className="msg">
              <div className="ava agent">r.</div>
              <div className="body">
                <div className="card" style={{ maxWidth: 680 }}>
                  <div className="card-head">후보 {cands.length}곳
                    <span className="meta">회사명을 누르면 원문</span></div>
                  <div className="card-body">
                    {cands.map((c, i) => (
                      <div className="cand-row" key={c.company_id}>
                        <div className="rank">{i + 1}위</div>
                        <div className="cand-main">
                          <div className="cand-name">
                            <a href={c.source_url} target="_blank" rel="noreferrer">
                              {c.name}</a>
                            {c.weak && <span className="chip ask"> 임계 미만</span>}
                          </div>
                          <div className="cand-why">{c.pain_signal.slice(0, 120)}</div>
                        </div>
                        <div style={{ display: "flex", gap: 5, flex: "none" }}>
                          <button
                            className={`mini ${saved.has(c.company_id) ? "saved" : ""}`}
                            onClick={() => setSaved((s) => {
                              const n = new Set(s);
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
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
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

function BriefForm({ onSubmit }:
  { onSubmit: (intent: Record<string, unknown>) => void }) {
  const [region, setRegion] = useState("일본");
  const [ttype, setTtype] = useState("독립 호텔");
  const [notes, setNotes] = useState("객실 리노베이션과 운영 개선");
  const [count, setCount] = useState(10);
  return (
    <div className="card">
      <div className="card-head">Lead Request</div>
      <div className="card-body">
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
          target_type: ttype, notes, lead_count: count,
        })}>이 조건으로 후보 찾기</button>
      </div>
    </div>
  );
}
