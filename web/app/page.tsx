"use client";
/* rename. Lead 발굴 워크스페이스 — saas.html 이식 1차 (이슈 #6-F).
 *
 * 배선: /api → Next rewrites → 엔진 /saas. 인증은 supabase.ts가 담당한다 —
 * Supabase env가 있으면 매직링크 세션 토큰, 없으면 로컬 dev 헤더.
 * 상태는 서버(SaasStore)가 원본 — 새로고침 시 /saas/lead-requests로
 * 복원한다 (saas.html의 메모리 상태 소실 문제 해소).
 */
import { memo, useEffect, useRef, useState } from "react";
import { authHeaders, DEV_USER, emailLoginEnabled, isConfigured,
  isMisconfigured, supabase } from "./supabase";

// kind: "candidates" — 후보 캐러셀이 붙는 자리. jsx로 박아 넣지 않는 이유는
// 스냅샷이 되어 👍/저장/판독 갱신이 반영되지 않기 때문이다. 자리만 표시하고
// 렌더는 현재 상태로 한다.
type Msg = { who: "agent" | "user" | "stamp"; text: string;
  jsx?: React.ReactNode; kind?: "candidates" };
type Cand = { company_id: string; name: string; name_ko?: string;
  what?: string; signal?: string; source_url: string;
  pain_signal: string; retrieval_score: number; match?: number; weak: boolean;
  segment?: string; found_by?: string; ontology?: Ont | null;
  p?: number; source_kind?: string; partial?: boolean; reach_fact?: boolean;
  hunter?: { status: string;
    contacts: { email: string; type: string; confidence: number;
      position?: string; department?: string; name?: string;
      sources: string[] }[] };
  deep_read?: { status: string; note?: string; contacts?: number; signals?: number;
    site?: string; chars?: number;
    pages?: { url: string; kind: string; chars: number }[] } };
const SRC_LABEL: Record<string, string> = {
  own: "자사 페이지", directory: "디렉터리·협회", mention: "기사·언급" };
type Ont = { reachability?: number | null; reachability_why?: string;
  why_now?: string; why_now_source?: string;
  reading?: { situation?: string; fit?: string; inference?: string;
              unknowns?: string[] };
  axes: Record<string, { value: string; status: string; fit?: number | null; why?: string }>;
  search_keywords: string[]; confirmed_ratio?: number;
  signals?: { category: string; evidence: string; observed_at: string;
    source_url?: string }[];
  contacts?: { channel: string; value: string; role_hint: string }[] };
type Seg = { label: string; why: string; reach?: string };
const REACH_TAG: Record<string, [string, string]> = {
  // reach는 '닿을 가능성' — 클수록 좋다. 후보의 reachability와 같은 방향이며,
  // 색도 그 방향을 따른다(high=초록). 한때 업종 쪽만 '문턱'(클수록 나쁨)이라
  // 반대였는데, 라벨과 색이 뜻과 어긋나는 사고의 원인이 된다.
  low: ["가능성 낮음", "ask"], mid: ["가능성 중간", "inf"],
  high: ["가능성 높음", "ok"] };
type Draft = { subject: string; body: string;
  subject_ko?: string; body_ko?: string; warnings: string[];
  // 변종 라벨 — 초안이 여럿이면 서버가 "어느 것을 보낼지" 묻는다
  variant_label?: string };
type Recipient = { email: string; confidence: number; position?: string;
  name?: string; sources: string[];
  verify?: { result: string; score: number; smtp: boolean } };
/** 진행 문구를 사람의 말로.
 *
 * 엔진 로그는 개발자가 읽으라고 쓴 것이라 그대로 노출하면 예외 클래스명
 * (`KeyError`)·임계값(`p < 0.2`)·내부 용어(`온톨로지 판독`, `히트`)가 그대로
 * 보인다. 사용자는 "지금 뭘 하고 있나"만 알면 되고, 그건 짧은 한 문장이다.
 * 규칙에 걸리지 않으면 원문을 쓴다 — 모르는 상태를 "처리 중"으로 뭉개면
 * 오래 걸릴 때 멈춘 것처럼 보인다. */
function humanTick(raw: string): string {
  const t = (raw || "").trim();
  if (!t) return "생각하는 중";
  // 순서가 곧 우선순위다 — 좁은 규칙을 먼저 둔다. "웨이브 1: 웹 수집"이
  // '수집'에 걸려 자료 읽기로 새거나, "질문 선별 … 중복성 점검"이 '중복'에
  // 걸려 병합으로 새는 일이 실제로 있었다.
  const rules: [RegExp, string][] = [
    [/질문|명확화|open_questions/, "더 좁히기 위한 질문을 고르는 중"],
    [/Tavily|웹 수집|검색 결과|웨이브/, "웹에서 후보를 모으는 중"],
    [/검색어|질의|쿼리/, "검색어를 만드는 중"],
    [/비기업 도메인|도메인 필터/, "블로그·뉴스 같은 곳을 걸러내는 중"],
    [/심층 판독|사이트 본문|크롤|자료를 읽고/, "회사 사이트를 읽는 중"],
    [/실존 기업|추출|탈락/, "찾은 곳이 실제 회사인지 확인하는 중"],
    [/온톨로지|판독/, "각 회사가 어떤 곳인지 살펴보는 중"],
    [/병합|같은 회사/, "같은 회사가 겹친 것을 정리하는 중"],
    [/재랭킹|정렬|순위/, "잘 맞는 순서로 줄 세우는 중"],
    [/인사이트|수요 신호/, "이 회사에 무엇을 제안할지 정리하는 중"],
    [/메일|초안|compose/, "메일 초안을 쓰는 중"],
    [/프로필|represent/, "회사 프로필을 만드는 중"],
    [/접점|연락/, "연락할 곳을 찾는 중"],
    [/텍스트 생성|생성 대기/, "글을 쓰는 중"],
    [/정상 완료|완료되었습니다/, "마무리하는 중"],
    // 모델 호출의 내부 단계 — 사용자에게는 전부 '생각하는 중' 하나다.
    // (스키마 구조화 / 근거 매핑 / 호출 시작 / 응답 수신 / 토큰·엔드포인트…)
    // 호출 로그가 먼저다 — "호출 시작 … 스키마 강제 …"가 '스키마'에 걸려
    // 근거 정리로 새는 일이 있었다(실측).
    [/^▶?\s*시작$|^시작 —|호출 시작|응답 수신|max_tokens|endpoint|reasoning|finish=|timeout/,
     "생각하는 중"],
    [/스키마 구조화|근거 매핑|evidence_chunk|provenance/, "근거를 정리하는 중"],
    [/1층|2층|3층|4층|5층|상\(像\)|독해/, "회사를 여러 각도로 읽는 중"],
  ];
  for (const [re, label] of rules) if (re.test(t)) return label;
  // 규칙에 없으면 원문을 쓰되, 개발자용 찌꺼기만 떼어낸다.
  // 규칙에 없더라도 영문 식별자·경로·토큰 수치가 섞였으면 사용자용 문구가
  // 아니다. 원문을 노출하기보다 중립 문구로 돌린다.
  if (/[a-z_]{4,}=|https?:\/\/|[a-z]+\.[a-z]{2,}\/|_id|tokens?\b/i.test(t))
    return "생각하는 중";
  return t
    .replace(/\([A-Za-z]*Error\)/g, "")        // (KeyError)
    .replace(/\s*\(p\s*<\s*[\d.]+\)/g, "")     // (p < 0.2)
    .replace(/^[⚠·\-\s]+/, "")
    .trim() || "생각하는 중";
}

type PipeRow = { request_id: string; request_title: string; company_id: string;
  name: string; source_url: string; drafted: boolean; replied: string;
  note: string; stage: string; opened?: boolean };
type Pipeline = { stages: string[]; board: Record<string, PipeRow[]>; total: number };
type TrackLead = { company_id: string; request_id: string;
  request_title?: string; name: string;
  source_url?: string; sent_at: number | null; opened_at: number | null;
  open_count: number; replied_at: number | null; bounced_at: number | null };
type Funnel = { sent: number; opened: number; replied: number; bounced: number;
  open_rate: number; reply_rate: number; bounce_rate: number };
type TrackerData = { leads: TrackLead[]; total: number; funnel: Funnel;
  by_day: { date: string; sent: number }[] };
type OutreachEvent = { at: number; event: string; label: string;
  name: string; source_url?: string; request_id: string; company_id: string };
const STAGE_LABEL: Record<string, string> = {
  saved: "저장", contacted: "연락함", replied: "답장", meeting: "미팅",
  won: "성사", lost: "종료" };
type OutreachKit = { to_role?: string; channel?: string; channel_value?: string;
  why_now?: string; hook?: string };
type KwRec = { query: string; score: number; why: string };
type ProfileDoc = {
  basic: { name: string; country?: string; industry?: string };
  description?: string;
  problem_solved?: { value: string };
  solution?: { value: string };
  target_customer?: { value: string };
};
type Usage = { month: string; workspace_usd: number; workspace_cap_usd: number;
  global_usd: number; global_cap_usd: number; estimated: boolean };
type ReqSummary = { request_id: string; title: string; status: string;
  candidate_count: number; wave: number; target_region: string;
  purpose: string };
type ClarifyQ = { id: string; question: string; axis: string; why: string;
  options: { label: string; company_ids: string[] }[] };
type Llm = { provider: "local" | "openai"; label: string; model: string;
  ready: { local: boolean; openai: boolean } };


/** body 유무로 메서드를 추측하지 않는다 — /run·/search처럼 본문 없는 POST가
 *  GET으로 나가 404가 났다(실측). 메서드는 호출자가 명시한다. */
async function api(path: string, body?: unknown,
                   method: "GET" | "POST" | "DELETE" = "GET") {
  const m = method !== "GET" ? method
    : (body !== undefined ? "POST" : "GET");
  const r = await fetch(`/api/saas${path}`, {
    method: m,
    headers: await authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    // FastAPI의 HTTPException은 {detail}로, 엔진 오류는 {error:{message}}로
    // 온다. detail을 안 보면 401/403이 "요청 실패" 다섯 글자가 된다 —
    // 허용 목록 밖 사용자가 로그인에 성공하고도 이유를 모르던 경로.
    const msg = j?.error?.message ?? (typeof j?.detail === "string" ? j.detail : null)
      ?? `요청 실패 (${r.status})`;
    throw Object.assign(new Error(msg),
      { payload: j?.error, status: r.status });
  }
  return j;
}



const POLL_MAX_MS = 15 * 60_000;   // 엔진 job 타임아웃(900s)과 맞춘 상한
const POLL_FAIL_MAX = 5;           // 연속 통신 실패 허용치

/** job 폴링 — 반드시 끝난다.
 *
 * 이전 판은 for(;;)에 !r.ok 검사도 없어, 서버가 죽거나 job이 사라지면
 * 스피너가 영원히 돌았다(취소 버튼도 없었다). 세 가지를 보장한다:
 * 상한 시간, 연속 실패 상한, 그리고 호출자가 건 취소 신호.
 * onTick으로 진행 로그를 흘려보내 사용자가 무슨 일이 일어나는지 본다.
 */
async function pollJob(jobId: string, opts: {
  signal?: AbortSignal;
  onTick?: (logs: { stage?: string; message?: string;
                    data?: Record<string, unknown> }[],
            elapsed: number) => void;
} = {}): Promise<Record<string, unknown>> {
  const started = Date.now();
  let fails = 0;
  for (;;) {
    if (opts.signal?.aborted) throw new Error("작업을 취소했어요.");
    if (Date.now() - started > POLL_MAX_MS)
      throw new Error("15분이 지나도 끝나지 않아 기다리기를 멈췄어요. "
        + "같은 조건으로 다시 누르면 찾아둔 후보는 이어받아요.");
    try {
      const r = await fetch(`/api/saas/jobs/${jobId}`,
        { headers: await authHeaders(), signal: opts.signal });
      if (!r.ok) {
        if (r.status === 401 || r.status === 403 || r.status === 404) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j?.error?.message ?? `작업을 찾을 수 없어요 (${r.status})`);
        }
        throw new Error(`서버 오류 (${r.status})`);
      }
      const j = await r.json().catch(() => null);
      if (!j) throw new Error("응답을 읽지 못했어요");
      fails = 0;
      if (j.status === "done") return j.result;
      // 인스턴스 소멸로 끊긴 작업은 재개가 답이다 — 그 사실을 알려야
      // 사용자가 '실패했으니 처음부터'라고 오해하지 않는다.
      if (j.status === "error" && /중단되었습니다/.test(j.error?.message ?? ""))
        throw new Error("작업이 중간에 끊겼어요. 같은 조건으로 다시 누르면 "
          + "찾아둔 후보를 이어받아 계속합니다.");
      if (j.status === "error") throw Object.assign(
        new Error(j.error?.message || "실패"), { payload: j.error });
      opts.onTick?.(j.logs ?? [], j.elapsed ?? 0);
    } catch (e) {
      // 취소·명시적 실패는 즉시 올린다. 일시적 통신 실패만 재시도한다.
      if (opts.signal?.aborted) throw new Error("작업을 취소했어요.");
      if ((e as { payload?: unknown }).payload
          || (e as Error).message?.startsWith("작업을 찾을 수 없어요")) throw e;
      if (++fails >= POLL_FAIL_MAX)
        throw new Error(`서버와 연결이 끊겼어요 (${(e as Error).message})`);
    }
    await new Promise((res) => setTimeout(res, 1200));
  }
}

/** 메일로 받은 일회용 코드의 자릿수 (Supabase의 mailer_otp_length).
 *
 * 이 값은 화면 힌트와 자동 제출에만 쓴다 — 서버 설정이 바뀌어도 로그인이
 * 막히지 않도록, 제출 버튼은 6자리 이상이면 항상 열어둔다. 길이를 하드
 * 게이트로 쓰면 설정 한 줄 바뀔 때 아무도 못 들어온다. */
const OTP_LEN = 8;
const OTP_MIN = 6;      // 자동 제출은 안 해도 수동 제출은 허용하는 하한
const RESEND_COOLDOWN = 60;   // GoTrue가 동일 사용자에게 강제하는 재요청 간격(초)

/** Supabase(영문) 오류를 사용자가 행동할 수 있는 한국어로 옮긴다.
 *
 * 원문을 그대로 띄우면 "Token has expired or is invalid"를 본 사용자가
 * 무엇을 해야 하는지 모른다. 매칭에 실패하면 원문을 그대로 보여준다 —
 * 모르는 오류를 '알 수 없는 오류'로 뭉개면 디버깅 단서가 사라진다. */
function loginError(raw: string): string {
  const m = raw.toLowerCase();
  if (m.includes("rate limit") || m.includes("over_email_send_rate"))
    return "메일 발송 한도에 걸렸어요. 잠시 후 다시 시도해 주세요.";
  if (m.includes("only request this after") || m.includes("60 seconds"))
    return "방금 코드를 보냈어요. 1분 뒤에 다시 요청할 수 있어요.";
  // 순서 주의: 아래 코드 만료 규칙이 "invalid"를 넓게 잡으므로, 그보다
  // 좁은 문구를 먼저 본다. 실측으로 걸렸던 버그다 — 비밀번호가 틀렸는데
  // "Invalid login credentials"가 'invalid'에 먼저 걸려 "코드가 맞지 않다"고
  // 안내했다. 사용자는 있지도 않은 코드를 다시 받으러 간다.
  //
  // 비밀번호가 틀린 경우와 비밀번호가 아예 설정되지 않은 계정에 대해
  // Supabase는 같은 문구를 준다 — 어느 계정에 비밀번호가 있는지 알려주지
  // 않기 위해서다. 우리도 그 구분을 화면에 드러내지 않는다.
  if (m.includes("invalid login credentials"))
    return "이메일 또는 비밀번호가 올바르지 않아요.";
  if (m.includes("email logins are disabled"))
    return "이 프로젝트에서 비밀번호 로그인이 꺼져 있어요.";
  if (m.includes("expired") || m.includes("invalid"))
    return "코드가 맞지 않거나 만료됐어요. 다시 받아 주세요.";
  if (m.includes("signups not allowed") || m.includes("not authorized"))
    return "허용된 계정이 아니에요. 관리자에게 접근을 요청해 주세요.";
  if (m.includes("failed to fetch") || m.includes("network"))
    return "네트워크에 연결하지 못했어요. 연결을 확인해 주세요.";
  return raw;
}

type Stage = "email" | "code" | "password";

/** 첫 화면을 무엇으로 열 것인가.
 *
 * 메일 발송이 켜져 있으면 '코드 받기'가 주 경로다. 꺼져 있으면 보낼 것이
 * 없으므로 비밀번호를 먼저 보여준다 — 이때 코드 경로는 관리자가 발급한
 * 코드를 쓰는 보조 수단으로 남는다(scripts/issue_login_code.py). */
function defaultStage(): Stage {
  return emailLoginEnabled ? "email" : "password";
}

/** 로그인 게이트. Supabase 미설정이면 통과(로컬 dev) — 설정 여부를 화면이
 *  드러내므로 '인증이 조용히 사라진' 상태가 생기지 않는다.
 *
 * 링크가 아니라 **코드**로 들어온다. 이유는 두 가지 실측 실패 모드다:
 * 1) 기업 메일 보안(Microsoft Safe Links·Proofpoint·Mimecast)이 배달 전에
 *    링크를 미리 열어본다. 매직링크는 일회용이라 스캐너가 먼저 소진하고,
 *    사용자는 '만료된 링크'를 본다. supabase/auth#1214가 2023년부터 열려
 *    있다 — 우리 쪽에서 고칠 수 있는 문제가 아니다.
 * 2) 데스크톱에서 요청하고 폰에서 메일을 열면 세션이 폰에 생긴다.
 * 코드는 URL이 아니라서 스캐너가 클릭할 것이 없고, 눈으로 읽어 옮겨 적으므로
 * 기기가 갈려도 시작한 화면에서 로그인이 끝난다.
 *
 * 링크 경로도 계속 동작한다(emailRedirectTo 유지) — 메일 템플릿이 링크를
 * 보내는 동안에도 로그인이 되어야 하므로, 코드 전환은 단절이 아니라 추가다. */
export default function Gate() {
  const [ready, setReady] = useState(!isConfigured);
  const [stage, setStage] = useState<Stage>(defaultStage());
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [who, setWho] = useState("");

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => {
      setWho(data.session?.user?.email ?? "");
      setReady(Boolean(data.session));
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, sess) => {
      setWho(sess?.user?.email ?? "");
      setReady(Boolean(sess));
      // 세션이 사라지면(로그아웃·만료) 로그인 폼을 처음 상태로 되돌린다.
      // Gate는 언마운트되지 않으므로 stage/code가 그대로 남는다 — 실측:
      // 로그아웃 직후 '코드를 보냈어요' 화면에 **직전 인증코드가 입력된 채**
      // 다시 나타났다. 공용 화면에서는 그 코드가 그대로 노출된다.
      if (!sess) {
        setStage(defaultStage());
        setCode(""); setPassword(""); setErr(""); setCooldown(0);
      }
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  // 재요청 쿨다운 — 서버가 어차피 60초를 강제하므로, 눌러도 실패하는 버튼을
  // 열어두는 대신 남은 시간을 보여준다(실패를 겪게 하지 않는다).
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  // 자동 제출 — 자릿수가 다 차면 사용자가 버튼을 찾지 않아도 된다.
  useEffect(() => {
    if (stage === "code" && code.length === OTP_LEN && !busy
        && email.trim()) verify();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, stage]);

  // 설정 누락을 dev 폴백으로 덮지 않는다 — 배포 사고를 화면이 말한다
  if (isMisconfigured) return (
    <div className="login">
      <div className="login-box">
        <div className="login-brand">rename<em>.</em></div>
        <p className="login-msg">
          인증 설정이 빠진 채 배포됐어요.<br />
          <b>NEXT_PUBLIC_SUPABASE_URL</b>과 <b>NEXT_PUBLIC_SUPABASE_ANON_KEY</b>를
          Vercel 프로젝트 환경변수에 넣고 다시 배포해 주세요.
        </p>
        <p className="login-note">
          이 화면은 인증 없이 서비스가 뜨는 것을 막기 위한 것입니다.
        </p>
      </div>
    </div>
  );
  if (ready) return <Workspace who={who || (isConfigured ? "" : DEV_USER)} />;

  return (
    <div className="login">
      <div className="login-box">
        <div className="login-brand">rename<em>.</em></div>
        <div className="login-sub">Lead 발굴 워크스페이스</div>

        {stage === "password" ? (
          <>
            <input className="login-input" type="email" value={email}
              placeholder="회사 이메일" autoFocus autoComplete="username"
              disabled={busy}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && signInPassword()} />
            <input className="login-input" type="password" value={password}
              placeholder="비밀번호" autoComplete="current-password"
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && signInPassword()} />
            <button className="btn pri login-btn" onClick={signInPassword}
              disabled={busy || !email.trim() || !password}>
              {busy ? "확인 중…" : "로그인"}
            </button>
            <div className="login-alt">
              <button className="linky" onClick={() => goStage("code")}
                disabled={busy}>
                코드로 로그인
              </button>
            </div>
            <p className="login-note">
              비밀번호는 설정된 계정만 쓸 수 있어요.
              없으면 코드로 로그인해 주세요.
            </p>
          </>
        ) : stage === "email" ? (
          <>
            <input className="login-input" type="email" value={email}
              placeholder="회사 이메일" autoFocus autoComplete="email"
              disabled={busy}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendCode()} />
            <button className="btn pri login-btn" onClick={sendCode}
              disabled={busy || !email.trim()}>
              {busy ? "보내는 중…" : "인증 코드 받기"}
            </button>
            <div className="login-alt">
              <button className="linky" onClick={() => goStage("password")}
                disabled={busy}>
                비밀번호로 로그인
              </button>
            </div>
            <p className="login-note">
              메일로 받은 코드로 들어옵니다.
              허용된 계정만 접근할 수 있어요.
            </p>
          </>
        ) : (
          <>
            {emailLoginEnabled ? (
              <p className="login-msg">
                <b>{email}</b>으로<br />{OTP_LEN}자리 코드를 보냈어요.
              </p>
            ) : (
              <input className="login-input" type="email" value={email}
                placeholder="회사 이메일" autoComplete="email" disabled={busy}
                onChange={(e) => setEmail(e.target.value)} />
            )}
            {/* maxLength를 DOM에 걸지 않는다 — 브라우저는 숫자만 남기기
                *전에* 원본 길이로 잘라서, 오타로 문자가 한 번 섞이면 그만큼
                자릿수를 잃는다(실측: "12ab345678" → "123456"). 길이 제한은
                숫자만 걸러낸 뒤 코드가 건다. 붙여넣기도 같은 이유로 여기서만
                자른다. */}
            <input className="login-input login-code" value={code}
              autoFocus inputMode="numeric" autoComplete="one-time-code"
              disabled={busy}
              onChange={(e) =>
                setCode(e.target.value.replace(/\D/g, "").slice(0, OTP_LEN))}
              onKeyDown={(e) => e.key === "Enter" && verify()} />
            <button className="btn pri login-btn" onClick={verify}
              disabled={busy || code.length < OTP_MIN || !email.trim()}>
              {busy ? "확인 중…" : "로그인"}
            </button>
            {emailLoginEnabled ? (
              <>
                <div className="login-alt">
                  <button className="linky" onClick={sendCode}
                    disabled={busy || cooldown > 0}>
                    {cooldown > 0
                      ? `코드 다시 받기 (${cooldown}초)` : "코드 다시 받기"}
                  </button>
                  <button className="linky" onClick={backToEmail}
                    disabled={busy}>
                    다른 이메일로
                  </button>
                </div>
                <p className="login-note">
                  메일이 안 보이면 스팸함도 확인해 주세요.
                  코드는 1시간 뒤 만료됩니다.
                </p>
              </>
            ) : (
              <>
                <div className="login-alt">
                  <button className="linky" onClick={() => goStage("password")}
                    disabled={busy}>
                    비밀번호로 로그인
                  </button>
                </div>
                <p className="login-note">
                  지금은 메일 발송이 꺼져 있어요.<br />
                  관리자에게 로그인 코드를 요청해 주세요.
                </p>
              </>
            )}
          </>
        )}
        {err && <p className="login-err">{err}</p>}
      </div>
    </div>
  );

  function backToEmail() {
    setStage("email"); setCode(""); setErr("");
  }

  /** 경로 전환. 이전 경로에서 뜬 오류·입력을 끌고 가지 않는다 —
   *  '비밀번호가 틀렸다'가 코드 화면에 남아 있으면 무엇이 문제인지 모른다. */
  function goStage(next: Stage) {
    setStage(next); setErr(""); setCode(""); setPassword("");
  }

  async function signInPassword() {
    if (!supabase || !email.trim() || !password || busy) return;
    setBusy(true); setErr("");
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(), password,
    });
    setBusy(false);
    // 성공하면 onAuthStateChange가 세션을 받아 ready로 넘어간다.
    if (error) { setErr(loginError(error.message)); setPassword(""); }
  }

  async function sendCode() {
    // 발송이 꺼져 있으면 여기까지 오는 경로가 없어야 하지만, 화면에서 버튼을
    // 감추는 것과 함수가 호출되지 않는 것은 다른 보장이다. 스위치는 호출
    // 지점에서 막는다.
    if (!emailLoginEnabled) return;
    if (!supabase || !email.trim() || busy) return;
    setBusy(true); setErr(""); setCode("");
    // emailRedirectTo를 유지해 링크 경로도 살려둔다 — 템플릿이 코드로 바뀌기
    // 전까지는 메일에 링크가 담기므로, 링크를 눌러도 로그인이 되어야 한다.
    const { error } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      // 로그인 화면은 계정을 만들지 않는다 — 만들면 아무 주소나 입력해
      // 시간당 2통뿐인 메일 쿼터를 태울 수 있다(무인증 서비스 거부).
      // 새 사용자는 운영자가 만든다(scripts의 admin 경로). anon 키로 API를
      // 직접 치는 경로는 대시보드의 disable_signup이 마저 닫는다.
      options: { emailRedirectTo: window.location.origin,
                 shouldCreateUser: false },
    });
    setBusy(false);
    if (error) { setErr(loginError(error.message)); return; }
    setStage("code"); setCooldown(RESEND_COOLDOWN);
  }

  async function verify() {
    if (!supabase || code.length < OTP_MIN || busy) return;
    setBusy(true); setErr("");
    // type: "email" — 메일로 보낸 일회용 코드의 검증 타입.
    // 성공하면 onAuthStateChange가 세션을 받아 ready로 넘어간다.
    const { error } = await supabase.auth.verifyOtp({
      email: email.trim(), token: code, type: "email",
    });
    setBusy(false);
    if (error) { setErr(loginError(error.message)); setCode(""); }
  }
}

function Workspace({ who }: { who: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [session, setSession] = useState<string | null>(null);
  // 회사명 — 사용자가 아는 사실이라 첫 턴에 직접 받는다. 자료에서 추론하면
  // '뉴턴/뉴톤'처럼 표기가 흔들리고, 자료가 얇으면 '미상'이 된다.
  const [companyName, setCompanyName] = useState<string | null>(null);
  // 해외 메일에 쓸 로마자 상호. 한글·한자 상호일 때만 묻는다 — 이미 로마자인
  // 회사에게 같은 걸 두 번 묻지 않는다.
  const [companyLatin, setCompanyLatin] = useState<string | null>(null);
  const [awaitingLatin, setAwaitingLatin] = useState(false);
  const [questions, setQuestions] = useState<string[]>([]);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [cands, setCands] = useState<Cand[]>([]);
  // 저장은 **스냅샷**이다 — Set에 id만 담고 cands에서 찾아 쓰면, 다음 웨이브에서
  // 그 후보가 밀려났을 때 "이 회사 좋다"고 저장해둔 것이 정체불명 문자열로
  // 바뀐다(감사 확정 high). 저장 시점의 후보를 통째로 들고 있는다.
  const [saved, setSaved] = useState<Map<string, Cand>>(new Map());
  const [recs, setRecs] = useState<KwRec[]>([]);
  const [replied, setReplied] = useState<Set<string>>(new Set());
  // 후보별 파생물(초안·인사이트·결과) — 서버에 이미 저장돼 있던 것을 복원 때
  // 받아 둔다. 없으면 화면은 매번 처음인 것처럼 보였다.
  // 승인 중복 방지 — state가 아니라 ref다(위 approve의 주석 참조).
  const approvingRef = useRef<string | null>(null);
  const [derived, setDerived] =
    useState<Record<string, { draft?: { drafts?: Draft[] } | null;
                              has_insight?: boolean;
                              outcome?: { saved: boolean; drafted: boolean;
                                          replied: string } }>>({});
  const signOut = () => supabase?.auth.signOut();

  /** 지금 로그인한 계정의 비밀번호를 설정한다.
   *
   * 실패해도 로그인 수단을 잃지 않는다 — 코드 경로는 그대로 살아 있으므로,
   * 오타로 엉뚱한 값이 들어가도 코드로 들어와 다시 설정하면 된다. */
  async function savePassword() {
    if (!supabase || !pwField) return;
    setPwMsg("저장 중…");
    const { error } = await supabase.auth.updateUser({ password: pwField });
    if (error) {
      const m = error.message.toLowerCase();
      setPwMsg(m.includes("at least") || m.includes("should be")
        ? "너무 짧아요 — 더 길게 정해 주세요."
        : error.message);
      return;
    }
    setPwField(null);
    setPwMsg("");
    alert("비밀번호가 설정됐어요. 다음 로그인부터 쓸 수 있어요.");
  }

  // 비밀번호 설정칸 — 열려 있을 때만 렌더한다(평소 사이드바를 어지럽히지
  // 않는다). null = 닫힘, "" = 열렸고 아직 입력 없음.
  const [pwField, setPwField] = useState<string | null>(null);
  const [pwMsg, setPwMsg] = useState("");
  const [reqs, setReqs] = useState<ReqSummary[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  // 파이프라인 보드 — 요청 넘어 저장한 리드를 단계별로. 열 때만 불러온다.
  const [pipe, setPipe] = useState<Pipeline | null>(null);
  const [pipeOpen, setPipeOpen] = useState(false);
  const [track, setTrack] = useState<TrackerData | null>(null);
  const [trackOpen, setTrackOpen] = useState(false);
  const [identOpen, setIdentOpen] = useState(false);
  const [likedC, setLikedC] = useState<Set<string>>(new Set());
  const [dislikedC, setDislikedC] = useState<Set<string>>(new Set());
  const [llm, setLlm] = useState<Llm | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [keySaving, setKeySaving] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // 진행 상태 — 한 줄이 아니라 여러 줄이다. 엔진은 업종별 검색·후보별 판독을
  // 실제로 나눠서 돌리므로(심층 판독은 4개 동시), 한 줄만 보여주면 그 병렬성이
  // 사라지고 "멈춘 것 같다"는 인상만 남는다. 최근 것들을 쌓아 보여주되 마지막
  // 줄만 살아 움직인다.
  const [tick, setTick] = useState<{ msg: string; sec: number } | null>(null);
  const [ticks, setTicks] = useState<{ label: string; done: boolean }[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api("/settings/llm").then(setLlm).catch(() => {}); }, []);
  // 테스트 발송은 자기 자신에게 간다 — 그 주소를 초안 카드가 알아야 한다.
  useEffect(() => {
    try { if (who) localStorage.setItem("a2a:email", who); } catch { /* 사파리 사생활 모드 */ }
  }, [who]);
  // 이번 달 얼마 썼는지 아무도 볼 수 없던 것을 헤더에 상시 노출한다.
  // busy가 끝날 때마다 갱신 — 검색 한 번에 얼마가 빠지는지 보인다.
  useEffect(() => {
    if (busy) return;
    api("/usage").then(setUsage).catch(() => {});
  }, [busy]);

  /** 발송 이후의 소식을 대화로 가져온다.
   *
   *  답장은 우리가 만드는 사건이 아니라 **상대가 만드는 사건**이다. 사용자가
   *  화면을 보고 있는 동안 그것이 도착하면, 원장에만 조용히 쌓이는 것이
   *  아니라 대화에서 말해야 한다 — 이 제품에서 답장은 결과 그 자체다.
   *
   *  since를 ref로 드는 이유: state로 두면 폴링 클로저가 옛 값을 붙잡아
   *  같은 답장을 매번 다시 알린다. 첫 폴링은 서버의 현재 시각만 받아 두고
   *  아무것도 말하지 않는다 — 화면을 열었다는 이유로 지난 답장이 쏟아지면
   *  그건 알림이 아니라 소음이다. */
  const seenAt = useRef<number | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const since = seenAt.current;
        const r = await api(`/outreach/events?since=${since ?? 0}`);
        if (!alive) return;
        if (since === null) { seenAt.current = r.now ?? 0; return; }
        seenAt.current = r.now ?? since;
        for (const e of (r.events ?? []) as OutreachEvent[]) {
          push({ who: "agent",
                 text: `${e.label} — ${e.name}`,
                 jsx: e.event === "EMAIL_REPLY" ? (
                   <div className="card reply-hit">
                     <div className="card-head">회신 도착 · {e.name}</div>
                     <div className="card-body">
                       <p>이 회사가 답장했어요. 답장 사실은 원장에 기록돼,
                         앞으로 비슷한 회사를 찾을 때 <b>연락 가능성 판정을
                         덮어씁니다</b> — 추정이 아니라 실측이니까요.</p>
                       {e.source_url && (
                         <a href={e.source_url} target="_blank" rel="noreferrer">
                           {e.source_url.replace(/^https?:\/\//, "").slice(0, 46)}
                         </a>)}
                     </div>
                   </div>) : undefined });
        }
      } catch { /* 소식 폴링 실패가 작업을 막지 않는다 */ }
    };
    void tick();
    const id = setInterval(tick, 20000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  /** 서버에 다 있는데 돌아갈 화면이 없던 것을 고친다.
   *  이전엔 새로고침 한 번에 승인된 프로필·후보·대화가 전부 사라졌다
   *  (파일 헤더 주석은 복원한다고 써 있었지만 코드가 한 줄도 없었다). */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { requests } = await api("/lead-requests");
        if (!alive) return;
        setReqs(requests ?? []);
        const url = new URL(location.href);
        const want = url.searchParams.get("r");
        if (want && requests?.some((x: ReqSummary) => x.request_id === want))
          await openRequest(want, { silent: true });
      } catch { /* 목록 실패가 새 대화 시작을 막지 않는다 */ }
    })();
    return () => { alive = false; };
  }, []);

  /** 저장된 요청을 화면으로 되살린다. 대화 기록 자체는 서버에 없으므로
   *  (메시지는 클라이언트 상태다) 무엇이 복원됐는지 정직하게 말한다. */
  async function openRequest(rid: string, opts: { silent?: boolean } = {}) {
    try {
      const doc = await api(`/lead-requests/${rid}`);
      setRequestId(rid);
      setVersionId(doc.profile_version_id ?? null);
      setCands(doc.candidates ?? []);
      const fb = doc.feedback ?? {};
      setLikedC(new Set(fb.liked ?? []));
      setDislikedC(new Set(fb.disliked ?? []));
      // 저장·답장·초안 상태를 서버 원장에서 되살린다 — 새로고침이 진행을 지우면
      // 도구가 아니라 일회성 검색이다.
      const dv = (doc.derived ?? {}) as typeof derived;
      setDerived(dv);
      const byId = new Map<string, Cand>((doc.candidates ?? []).map((c: Cand) => [c.company_id, c]));
      setSaved(new Map(Object.entries(dv)
        .filter(([, v]) => v.outcome?.saved && byId.has)
        .flatMap(([cid, v]) => v.outcome?.saved && byId.get(cid) ? [[cid, byId.get(cid)!]] : [])));
      setReplied(new Set(Object.entries(dv)
        .filter(([, v]) => v.outcome?.replied === "yes").map(([cid]) => cid)));
      const u = new URL(location.href);
      u.searchParams.set("r", rid);
      history.replaceState(null, "", u.toString());
      if (!opts.silent) setMsgs([]);
      push({ who: "stamp",
        text: `${doc.title || rid} 을(를) 불러왔습니다 — 후보 `
          + `${(doc.candidates ?? []).length}곳 (${doc.wave ?? 1}차 검색까지)` });
      if ((doc.clarify ?? []).length) askClarify(rid, doc.clarify);
      else if ((doc.candidates ?? []).length)
        push({ who: "agent", kind: "candidates",
          text: "이어서 후보에 반응을 남기거나, 메일 초안을 만들 수 있어요. "
            + "지난 대화 내용은 저장되지 않아 후보 목록부터 다시 보여드려요." });
    } catch (e) {
      push({ who: "agent", text: `불러오지 못했어요 — ${(e as Error).message}` });
    }
  }

  /** 요청 삭제 — 파생물(온톨로지·원장·인사이트·초안)까지 서버가 연쇄로 지운다.
   *  되돌릴 수 없으므로 무엇이 사라지는지 말하고 확인을 받는다. */
  async function removeRequest(r: ReqSummary) {
    const label = r.title || r.request_id;
    if (!confirm(`"${label}" 을(를) 삭제할까요?\n\n`
      + `후보 ${r.candidate_count}곳과 그 판독·메일 초안이 함께 지워지고 `
      + `되돌릴 수 없어요.`)) return;
    try {
      await api(`/lead-requests/${r.request_id}`, undefined, "DELETE");
      setReqs((xs) => xs.filter((x) => x.request_id !== r.request_id));
      if (r.request_id === requestId) newRequest();
      push({ who: "stamp", text: `${label} 을(를) 삭제했습니다` });
    } catch (e) {
      push({ who: "agent", text: `삭제하지 못했어요 — ${(e as Error).message}` });
    }
  }

  /** 새 대화 — location.reload()는 상태를 통째로 버리는 대신 로그인 왕복까지
   *  일으킨다. 필요한 것만 비운다. */
  function newRequest() {
    setRequestId(null); setVersionId(null); setSession(null); setCompanyName(null); setCompanyLatin(null);
    setAwaitingLatin(false);
    setCands([]); setRecs([]); setQuestions([]);
    setSaved(new Map()); setReplied(new Set());
    setLikedC(new Set()); setDislikedC(new Set());
    const u = new URL(location.href);
    u.searchParams.delete("r");
    history.replaceState(null, "", u.toString());
    setMsgs([{ who: "agent",
      text: "새로 시작할게요. 회사 소개를 붙여넣으면 프로필부터 만들어요." }]);
  }

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

  /** 취소 가능한 job 대기. 진행 로그의 마지막 줄과 경과를 헤더에 흘린다 —
   *  사용자가 '무슨 일이 일어나는 중인지' 보이면 기다림이 견딜 만해진다. */
  async function waitJob(jobId: string,
                         onPartial?: (cands: Cand[]) => void) {
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      return await pollJob(jobId, {
        signal: ac.signal,
        onTick: (logs, elapsed) => {
          const last = logs[logs.length - 1];
          setTick({ msg: last?.message ?? last?.stage ?? "처리 중",
                    sec: Math.round(elapsed) });
          // 부분 결과 — 서버가 발굴되는 대로 흘려보낸 후보. 최신 하나만 온다.
          if (onPartial) {
            const pt = [...logs].reverse().find(
              (l) => (l as { data?: { candidates?: Cand[] } }).data?.candidates);
            const cs = (pt as { data?: { candidates?: Cand[] } } | undefined)
              ?.data?.candidates;
            if (cs?.length) onPartial(cs);
          }
          // 같은 문구가 이어지면 한 줄로 접는다 — 로그는 건마다 찍히지만
          // 사용자에게는 "무슨 일이 몇 갈래로 도는가"만 의미가 있다.
          const labels: string[] = [];
          for (const l of logs) {
            const t = humanTick(l.message ?? l.stage ?? "");
            if (t && labels[labels.length - 1] !== t) labels.push(t);
          }
          const tail = labels.slice(-4);
          setTicks(tail.map((label, i) => ({ label, done: i < tail.length - 1 })));
        },
      });
    } finally {
      abortRef.current = null;
      setTick(null);
      setTicks([]);
    }
  }
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  // 인사는 한 번만. StrictMode가 effect를 두 번 실행해 개발 중 인사말이 두
  // 번 떴다(프로덕션 빌드는 한 번). 개발에서만 보이는 차이는 실제 중복과
  // 구분이 안 되어 디버깅을 흐린다 — ref로 못 박는다.
  const greeted = useRef(false);
  useEffect(() => {
    if (greeted.current) return;
    greeted.current = true;
    push({ who: "agent", text: "안녕하세요. 먼저 회사 이름을 알려주세요. 그다음 회사 소개 텍스트를 붙여넣거나 PDF·Word를 올리면 프로필을 만들고, 조건에 맞는 리드를 웹에서 찾아드려요." });
  }, []);

  /** PDF 업로드 → 엔진이 파싱할 Asset으로 변환.
   *  Asset 계약: 업로드 파일은 url에 서버 경로, content는 빈 문자열. */
  /** 본문 없는 실패의 원인을 상태 코드로 갈라 준다. */
  function uploadFailure(status: number, name: string): string {
    if (status === 413)
      return `${name}이(가) 너무 큽니다 — 4.5MB를 넘으면 서버가 받지 못해요.`;
    if (status === 401 || status === 403)
      return "로그인이 만료됐어요. 새로고침 후 다시 시도해 주세요.";
    if (status >= 500)
      return `${name} 업로드 중 서버 오류(${status})예요. 파일 형식 문제는 아닙니다.`;
    return `${name}을(를) 업로드하지 못했어요 (${status}).`;
  }

  /** 파일을 스토리지로 **직접** 올린다.
   *
   * 예전엔 파일이 Next Route Handler를 거쳐 엔진으로 갔는데, Vercel 함수의
   * 요청 본문 상한이 4.5MB라 IR덱은 413으로 튕겼다(요금제로 못 올린다).
   * 지금은 엔진에서 서명만 받고 브라우저가 스토리지로 바로 올린다 — 파일이
   * 함수를 통과하지 않으므로 그 상한이 적용되지 않는다.
   *
   * 경로는 엔진이 정한다(워크스페이스 접두사 + 난수 이름). 클라이언트가
   * 경로를 못 정하므로 남의 워크스페이스에 쓰거나 남의 파일을 덮어쓸 수 없다.
   */
  async function uploadFiles(files: File[]) {
    const assets: Array<Record<string, string>> = [];
    for (const f of files) {
      const sign = await api("/uploads/sign", { filename: f.name }, "POST");
      const { error } = await supabase!.storage
        .from(sign.bucket)
        .uploadToSignedUrl(sign.path, sign.token, f,
                           { contentType: sign.content_type });
      if (error) throw new Error(`${f.name} 업로드 실패 — ${error.message}`);
      assets.push({ type: "ir_deck", content: "",
                    url: `supabase://${sign.path}` });
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
      // 진행 문구는 runOnboard가 민다 — 여기서 또 밀면 "자료를 읽고 있어요"가
      // 두 번 뜬다(실측). 업로드와 판독은 사용자에게 한 동작이다.
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
          { assets: assets ?? [{ type: "text", content: text }],
            company_name: companyName ?? undefined,
            company_name_latin: companyLatin ?? undefined });
        sid = s.session_id; setSession(sid);
      } else if (assets) {
        // 자료가 더 오면 **같은 세션에 붙인다.** 예전엔 새 세션을 만들어서,
        // 앞서 붙여넣은 소개 텍스트가 통째로 버려졌다.
        await api(`/onboarding-sessions/${sid}/assets`, { assets });
      } else {
        await api(`/onboarding-sessions/${sid}/messages`, { answer: text });
      }
      push({ who: "agent", text: "자료를 읽고 있어요…" });
      const { job_id } = await api(`/onboarding-sessions/${sid}/run`, undefined, "POST");
      const res = (await waitJob(job_id)) as {
        needs_answers: boolean;
        session: { current_questions: string[]; profile?: ProfileDoc };
      };
      if (res.needs_answers) {
        setQuestions(res.session.current_questions);
        push({ who: "agent", text: res.session.current_questions[0]
          ?? "회사에 대해 더 알려주세요." });
      } else {
        const name = res.session.profile?.basic?.name ?? "회사";
        push({
          who: "agent",
          text: `${name} 프로필이 준비됐어요. 내용을 확인하고 승인해 주세요.`,
          jsx: <ProfileCard profile={res.session.profile}
            onApprove={() => approve(sid!)}
            onFix={(note) => {
              push({ who: "user", text: note });
              reviseProfile(sid!, note);
            }} />,
        });
      }
    } catch (e) {
      push({ who: "agent", text: (e as Error).message });
    } finally { setBusy(false); }
  }

  /** 프로필 정정.
   *
   * runOnboard로 흘려보내면 안 된다 — 거기엔 "세션이 없으면 이 텍스트를
   * 자료 삼아 새 세션을 만든다"는 분기가 있고, 상태가 어긋나면 정정문이
   * **회사 자료 전체를 대체**한다. 실측(프로덕션 저장 세션): 자료가
   * '뉴톤이야 기업명이' 9글자뿐인 세션이 만들어졌고, 15,559자로 만든
   * 프로필은 버려진 채 회사를 처음부터 다시 파악하려 들었다.
   *
   * 정정은 반드시 **그 프로필을 만든 세션**에 붙는다. sid는 카드가 그려질
   * 때 확정된 값이므로 여기서 인자로 받는다 — session 상태에 의존하지
   * 않는다(둘이 어긋나는 것이 사고의 원인이었다).
   */
  async function reviseProfile(sid: string, note: string) {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/onboarding-sessions/${sid}/corrections`, { note });
      push({ who: "agent", text: "고칠게요…" });
      const { job_id } = await api(
        `/onboarding-sessions/${sid}/run`, undefined, "POST");
      const res = (await waitJob(job_id)) as {
        needs_answers: boolean;
        session: { current_questions: string[]; profile?: ProfileDoc };
      };
      if (res.needs_answers) {
        setQuestions(res.session.current_questions);
        push({ who: "agent", text: res.session.current_questions[0]
          ?? "회사에 대해 더 알려주세요." });
        return;
      }
      const name = res.session.profile?.basic?.name ?? "회사";
      push({
        who: "agent", text: `${name} 프로필을 고쳤어요. 확인해 주세요.`,
        jsx: <ProfileCard profile={res.session.profile}
          onApprove={() => approve(sid)}
          onFix={(n) => { push({ who: "user", text: n });
                          reviseProfile(sid, n); }} />,
      });
    } catch (e) {
      push({ who: "agent", text: `고치지 못했어요 — ${(e as Error).message}` });
    } finally { setBusy(false); }
  }

  async function approve(sid: string) {
    // 형제 함수들과 같은 형태 — 이전엔 try/catch가 없어 승인이 실패하면
    // 화면에 아무 흔적도 남지 않았다(사용자는 버튼이 먹통이라고 느낀다).
    //
    // busy(state)로는 이중 클릭을 못 막는다: 두 번째 클릭 핸들러가 잡고 있는
    // busy는 첫 클릭의 setBusy가 반영되기 전 값이라 둘 다 통과한다. 실측으로
    // 승인이 두 번 나가 Lead Request 폼이 두 벌 떴다. ref는 즉시 반영된다.
    if (busy || approvingRef.current === sid) return;
    approvingRef.current = sid;
    setBusy(true);
    try {
      const { version_id, brief } = await api(
        `/onboarding-sessions/${sid}/approve`, undefined, "POST");
      setVersionId(version_id);
      push({ who: "stamp", text: "프로필을 승인했습니다" });
      push({
        who: "agent", text: "어떤 리드를 찾을까요?",
        jsx: <BriefForm draft={brief}
          onSubmit={(intent) => createRequest(version_id, intent)} />,
      });
    } catch (e) {
      push({ who: "agent", text: `승인하지 못했어요 — ${(e as Error).message}` });
      approvingRef.current = null;   // 실패했으면 다시 눌러볼 수 있어야 한다
    } finally { setBusy(false); }
  }

  async function createRequest(vid: string, intent: Record<string, unknown>) {
    setBusy(true);
    try {
      const doc = await api("/lead-requests", {
        title: String(intent.target_region || "") + " " + String(intent.target_type || "리드"),
        profile_version_id: vid, intent,
      });
      setRequestId(doc.request_id);
      // 사이드바를 즉시 갱신 — 새로고침해도 돌아올 자리가 생긴다
      api("/lead-requests").then((l) => setReqs(l.requests ?? [])).catch(() => {});
      const u = new URL(location.href);
      u.searchParams.set("r", doc.request_id);
      history.replaceState(null, "", u.toString());
      push({ who: "stamp", text: "검색 조건을 확정했습니다" });
      push({ who: "agent", text: "검색 기준을 만들고 있어요…" });
      const b = await api(`/lead-requests/${doc.request_id}/search-brief`, undefined, "POST");
      const brief = (await waitJob(b.job_id)) as {
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
      const res = (await waitJob(r.job_id)) as
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

  /** 성공하면 true — 실패 시 카드가 다시 눌릴 수 있게 호출자에게 알린다. */
  /** 상위 후보의 사이트를 실제로 읽어 접점·신호를 채운다.
   *
   * 검색 job과 분리한 이유: 웨이브1이 300초 근처라 같이 넣으면 Vercel 상한을
   * 넘긴다. 목록은 즉시 보여주고, 판독이 끝나면 카드만 조용히 갱신한다.
   * 실측: 스니펫만 읽던 때는 1위 후보 접점 0건 — 사이트를 읽자 UNDO는
   * 접점 1·신호 3(Microsoft·BA·McLaren 파트너십), Project Cece는
   * 파트너 모집 페이지가 나왔다. */
  async function deepRead(rid: string) {
    try {
      const j = await api(`/lead-requests/${rid}/deep-read`, {}, "POST");
      // waitJob을 쓰지 않는다 — 그건 abortRef와 진행 표시를 독점하는데, 판독은
      // 사용자가 다음 행동(반응·재검색)을 하는 동안 뒤에서 도는 작업이다.
      const res = (await pollJob(j.job_id, {})) as
        { candidates: Cand[]; read: number; total: number };
      // 교체가 아니라 병합 — 판독하는 동안 사용자가 재검색했으면 목록이 이미
      // 바뀌어 있다. 옛 목록으로 되돌리면 안 되고, 겹치는 후보만 살찌운다.
      const by = new Map(res.candidates.map((c) => [c.company_id, c]));
      setCands((prev) => prev.map((c) => {
        const u = by.get(c.company_id);
        return u ? { ...c, ontology: u.ontology, deep_read: u.deep_read } : c;
      }));
      const contacts = res.candidates.reduce(
        (n, c) => n + (c.ontology?.contacts?.length ?? 0), 0);
      push({ who: "agent",
        text: `상위 ${res.total}곳 중 ${res.read}곳의 사이트를 읽어 접점 ${contacts}건을 찾았어요. 카드를 펼치면 보여요.` });
    } catch (e) {
      // 판독 실패가 검색 결과를 가리면 안 된다 — 목록은 이미 떠 있다.
      push({ who: "agent", text: `사이트 판독을 마치지 못했어요 — ${(e as Error).message}` });
    }
  }

  async function runSearch(rid: string, segments: string[],
                           extra: string[]): Promise<boolean> {
    setBusy(true);
    push({ who: "user", text: segments.length
      ? segments.join(" · ") : "기준 그대로 검색" });
    push({ who: "agent", text: segments.length > 1
      ? `${segments.length}개 업종을 각각 검색하고 있어요…`
      : "웹에서 후보를 모으고 있어요…" });
    try {
      const s2 = await api(`/lead-requests/${rid}/search`,
        { segments, extra_queries: extra });
      // 캐러셀 자리를 지금 연다 — 후보는 발굴되는 대로 이 자리에 뜬다.
      // 웨이브1이 2~5분인데 끝에 한 번에 보여주면 그 시간이 통째로 침묵이다.
      push({ who: "agent", kind: "candidates",
        text: "찾는 대로 여기에 띄울게요 — 판독과 순위는 이어서 채워집니다." });
      const res = (await waitJob(s2.job_id, setCands)) as
        { candidates: Cand[]; keyword_recommendations: KwRec[];
          clarify: ClarifyQ[] };
      setCands(res.candidates);
      setRecs(res.keyword_recommendations || []);
      if (res.candidates.length) void deepRead(rid);
      const bySeg = new Map<string, number>();
      for (const c of res.candidates)
        bySeg.set(c.segment || "", (bySeg.get(c.segment || "") ?? 0) + 1);
      const brk = [...bySeg.entries()].filter(([k]) => k)
        .map(([k, n]) => `${k} ${n}곳`).join(" · ");
      if (res.candidates.length === 0) {
        // 0건은 실패가 아니라 결과다 — 다음에 뭘 하면 되는지 말해준다.
        push({ who: "agent",
          text: "이 조건으로는 후보를 못 찾았어요. 지역을 넓히거나, 업종을 "
            + "다시 고르거나, 검색어를 직접 추가해 보세요." });
      } else {
        push({ who: "agent", kind: "candidates",
          text: `일단 ${res.candidates.length}곳 찾았어요.`
          + (brk ? ` (${brk})` : "")
          + " 좌우로 넘겨 보시고, '이런 곳 더/아니에요'로 알려주시면 더 정확해져요." });
      }
      askClarify(rid, res.clarify);
      return true;
    } catch (e) {
      const code = (e as { payload?: { code?: string } }).payload?.code;
      push({ who: "agent", text: code === "cost_cap"
        ? (e as Error).message
        : `후보를 찾지 못했어요 — ${(e as Error).message}` });
      return false;
    } finally { setBusy(false); }
  }

  /** 관측된 갈림에서 나온 질문을 던진다 — 라벨링 대신 대화로 좁힌다.
   *  질문이 없어도 반응·확정 카드는 띄운다 (멀티턴의 손잡이). */
  function askClarify(rid: string, qs: ClarifyQ[]) {
    push({
      who: "agent",
      // 문구는 후보를 **본 뒤**의 말이어야 한다. 캐러셀이 위에 있으므로
      // "이런 방향으로 찾을까요?"처럼 결과를 안 본 것처럼 묻지 않는다.
      text: qs.length
        ? "위 후보를 보고 몇 가지만 알려주세요 — 다음 검색이 좁혀져요."
        : "위 후보 중 마음에 드는 곳에 👍, 아닌 곳에 👋를 눌러주세요. 충분하면 확정할게요.",
      jsx: <ClarifyCard qs={qs}
        onRefine={(answers) => refine(rid, answers, false)}
        onDone={() => refine(rid, [], true)} />,
    });
  }

  async function refine(rid: string, answers: string[],
                        done: boolean): Promise<boolean> {
    setBusy(true);
    push({ who: "user", text: done ? "이 정도면 확정"
      : (answers.join(" · ") || "반응 반영해서 다시") });
    if (!done) push({ who: "agent", text: "답을 반영해 더 찾고 있어요…" });
    try {
      const r = await api(`/lead-requests/${rid}/refine`, {
        answers, liked: [...likedC], disliked: [...dislikedC], done });
      const res = (await waitJob(r.job_id, done ? undefined : setCands)) as {
        candidates: Cand[]; clarify: ClarifyQ[]; final: boolean;
        wave: number; new_found?: number; note?: string };
      setCands(res.candidates);
      if (res.final) {
        push({ who: "stamp", text: "후보를 확정했습니다" });
        void deepRead(rid);
        push({ who: "agent", kind: "candidates",
          text: `최종 ${res.candidates.length}곳이에요. 저장한 후보만 메일 초안으로 이어져요.` });
      } else {
        push({ who: "agent", kind: "candidates",
          text: res.note ?? `${res.new_found ?? 0}곳을 새로 찾아 다시 정렬했어요 (${res.wave}차).` });
        askClarify(rid, res.clarify);
      }
      return true;
    } catch (e) {
      push({ who: "agent", text: (e as Error).message });
      return false;
    } finally { setBusy(false); }
  }

  async function draftMail(cid: string) {
    if (!requestId) return;
    setBusy(true);
    push({ who: "agent", text: "수요 신호를 정리하고 초안을 쓰고 있어요…" });
    try {
      const i = await api(`/lead-requests/${requestId}/candidates/${cid}/insight`, undefined, "POST");
      await waitJob(i.job_id);
      const c = await api(`/lead-requests/${requestId}/candidates/${cid}/compose`, undefined, "POST");
      const res = (await waitJob(c.job_id)) as
        { drafts: Draft[]; outreach?: OutreachKit; language?: string };
      const d = res.drafts[0];
      setDerived((prev) => ({ ...prev, [cid]: { ...(prev[cid] ?? {}),
        draft: res, has_insight: true,
        outcome: { ...(prev[cid]?.outcome ?? { saved: false, replied: "" }),
                   drafted: true } } }));
      push({
        who: "agent", text: "초안이에요. 발송은 직접 하셔야 해요.",
        jsx: <MailDraft d={d} kit={res.outreach} lang={res.language}
          recipient={(res as { recipient?: Recipient }).recipient}
          rid={requestId} cid={cid} api={api}
          onSent={(m) => push({ who: "agent", text: m })}
          onNeedIdentity={() => { setIdentOpen(true); setPipeOpen(false); setTrackOpen(false); }} />,
      });
    } catch (e) { push({ who: "agent", text: (e as Error).message }); }
    finally { setBusy(false); }
  }

  /** 저장된 초안을 다시 연다 — 다시 만들면 비용이 들고 문장이 바뀐다. */
  function reopenDraft(cid: string, name: string) {
    const stored = derived[cid]?.draft as
      { drafts?: Draft[]; outreach?: OutreachKit; language?: string } | null | undefined;
    const d = stored?.drafts?.[0];
    if (!d) return;
    push({ who: "agent", text: `${name}에게 보낼 저장된 초안이에요.`,
           jsx: <MailDraft d={d} kit={stored?.outreach} lang={stored?.language}
             recipient={(stored as { recipient?: Recipient } | null | undefined)?.recipient}
             rid={requestId ?? undefined} cid={cid} api={api}
             onSent={(m) => push({ who: "agent", text: m })}
             onNeedIdentity={() => { setIdentOpen(true); setPipeOpen(false); setTrackOpen(false); }} /> });
  }

  function send() {
    const v = input.trim();
    if (!v || busy) return;
    setInput("");
    push({ who: "user", text: v });
    // 세션 전이고 이름이 없는데 짧은 한 줄이면 이름으로 받는다. 긴 글은
    // 자료다 — 이름을 안 주고 소개를 붙여넣는 사용자를 막지 않는다(그때는
    // 모델이 추론하고, 틀리면 정정 경로로 고친다).
    if (!session && !companyName && looksLikeName(v)) {
      setCompanyName(v);
      if (needsLatinName(v)) {
        // 해외 후보에게 보낼 메일에 한글 상호를 그대로 넣으면 상대가 못 읽는다
        // (실측: 일본어 본문에 "弊社の귤메달"). 음역은 회사가 실제 쓰는 영문
        // 상호와 다른 경우가 많아 지어내지 않고 묻는다.
        setAwaitingLatin(true);
        push({ who: "agent",
          text: `${v}, 반가워요. 해외에 보낼 메일에 쓸 영문 상호가 있을까요? `
            + `(없으면 "없어요"라고 답해 주세요)` });
        return;
      }
      push({ who: "agent",
        text: `${v}, 반가워요. 이제 회사 소개 텍스트를 붙여넣거나 PDF·Word를 올려주세요.` });
      return;
    }
    if (awaitingLatin) {
      setAwaitingLatin(false);
      const skip = /^(없|없어요|없음|아니|패스|skip|no)/i.test(v);
      if (!skip && looksLikeName(v)) setCompanyLatin(v);
      push({ who: "agent",
        text: (skip || !looksLikeName(v))
          ? "네, 해외 메일에는 상호를 그대로 쓸게요. 이제 회사 소개 텍스트를 붙여넣거나 PDF·Word를 올려주세요."
          : `${v}로 쓸게요. 이제 회사 소개 텍스트를 붙여넣거나 PDF·Word를 올려주세요.` });
      return;
    }
    if (!versionId) { runOnboard(v); return; }
    push({ who: "agent", text: "지금은 위 카드의 버튼으로 진행해 주세요 — 자유 대화 확장은 다음 이슈예요." });
  }

  return (
    <div className="app">
      <nav className="rail" aria-label="워크스페이스">
        <div className="ws" title="rename">r.</div>
        <div className="spacer" />
        <div className="me" title={who}>{(who || "?").trim()[0]?.toUpperCase()}</div>
      </nav>

      <aside className="side">
        <div className="side-head">
          <div className="brand">rename<em>.</em>
            <small>Lead 발굴 워크스페이스</small></div>
          <button className="btn-new" title="새 Lead Request"
            onClick={newRequest}>+</button>
        </div>
        <div className="side-scroll">
          <div className="sec">
            <div className="sec-title"><span className="tri">▾</span> Request</div>
            {reqs.length === 0 && !requestId && (
              <div className="empty">아직 없어요</div>)}
            {reqs.map((r) => (
              <div className={`chan-row ${r.request_id === requestId ? "on" : ""}`}
                key={r.request_id}>
                <button disabled={busy}
                  className={`chan ${r.request_id === requestId ? "active" : ""}`}
                  title={r.request_id}
                  onClick={() => openRequest(r.request_id)}>
                  <span className="hash">#</span>
                  <span className="nm">{r.title || r.request_id}</span>
                  {r.candidate_count > 0 &&
                    <span className="badge">{r.candidate_count}</span>}
                </button>
                <button className="chan-del" disabled={busy} title="이 요청 삭제"
                  onClick={() => removeRequest(r)}>×</button>
              </div>
            ))}
          </div>
          <div className="sec">
            <div className="sec-title"><span className="tri">▾</span> 파이프라인</div>
            <button className={`chan ${pipeOpen ? "active" : ""}`}
              onClick={async () => {
                if (pipeOpen) { setPipeOpen(false); return; }
                try { setPipe(await api("/pipeline")); setPipeOpen(true); }
                catch (e) { push({ who: "agent", text: (e as Error).message }); }
              }}>
              <span className="hash">☆</span>
              <span className="nm">저장한 리드 보드{pipe ? ` · ${pipe.total}` : ""}</span></button>
            <button className={`chan ${trackOpen ? "active" : ""}`}
              onClick={async () => {
                if (trackOpen) { setTrackOpen(false); return; }
                try { setTrack(await api("/outreach/tracker")); setTrackOpen(true); setPipeOpen(false); }
                catch (e) { push({ who: "agent", text: (e as Error).message }); }
              }}>
              <span className="hash">✉</span>
              <span className="nm">보낸 메일{track ? ` · ${track.total}` : ""}</span></button>
            <button className={`chan ${identOpen ? "active" : ""}`}
              onClick={() => { setIdentOpen((v) => !v); setPipeOpen(false); setTrackOpen(false); }}>
              <span className="hash">⚖</span>
              <span className="nm">발신자 정보</span></button>
          </div>
        </div>
        <div className="side-foot">
          <span className="dot-live" />
          rename 에이전트 온라인 · {llm?.label ?? "…"}
          {usage && (
            <span className="usage" title={
              `이번 달 ${usage.month} · 워크스페이스 $${usage.workspace_usd} / `
              + `$${usage.workspace_cap_usd} · 전체 $${usage.global_usd} / `
              + `$${usage.global_cap_usd} (선예약 추정치이며 실 청구액이 아닙니다)`}>
              ${usage.workspace_usd.toFixed(2)}~
            </span>
          )}
          {isConfigured && (
            <>
              <button className="side-out" onClick={() => {
                setPwMsg(""); setPwField((v) => (v === null ? "" : null));
              }}>비밀번호</button>
              <button className="side-out side-out-tight" onClick={signOut}
                title={who}>로그아웃</button>
            </>
          )}
        </div>
        {/* 비밀번호 설정 — updateUser는 **현재 세션**으로만 동작한다.
            즉 이미 로그인한 사람이 자기 것을 바꾸는 경로이고, 로그인 화면에
            둘 수 없는 이유(누구나 누를 수 있음)가 여기서는 성립하지 않는다. */}
        {pwField !== null && (
          <div className="side-pw">
            <input className="side-pw-input" type="password" value={pwField}
              placeholder="새 비밀번호" autoFocus
              autoComplete="new-password"
              onChange={(e) => setPwField(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && savePassword()} />
            <button className="side-pw-save" onClick={savePassword}
              disabled={!pwField}>저장</button>
            {pwMsg && <div className="side-pw-msg">{pwMsg}</div>}
          </div>
        )}
      </aside>

      <main className="main">
        <header className="chat-head">
          <h1><span className="hash">#</span> lead-discovery</h1>
          {busy && (
            <span className="pill run">
              {tick ? `${humanTick(tick.msg)} · ${tick.sec}s` : "작업 중"}
            </span>
          )}
          {busy && abortRef.current && (
            <button className="pill cancel"
              onClick={() => abortRef.current?.abort()}>취소</button>
          )}
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
        {identOpen && <SenderIdentity api={api}
          onDone={() => push({ who: "agent", text: "발신자 정보를 저장했어요. 이제 발송할 수 있습니다." })} />}
        {trackOpen && track && <MailTracker t={track} />}
        {pipeOpen && pipe && (
          <PipelineBoard pipe={pipe}
            onOpen={(rid) => { setPipeOpen(false); openRequest(rid); }}
            onStage={async (row, stage) => {
              try {
                await api(`/lead-requests/${row.request_id}/candidates/${row.company_id}/outcome`,
                          { stage });
                setPipe(await api("/pipeline"));
              } catch (e) { push({ who: "agent", text: (e as Error).message }); }
            }} />
        )}
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
                {/* 무엇을 하고 있는지 한 줄 — 애니메이션만 돌면 멈춘 건지
                    도는 건지 알 수 없다. 문구는 사람의 말로 옮겨 보여준다. */}
                {busy && i === msgs.length - 1 && m.who === "agent"
                  && (ticks.length > 0 || tick) && (
                  <div className="ticks">
                    {(ticks.length ? ticks
                      : [{ label: humanTick(tick!.msg), done: false }]
                     ).map((t, k, arr) => (
                      <div className={`tick ${t.done ? "done" : "live"}`} key={k}>
                        <span className="tick-dot" />
                        {t.label}
                        {k === arr.length - 1 && tick ? ` · ${tick.sec}초` : ""}
                      </div>))}
                  </div>
                )}
                {m.jsx && <div className="attach">{m.jsx}</div>}
                {/* 자리가 여러 번 열려도(스트리밍 시작·완료·복원) 목록은
                    마지막 자리 한 곳에만 — 두 벌 뜨면 어느 쪽이 최신인지 모른다. */}
                {m.kind === "candidates" && cands.length > 0
                  && i === msgs.reduce((a, x, j) =>
                       x.kind === "candidates" ? j : a, -1) && (
                <div className="carousel">
                  {cands.map((c, i) => (
                    <div className="cand-card" key={c.company_id}>
  <div className="bubble">
                    <b>{c.partial ? "발굴" : `${i + 1}위`} · {
                      c.name_ko && c.name_ko !== c.name ? c.name_ko : c.name}</b>
                    {c.name_ko && c.name_ko !== c.name && (
                      <span className="orig"> {c.name}</span>)}
                    {/* 적합도 — 원점수는 곱셈으로 눌려 실측이 0.005~0.303에
                        뭉쳤다(중앙 0.099). 로지스틱으로 편 값을 보여준다;
                        순위는 원점수 그대로라 배지와 순서가 어긋나지 않는다. */}
                    {typeof c.match === "number" && !c.partial && (
                      <span className={`chip fitc ${
                        c.match >= 0.7 ? "hi" : c.match >= 0.4 ? "mid" : "lo"}`}
                        title="이번 제안에 대한 적합도 — 보완성·출처 신뢰도·연락 가능성을 합친 값이에요">
                        적합도 {Math.round(c.match * 100)}</span>)}
                    {c.segment && <span className="chip seg">{c.segment}</span>}
                    {c.weak && <span className="chip ask">임계 미만</span>}
                    {c.reach_fact && (
                      <span className="chip ok"
                        title="이 도메인에서 답장을 받은 적이 있어요 — 가능성 추정을 실측이 덮었습니다">
                        답장 이력</span>)}
                    {/* 임계 0.25 — 실측 분포에서 대기업이 0.08~0.2, 중견이
                        0.28~0.45로 갈렸다. 0.4로 하면 1위에도 배지가 붙는다. */}
                    {typeof c.ontology?.reachability === "number"
                      && c.ontology.reachability < 0.25 && (
                      <span className="chip ask"
                        title={c.ontology.reachability_why
                          || "첫 콜드 아웃리치가 실무자에게 닿기 어려운 구조"}>
                        가능성 낮음</span>)}
                    {"\n"}{c.what || c.pain_signal.slice(0, 140)}
                    {c.signal ? `\n\n관측된 신호 — ${c.signal}` : ""}
                  </div>
                  {c.partial && (
                    <div className="quiet">판독 중 — 접점·신호·순위는 곧 채워져요</div>)}
                  {/* 왜 지금 — 이 카드에서 가장 먼저 읽혀야 하는 한 문장.
                      근거 페이지로 바로 갈 수 있어야 사용자가 확인한다. */}
                  {c.ontology?.why_now && (
                    <div className="whynow">
                      <span className="whynow-lb">왜 지금</span>
                      {c.ontology.why_now}
                      {c.ontology.why_now_source && (
                        <a href={c.ontology.why_now_source} target="_blank"
                          rel="noreferrer" className="whynow-src">근거</a>)}
                    </div>)}
                  {c.deep_read?.status === "done" && !c.ontology?.why_now && (
                    <div className="quiet">지금이어야 할 이유는 못 찾았어요 — 상시 제안으로 접근</div>)}
                  {c.deep_read?.status === "done"
                    && !(c.ontology?.contacts?.length) && (
                    <div className="quiet">공개 접점 미확인 — 사이트에서 문의 창구를 찾지 못함</div>)}
                  {(c.ontology?.signals ?? []).length > 0 && (
                    <div className="sig-badges">
                      {c.ontology!.signals!.slice(0, 3).map((sg, i) => (
                        <span className={`sig-cat ${sg.category}`} key={i}
                          title={sg.evidence}>
                          {SIGNAL_KO[sg.category] ?? sg.category}</span>
                      ))}
                    </div>
                  )}
                  {c.ontology && <OntologyView ont={c.ontology}
                    sourceUrl={c.source_url} />}
                  {/* 색인에서 찾은 접점 — 판독(사이트에서 읽음)과 출처가
                      다르므로 섞지 않고 따로 표시한다. 서버가 도메인 일치를
                      이미 집행했으므로 여기선 경고가 필요 없다. */}
                  {(c.hunter?.contacts?.length ?? 0) > 0 && (
                    <div className="hunt">
                      <div className="hunt-h">색인에서 찾은 메일
                        <span className="hunt-note">공개 웹에서 수집된 주소예요 — 발송 전 확인하세요</span>
                      </div>
                      {c.hunter!.contacts.slice(0, 3).map((h) => (
                        <div className="hunt-row" key={h.email}>
                          <span className="hunt-mail">{h.email}</span>
                          {h.position && <span className="hunt-pos">{h.position}</span>}
                          <span className={`hunt-conf ${h.confidence >= 90 ? "hi" : h.confidence >= 70 ? "mid" : "lo"}`}
                            title="색인 신뢰도 — 이 주소가 실제로 쓰이는 것을 웹에서 몇 번 봤는가">
                            {h.confidence}</span>
                          {h.sources[0] && (
                            <a className="hunt-src" href={h.sources[0]} target="_blank"
                              rel="noreferrer" title="이 주소가 발견된 페이지">근거</a>)}
                        </div>))}
                    </div>)}
                  <div className="reacts">
                    <button className={`react ${likedC.has(c.company_id) ? "on" : ""}`}
                      onClick={() => setLikedC((v) => {
                        const n = new Set(v);
                        n.has(c.company_id) ? n.delete(c.company_id)
                          : (n.add(c.company_id), dislikedC.delete(c.company_id));
                        return n;
                      })}>👍 이런 곳 더</button>
                    <button className={`react ${dislikedC.has(c.company_id) ? "on" : ""}`}
                      onClick={() => setDislikedC((v) => {
                        const n = new Set(v);
                        n.has(c.company_id) ? n.delete(c.company_id)
                          : (n.add(c.company_id), likedC.delete(c.company_id));
                        return n;
                      })}>👋 아니에요</button>
                  </div>
                  <div className="cand-acts">
                    <a className="mini" href={c.source_url} target="_blank"
                      rel="noreferrer">원문</a>
                    {c.deep_read && (
                      <span className="mini" title={c.deep_read.note ?? ""}>
                        {c.deep_read.status === "done"
                          ? `사이트 읽음 · 접점 ${c.deep_read.contacts ?? 0}`
                          : c.deep_read.status === "no_site" ? "사이트 미확인"
                          : "사이트 못 읽음"}
                      </span>)}
                    {/* 근거 — "사이트를 읽었다"는 말만으로는 검증이 안 된다.
                        어느 성격의 페이지를 열었는지, 거기서 무엇이 나왔는지를
                        링크와 함께 보여 사용자가 직접 대조하게 한다. */}
                    {c.deep_read?.status === "done"
                      && (c.deep_read.pages?.length ?? 0) > 0 && (
                      <ReadEvidence dr={c.deep_read!} ont={c.ontology} />)}
                    {c.source_kind && SRC_LABEL[c.source_kind] && (
                      <span className="mini" title={`실존·부합 추정 p=${c.p ?? "?"}`}
                        style={{ opacity: c.source_kind === "mention" ? 0.6 : 0.85 }}>
                        {SRC_LABEL[c.source_kind]}{typeof c.p === "number" ? ` · p ${c.p.toFixed(2)}` : ""}
                      </span>)}
                    <button
                      className={`mini ${saved.has(c.company_id) ? "saved" : ""}`}
                      onClick={async () => {
                        const on = !saved.has(c.company_id);
                        setSaved((sv) => {
                          const n = new Map(sv);
                          on ? n.set(c.company_id, c) : n.delete(c.company_id);
                          return n;
                        });
                        if (!requestId) return;
                        try {
                          await api(
                            `/lead-requests/${requestId}/candidates/${c.company_id}/outcome`,
                            { saved: on });
                        } catch (e) {
                          // 기록에 실패하면 토글을 되돌린다 — 저장됐다고 표시해
                          // 놓고 서버엔 없으면 다음 추천 가중이 어긋난다
                          setSaved((sv) => {
                            const n = new Map(sv);
                            on ? n.delete(c.company_id) : n.set(c.company_id, c);
                            return n;
                          });
                          push({ who: "agent",
                            text: `저장을 기록하지 못했어요 — ${(e as Error).message}` });
                        }
                      }}>
                      {saved.has(c.company_id) ? "저장됨" : "저장"}
                    </button>
                    {derived[c.company_id]?.draft?.drafts?.length ? (
                      <>
                        <button className="mini saved" title="저장된 초안 다시 열기"
                          onClick={() => reopenDraft(c.company_id, c.name)}>초안 보기</button>
                        <button className="mini" title="새로 씁니다 (비용 발생)"
                          onClick={() => draftMail(c.company_id)}>다시 쓰기</button>
                      </>
                    ) : (
                      <button className="mini"
                        onClick={() => draftMail(c.company_id)}>메일 초안</button>
                    )}
                    <button
                      className={`mini ${replied.has(c.company_id) ? "saved" : ""}`}
                      onClick={async () => {
                        const on = !replied.has(c.company_id);
                        setReplied((rv) => {
                          const n = new Set(rv);
                          on ? n.add(c.company_id) : n.delete(c.company_id);
                          return n;
                        });
                        if (!requestId) return;
                        try {
                          await api(
                            `/lead-requests/${requestId}/candidates/${c.company_id}/outcome`,
                            { replied: on ? "yes" : "" });
                          // 성공한 뒤에 알린다 — 실패했는데 "기록했습니다"는 거짓말
                          if (on) push({ who: "stamp",
                            text: "답장을 기록했습니다 — 다음 검색의 키워드 추천에 반영됩니다" });
                        } catch (e) {
                          setReplied((rv) => {
                            const n = new Set(rv);
                            on ? n.delete(c.company_id) : n.add(c.company_id);
                            return n;
                          });
                          push({ who: "agent",
                            text: `답장을 기록하지 못했어요 — ${(e as Error).message}` });
                        }
                      }}>
                      {replied.has(c.company_id) ? "답장 받음 ✓" : "답장 받음"}
                    </button>
                  </div>
                    </div>
                  ))}
                </div>
                )}
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
                <input ref={fileRef} type="file" accept=".pdf,.docx" multiple hidden
                  onChange={onPickFiles} />
                <button className="icon-btn" title="자료 올리기 (PDF·Word)"
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

    </div>
  );
}

/** 승인 전에 프로필 본문을 보여준다.
 *
 *  이전엔 회사 이름만 뜨고 "승인" 버튼이 있었다 — 대표가 자기 회사에 대해
 *  엔진이 무엇을 이해했는지 못 본 채 승인했고, 그 프로필이 이후 모든 검색·
 *  판단·메일의 전제가 됐다. 틀렸을 때 고칠 길도 없었다.
 */
function ProfileCard({ profile, onApprove, onFix }: {
  profile?: ProfileDoc;
  onApprove: () => void;
  onFix: (note: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [fixing, setFixing] = useState(false);
  const [note, setNote] = useState("");
  const rows: [string, string][] = [
    ["회사", [profile?.basic?.name, profile?.basic?.country,
              profile?.basic?.industry].filter(Boolean).join(" · ")],
    ["소개", profile?.description ?? ""],
    ["푸는 문제", profile?.problem_solved?.value ?? ""],
    ["솔루션", profile?.solution?.value ?? ""],
    ["타깃 고객", profile?.target_customer?.value ?? ""],
  ];
  return (
    <div className="card" style={{ maxWidth: 620 }}>
      <div className="card-head">엔진이 이해한 우리 회사
        <button className="meta prof-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? "접기" : "펼치기"}</button>
      </div>
      {open && (
        <div className="card-body">
          {rows.filter(([, v]) => v).map(([k, v]) => (
            <div className="prof-row" key={k}>
              <span className="prof-k">{k}</span>
              <span className="prof-v">{v}</span>
            </div>
          ))}
        </div>
      )}
      {fixing && (
        <div className="card-body">
          <input className="clar-free" value={note} autoFocus
            placeholder="어디가 다른가요? (예: 타깃은 유통사가 아니라 호텔이에요)"
            onChange={(e) => setNote(e.target.value)} />
        </div>
      )}
      <div className="card-foot">
        {fixing ? (
          <button className="btn pri" disabled={!note.trim()}
            onClick={() => { onFix(note.trim()); setFixing(false); setNote(""); }}>
            이 내용으로 다시 만들기
          </button>
        ) : (
          <>
            <button className="btn pri" onClick={onApprove}>이대로 승인</button>
            <button className="btn" onClick={() => setFixing(true)}>
              이 부분이 달라요
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/** 파이프라인 보드 — 요청 넘어 저장한 리드를 단계별 열로. 단계 이동은 사용자의
 *  손이다(답장 여부 같은 사실은 자동으로 올라오고, 미팅·성사는 사용자만 안다). */
/** 발신자 정보 — 콜드메일에 법이 요구하는 것.
 *
 *  하드코딩하지 않는 이유는 분명하다: 이건 우리가 아는 사실이 아니라
 *  **보내는 사람이 아는 사실**이다. 법인명과 우편 주소를 대신 지어 넣으면
 *  고지가 아니라 거짓말이 된다. 그래서 빈칸으로 두고 사람이 채운다. */
function SenderIdentity({ api, onDone }: {
  api: (p: string, b?: unknown, m?: string) => Promise<any>;
  onDone?: () => void }) {
  const [v, setV] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string[]>([]);
  const [busy, setBusy] = useState(true);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api("/outreach/identity")
      .then((r) => { setV(r.identity ?? {}); setMissing(r.missing ?? []); })
      .catch((e) => setMsg((e as Error).message))
      .finally(() => setBusy(false));
  }, []);
  const F: [string, string, string][] = [
    ["legal_name", "법인명", "메일에 표시될 정식 상호 — 수신자가 읽을 수 있는 표기로"],
    ["postal_address", "우편 주소", "미국 CAN-SPAM이 실제 주소를 요구해요"],
    ["contact_email", "연락 이메일", "수신 거부·문의가 도착할 주소"],
    ["phone", "전화 (선택)", ""],
    ["website", "웹사이트 (선택)", ""],
    ["unsubscribe_url", "수신 거부 링크 (선택)", "비우면 '회신으로 거부' 안내만 들어갑니다"],
  ];
  async function save() {
    setBusy(true); setMsg("");
    try {
      const r = await api("/outreach/identity", v, "PUT");
      setMissing(r.missing ?? []);
      if ((r.missing ?? []).length === 0) { setMsg("저장했어요 — 발송이 열렸습니다."); onDone?.(); }
      else setMsg("아직 빈 항목이 있어요.");
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <div className="pipe">
      <div className="pipe-head">발신자 정보 — 콜드메일의 법적 요건</div>
      <div className="ident">
        <p className="quiet">
          보내는 메일 끝에 발신자와 수신 거부 방법을 밝혀야 합니다(미국
          CAN-SPAM, 유럽·영국 GDPR, 일본 특정전자메일법 등). 아래를 채우면
          코드가 수신 국가의 언어로 고지를 붙입니다. <b>채우기 전에는
          발송이 막힙니다</b> — 반쪽 고지는 고지가 아니니까요.
        </p>
        {F.map(([k, label, hint]) => (
          <label className="ident-row" key={k}>
            <span className="ident-k">
              {label}
              {missing.includes(k) && <i className="need">필요</i>}
            </span>
            <input className="ident-in" value={v[k] ?? ""} disabled={busy}
              onChange={(e) => setV({ ...v, [k]: e.target.value })} />
            {hint && <span className="ident-hint">{hint}</span>}
          </label>))}
        <div className="sc-act">
          <button className="btn coral" onClick={save} disabled={busy}>
            {busy ? "…" : "저장"}</button>
          {msg && <span className="quiet">{msg}</span>}
        </div>
      </div>
    </div>
  );
}

/** 보낸 메일 대시보드 — 세션(요청)을 넘어 "내가 뿌린 메일 전체"를 하나로.
 *
 *  세션 하나하나를 열어야 성과를 알 수 있으면 그로스 도구가 아니라
 *  로그 뷰어다. 이 화면은 워크스페이스 전체를 한 사용자의 캠페인으로
 *  본다 — Salesforce의 리드 리스트가 캠페인을 넘나들듯, 여기서도
 *  "어느 요청에서 나왔는지"는 필터일 뿐 경계가 아니다.
 */
function MailTracker({ t }: { t: TrackerData }) {
  const [filter, setFilter] = useState<"all" | "opened" | "replied" | "bounced" | "pending">("all");
  const [q, setQ] = useState("");
  const fmt = (v: number | null) =>
    v ? new Date(v * 1000).toLocaleString("ko-KR",
      { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "—";
  const gap = (a: number | null, b: number | null) => {
    if (!a || !b) return "";
    const m = Math.round((b - a) / 60);
    if (m < 60) return `${m}분 만에`;
    const h = Math.round(m / 60);
    return h < 48 ? `${h}시간 만에` : `${Math.round(h / 24)}일 만에`;
  };
  const requests = [...new Set(t.leads.map((l) => l.request_title).filter(Boolean))] as string[];
  const [reqFilter, setReqFilter] = useState("");

  const rows = t.leads.filter((l) => {
    if (reqFilter && l.request_title !== reqFilter) return false;
    if (q && !l.name.toLowerCase().includes(q.toLowerCase())) return false;
    if (filter === "opened") return !!l.opened_at && !l.replied_at;
    if (filter === "replied") return !!l.replied_at;
    if (filter === "bounced") return !!l.bounced_at;
    if (filter === "pending") return !!l.sent_at && !l.opened_at && !l.replied_at;
    return true;
  });

  const f = t.funnel;
  const maxDay = Math.max(1, ...t.by_day.map((d) => d.sent));

  return (
    <div className="pipe dash">
      <div className="pipe-head">보낸 메일 대시보드 — 워크스페이스 전체</div>

      {/* 퍼널 — 발송·열람·답장·반송을 한 줄로. 세션을 오가며 손으로
          더하지 않게 서버가 이미 합산했다. */}
      <div className="dash-stats">
        <div className="dash-stat">
          <b>{f.sent}</b><span>발송</span></div>
        <div className="dash-stat hi">
          <b>{f.opened}</b><span>열람 · {Math.round(f.open_rate * 100)}%</span></div>
        <div className="dash-stat ok">
          <b>{f.replied}</b><span>답장 · {Math.round(f.reply_rate * 100)}%</span></div>
        <div className="dash-stat bad">
          <b>{f.bounced}</b><span>반송 · {Math.round(f.bounce_rate * 100)}%</span></div>
      </div>

      {/* 최근 14일 추이 — 발송이 꾸준한지 몰아서 했는지 한눈에 */}
      {f.sent > 0 && (
        <div className="dash-trend">
          {t.by_day.map((d) => (
            <div className="dash-bar" key={d.date}
              title={`${d.date} · ${d.sent}건`}>
              <i style={{ height: `${Math.max(3, (d.sent / maxDay) * 100)}%` }}
                className={d.sent > 0 ? "hit" : ""} />
            </div>))}
        </div>
      )}

      {t.total === 0 ? (
        <div className="quiet" style={{ padding: "10px 14px" }}>
          아직 보낸 메일이 없어요. 후보 카드에서 초안을 만들고 발송하면 여기에 쌓입니다.
        </div>
      ) : (
        <>
          <div className="dash-filters">
            {([["all", "전체"], ["opened", "열람만"], ["replied", "답장"],
               ["bounced", "반송"], ["pending", "무응답"]] as const).map(([k, label]) => (
              <button key={k} className={`dash-f ${filter === k ? "on" : ""}`}
                onClick={() => setFilter(k)}>{label}</button>))}
            <input className="dash-q" placeholder="회사명 검색"
              value={q} onChange={(e) => setQ(e.target.value)} />
            {requests.length > 1 && (
              <select className="dash-q" value={reqFilter}
                onChange={(e) => setReqFilter(e.target.value)}>
                <option value="">모든 요청</option>
                {requests.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>)}
          </div>
          <div className="track-tbl">
            <div className="track-row track-hd">
              <span>회사</span><span>요청</span><span>발송</span><span>열람</span><span>답장</span>
            </div>
            {rows.length === 0 ? (
              <div className="quiet" style={{ padding: "10px 0" }}>조건에 맞는 메일이 없어요.</div>
            ) : rows.map((l) => (
              <div className="track-row" key={l.company_id}>
                <span className="track-nm">{l.name}</span>
                <span className="track-req" title={l.request_title}>{l.request_title}</span>
                <span className="track-t">{fmt(l.sent_at)}</span>
                <span className="track-t">
                  {l.opened_at ? (
                    <>
                      <b className="hit">{fmt(l.opened_at)}</b>
                      {l.open_count > 1 && <i className="cnt">{l.open_count}회</i>}
                      <i className="gap">{gap(l.sent_at, l.opened_at)}</i>
                    </>) : <span className="none">안 열림</span>}
                </span>
                <span className="track-t">
                  {l.bounced_at ? <b className="bad">반송</b>
                    : l.replied_at ? (
                      <>
                        <b className="hit">{fmt(l.replied_at)}</b>
                        <i className="gap">{gap(l.sent_at, l.replied_at)}</i>
                      </>) : <span className="none">—</span>}
                </span>
              </div>))}
          </div>
        </>
      )}
      <div className="quiet" style={{ padding: "8px 14px" }}>
        열람은 추적 픽셀로 재요 — 이미지를 자동으로 받는 메일 앱에서는
        열지 않아도 잡힐 수 있어요. 답장만이 확정 사실입니다.
      </div>
    </div>
  );
}

function PipelineBoard({ pipe, onOpen, onStage }: {
  pipe: Pipeline;
  onOpen: (rid: string) => void;
  onStage: (row: PipeRow, stage: string) => void;
}) {
  if (pipe.total === 0) return (
    <div className="board board-empty">
      아직 저장한 리드가 없어요. 후보 카드의 <b>저장</b>을 누르면 여기 쌓여요.
    </div>);
  return (
    <div className="board">
      {pipe.stages.map((st) => (
        <div className="board-col" key={st}>
          <div className="board-col-h">
            {STAGE_LABEL[st] ?? st}
            <span className="board-n">{(pipe.board[st] ?? []).length}</span>
          </div>
          {(pipe.board[st] ?? []).map((r) => (
            <div className="board-card" key={`${r.request_id}:${r.company_id}`}>
              <div className="board-name">{r.name || r.company_id}</div>
              <button className="linky board-req" onClick={() => onOpen(r.request_id)}
                title="이 요청 열기">{r.request_title}</button>
              <div className="board-meta">
                {r.drafted && <span className="mini">초안</span>}
                {/* 열람은 단계가 아니라 표식 — "보냈고 열어는 봤다"가
                    '연락함' 칸 안에서 구별돼야 다음 행동을 정할 수 있다. */}
                {r.opened && r.replied !== "yes" && (
                  <span className="mini opened" title="상대가 메일을 열어봤어요">열람</span>)}
                {r.replied === "yes" && <span className="mini saved">답장</span>}
                {r.replied === "bounced" && (
                  <span className="mini bounced" title="메일이 반송됐어요 — 주소를 확인하세요">반송</span>)}
              </div>
              <select className="board-sel" value={r.stage}
                onChange={(e) => onStage(r, e.target.value)}>
                {pipe.stages.map((x) => (
                  <option key={x} value={x}>{STAGE_LABEL[x] ?? x}</option>))}
              </select>
            </div>))}
        </div>))}
    </div>
  );
}

/** 무엇을 읽고 이 판단을 했는지 — 페이지 성격과 거기서 나온 것을 잇는다.
 *
 *  URL 목록만 늘어놓으면 로그이지 근거가 아니다. 사용자가 알고 싶은 것은
 *  "채용 페이지에서 이런 걸 봤구나"이지 경로 문자열이 아니다. 신호의 출처
 *  URL을 페이지에 되짚어 그 페이지 아래에 붙이고, 분량이 너무 짧아 근거가
 *  못 되는 페이지는 그렇게 표시한다. */
function ReadEvidence({ dr, ont }: {
  dr: NonNullable<Cand["deep_read"]>; ont?: Ont | null;
}) {
  const [open, setOpen] = useState(false);
  const pages = dr.pages ?? [];
  const kinds = [...new Set(pages.map((p) => p.kind))];
  const contacts = ont?.contacts ?? [];
  return (
    <div className="ev">
      <button className="ev-h" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} 읽은 곳 {pages.length}
        <span className="ev-kinds">{kinds.slice(0, 3).join(" · ")}</span>
      </button>
      {open && (
        <div className="ev-body">
          {pages.map((p) => {
            const sigs = (ont?.signals ?? []).filter(
              (sg) => sg.source_url === p.url);
            return (
              <div className="ev-row" key={p.url}>
                <a href={p.url} target="_blank" rel="noreferrer">{p.kind}</a>
                {p.chars < 200 && (
                  <span className="ev-thin">내용이 거의 없었어요</span>)}
                {sigs.map((sg, i) => (
                  <div className="ev-found" key={i}>
                    여기서 확인 — {sg.evidence}
                    {sg.observed_at ? ` (${sg.observed_at})` : ""}
                  </div>))}
              </div>);
          })}
          {contacts.length > 0 && (
            <div className="ev-row">
              <span className="ev-lb">확보한 연락 창구</span>
              {contacts.map((k, i) => (
                <div className="ev-found" key={i}>
                  {k.channel}
                  {k.value && k.value !== k.channel ? ` — ${k.value}` : ""}
                </div>))}
            </div>)}
        </div>)}
    </div>
  );
}

/** 본문 속 URL을 클릭 가능한 링크로. 표시만 바꾸고 원문은 건드리지 않는다. */
function linkify(text: string): React.ReactNode[] {
  return text.split(/(https?:\/\/[^\s<>()"']+)/g).map((part, i) =>
    /^https?:\/\//.test(part)
      ? <a key={i} href={part} target="_blank" rel="noreferrer">{part}</a>
      : <span key={i}>{part}</span>);
}

/** 메일 초안. 원문과 한국어 대역을 탭으로 오간다 —
 *  읽을 수 없는 메일을 승인해 보낼 수는 없다. 대역이 원문과 같으면
 *  (지정 언어가 한국어인 경우) 탭 자체를 띄우지 않는다. */
const LANG_LABEL: Record<string, string> = {
  ko: "한국어", en: "영어", ja: "일본어", zh: "중국어", de: "독일어",
  fr: "프랑스어", es: "스페인어", it: "이탈리아어", nl: "네덜란드어",
  pt: "포르투갈어", vi: "베트남어", id: "인도네시아어", th: "태국어" };

const VERIFY_KO: Record<string, [string, string]> = {
  // Hunter 원문 어휘를 유지하되 화면에는 뜻을 병기한다 — unknown은 나쁨이
  // 아니라 모름(실측: 한국 메일 서버는 검증을 막는 경우가 많다).
  valid: ["검증됨 — 받는 서버가 이 주소를 확인해줬어요", "ok"],
  accept_all: ["서버가 모든 주소를 받아요 — 반송 여부는 보내봐야 알아요", "mid"],
  unknown: ["검증 불가 — 서버가 확인을 막았어요 (나쁜 신호 아님)", "mid"],
  invalid: ["반송 위험 — 이 주소는 쓰지 마세요", "bad"],
};

/** 테스트 발송 주소 — 로그인한 사람 자신. 남의 주소로 "테스트"를 보내면
 *  그건 테스트가 아니라 발송이다. */
function myEmail(): string {
  try { return localStorage.getItem("a2a:email") || ""; } catch { return ""; }
}

function MailDraft({ d, kit, lang, recipient, rid, cid, api, onSent,
                     onNeedIdentity }: {
  d: Draft; kit?: OutreachKit; lang?: string; recipient?: Recipient;
  rid?: string; cid?: string;
  api?: (p: string, b?: unknown, m?: string) => Promise<any>;
  onSent?: (msg: string) => void;
  /** 발신자 정보가 없어 막혔을 때 그 화면을 열어 준다 — 어디서 채우는지
   *  말해 주는 것보다 데려다 주는 편이 낫다. */
  onNeedIdentity?: () => void }) {
  const hasKo = !!d.body_ko && d.body_ko !== d.body;
  const [ko, setKo] = useState(hasKo);   // 기본은 읽을 수 있는 쪽
  // 사람이 고친 값이 있으면 그것이 진짜다 — 모델 출력은 초안이지 결정이 아니다.
  const [sub0, setSub0] = useState(d.subject);
  const [body0, setBody0] = useState(d.body);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [boxes, setBoxes] = useState<{ id: number; from_email: string }[] | null>(null);
  const [mbox, setMbox] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<"" | "real" | "test">("");
  // 테스트 주소는 고를 수 있어야 한다. 발신함과 같은 주소로 보내면 Gmail이
  // 자기 메일로 알아보고 받은편지함에 안 띄우는 경우가 있다(실측: 발송·열람
  // 기록은 남는데 받은편지함에 없었다). 다른 주소로 보내야 도달을 확인한다.
  const [testTo, setTestTo] = useState(myEmail());
  const [sendMsg, setSendMsg] = useState("");
  const firedRef = useRef(false);
  const sub = ko && hasKo ? (d.subject_ko || d.subject) : sub0;
  const body = ko && hasKo ? (d.body_ko || d.body) : body0;
  const canSend = !!(rid && cid && api);

  async function save() {
    if (!canSend) return;
    setSaving(true);
    try {
      await api!(`/lead-requests/${rid}/candidates/${cid}/draft`,
                 { variant: d.variant_label, subject: sub0, body: body0 }, "PATCH");
      setEditing(false);
      setSendMsg("고친 내용을 저장했어요.");
    } catch (e) { setSendMsg((e as Error).message); }
    finally { setSaving(false); }
  }

  async function openSend(kind: "real" | "test") {
    if (!canSend) return;
    // 막을 것은 **누르기 전에** 막는다. 확인 대화상자까지 다 거친 뒤
    // 409로 튕기면 사용자는 같은 벽에 반복해서 부딪힌다(실측: 3회).
    try {
      const id = await api!("/outreach/identity");
      if ((id.missing ?? []).length) {
        setSendMsg("발신자 정보가 아직 비어 있어요 — 콜드메일에는 발신자의 "
                   + "우편 주소와 수신 거부 방법이 법으로 요구됩니다.");
        onNeedIdentity?.();
        return;
      }
    } catch { /* 조회 실패는 발송을 막지 않는다 — 서버가 다시 판정한다 */ }
    if (!boxes) {
      try {
        const r = await api!("/outreach/mailboxes");
        setBoxes(r.accounts ?? []);
        if ((r.accounts ?? []).length === 1) setMbox(r.accounts[0].id);
      } catch (e) { setSendMsg((e as Error).message); return; }
    }
    setConfirming(kind);
  }

  async function doSend(kind: "real" | "test") {
    // 발송은 되돌릴 수 없다 — ref 래치로 연타를 잠근다(state는 늦다).
    if (firedRef.current || !canSend) return;
    firedRef.current = true;
    setSaving(true);
    try {
      const prep = await api!(
        `/lead-requests/${rid}/candidates/${cid}/outreach/prepare`,
        { mailbox_ids: mbox ? [mbox] : [], variant: d.variant_label,
          ...(kind === "test" ? { to_override: testTo.trim() } : {}) }, "POST");
      const res = await api!(
        `/lead-requests/${rid}/candidates/${cid}/outreach/send`,
        { campaign_id: prep.campaign_id }, "POST");
      const line = kind === "test"
        ? `테스트 메일을 ${res.to}로 보냈어요. 받은편지함에서 확인해보세요.`
        : `${prep.to}로 보냈어요. 열람·답장은 '보낸 메일 추적'에 쌓입니다.`;
      setSendMsg(line);
      setConfirming("");
      onSent?.(line);
    } catch (e) {
      firedRef.current = false;          // 실패했으면 다시 눌러볼 수 있어야 한다
      const m = (e as Error).message;
      // 막힌 이유가 '어디서 고치는지'를 함께 말해야 사용자가 헤매지 않는다.
      setSendMsg(
        m.includes("발신자 정보")
          ? m + " — 왼쪽 '발신자 정보'에서 채울 수 있어요."
          : m.includes("고쳐야 할 것")
            ? m + " — '수정'으로 고친 뒤 다시 보내세요."
            : m);
    } finally { setSaving(false); }
  }
  const vr = recipient?.verify?.result;
  const kitRows: [string, string][] = kit ? ([
    ["받는 사람", kit.to_role ?? ""],
    ["보낼 곳", [kit.channel, kit.channel_value].filter(Boolean).join(" · ")],
    ["왜 지금", kit.why_now ?? ""],
  ] as [string, string][]).filter(([, v]) => v) : [];
  return (
    <div className="card">
      {/* 받는 사람 후보 — 색인 최고 신뢰 주소 + 배달 가능성 검증. 초안만
          있고 보낼 주소가 없으면 사용자는 다시 사이트를 뒤진다. 발송은
          여전히 하지 않는다 — 주소를 '제안'할 뿐이다. */}
      {recipient && (
        <div className={`mail-to ${vr === "invalid" ? "bad" : ""}`}>
          <span className="mail-to-k">받는 사람 후보</span>
          <span className="mail-to-mail">{recipient.email}</span>
          {recipient.position && <span className="mail-to-pos">{recipient.position}</span>}
          {vr && VERIFY_KO[vr] && (
            <span className={`mail-to-v ${VERIFY_KO[vr][1]}`}>{VERIFY_KO[vr][0]}</span>)}
          {recipient.sources?.[0] && (
            <a className="hunt-src" href={recipient.sources[0]} target="_blank"
              rel="noreferrer" title="이 주소가 발견된 페이지">근거</a>)}
        </div>)}
      {kitRows.length > 0 && (
        <div className="mail-kit">
          {kitRows.map(([k, v]) => (
            <div className="mail-kit-row" key={k}>
              <span className="mail-kit-k">{k}</span>
              {/^https?:\/\//.test(v.split(" · ").pop() ?? "")
                ? <a href={v.split(" · ").pop()} target="_blank" rel="noreferrer">{v}</a>
                : <span>{v}</span>}
            </div>))}
        </div>
      )}
      {hasKo && (
        <div className="mail-tabs">
          <button className={`mail-tab ${ko ? "on" : ""}`}
            onClick={() => setKo(true)}>한국어 대역</button>
          <button className={`mail-tab ${!ko ? "on" : ""}`}
            onClick={() => setKo(false)}>
            보낼 원문{lang ? ` (${LANG_LABEL[lang] ?? lang})` : ""}</button>
          {ko && <span className="mail-hint">이건 확인용이에요. 보내는 건 원문입니다.</span>}
        </div>
      )}
      {editing ? (
        <input className="mail-edit sub" value={sub0}
          onChange={(e) => setSub0(e.target.value)} aria-label="메일 제목" />
      ) : (
        <div className="mail-sub">{sub}</div>
      )}
      {/* 본문의 URL은 눌러서 확인할 수 있어야 한다 — 메일이 "여기서 봤습니다"라고
          말하는데 사용자가 그 페이지를 못 열면 검수가 안 된다. 복사는 원문
          그대로 나가므로 표시만 링크로 바꾼다. */}
      {editing ? (
        <textarea className="mail-edit" value={body0} rows={14}
          onChange={(e) => setBody0(e.target.value)}
          aria-label="메일 본문" />
      ) : (
        <div className="mail-body">{linkify(body)}</div>
      )}
      {d.warnings.map((w, k) => (
        <div className="mail-note" key={k}><b>제외됨</b> {w}</div>))}
      {sendMsg && <div className="mail-note send"><b>·</b> {sendMsg}</div>}
      {confirming && (
        /* 발송은 되돌릴 수 없다 — 무엇이 어디로 가는지 한 번 더 보여준 뒤
           누르게 한다. 우리 서비스에서 사람이 직접 보내야 추적도 시작된다. */
        <div className="send-confirm">
          <div className="sc-head">
            {confirming === "test" ? "나에게 테스트 발송" : "이 주소로 실제 발송"}
          </div>
          {confirming === "test" ? (
            <>
              <input className="sc-box" value={testTo} type="email"
                placeholder="테스트로 받을 주소"
                onChange={(e) => setTestTo(e.target.value)} />
              {testTo.trim() && boxes?.some((b) => b.from_email === testTo.trim()) && (
                <div className="quiet">보내는 주소와 같아요 — Gmail이 자기
                  메일로 알아보고 받은편지함에 안 띄울 수 있습니다. 다른
                  주소로 보내면 도달을 확실히 확인할 수 있어요.</div>)}
            </>
          ) : (
            <div className="sc-to">{recipient?.email || "받는 사람 미정"}</div>
          )}
          {(boxes?.length ?? 0) > 1 && (
            <select className="sc-box" value={mbox ?? ""}
              onChange={(e) => setMbox(Number(e.target.value))}>
              <option value="">보낼 메일함 선택</option>
              {boxes!.map((b) => (
                <option key={b.id} value={b.id}>{b.from_email}</option>))}
            </select>)}
          {boxes?.length === 0 && (
            <div className="quiet">연결된 발송 메일함이 없어요 — Smartlead에서 먼저 연결해주세요.</div>)}
          <div className="sc-act">
            <button className="btn coral"
              disabled={saving || !mbox
                        || (confirming === "test" && !testTo.trim())}
              onClick={() => doSend(confirming as "real" | "test")}>
              {saving ? "보내는 중…" : confirming === "test" ? "테스트 보내기" : "지금 보내기"}
            </button>
            <button className="btn" disabled={saving}
              onClick={() => setConfirming("")}>취소</button>
          </div>
        </div>
      )}
      <div className="card-foot">
        {canSend && !editing && (
          <>
            <button className="btn coral" disabled={saving}
              onClick={() => openSend("real")}>발송하기</button>
            <button className="btn" disabled={saving}
              onClick={() => openSend("test")}
              title="받는 사람 대신 내 주소로 먼저 보내봅니다">나에게 테스트</button>
            <button className="btn" onClick={() => { setKo(false); setEditing(true); }}>
              수정</button>
          </>
        )}
        {editing && (
          <>
            <button className="btn coral" disabled={saving} onClick={save}>
              {saving ? "저장 중…" : "저장"}</button>
            <button className="btn" disabled={saving}
              onClick={() => { setSub0(d.subject); setBody0(d.body); setEditing(false); }}>
              되돌리기</button>
          </>
        )}
        <button className="btn"
          onClick={() => navigator.clipboard.writeText(body0)}>원문 복사</button>
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
/** 축 적합도 레이더 — 열 축의 강약을 한눈에.
 *
 *  숫자 열 개를 나열하면 아무도 안 읽는다. 어느 축이 맞고 어느 축이 어긋나는지는
 *  모양으로 보는 게 빠르다. 판정이 없는 축(fit=null)은 그리지 않는다 — 0으로
 *  두면 '나쁨'으로 잘못 읽힌다. */
function AxisRadar({ axes }: { axes: Ont["axes"] }) {
  const rows = Object.entries(axes)
    .filter(([, a]) => typeof a.fit === "number");
  if (rows.length < 3) return null;
  const R = 46, C = 56, n = rows.length;
  const pt = (i: number, r: number) => {
    const ang = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [C + Math.cos(ang) * r, C + Math.sin(ang) * r];
  };
  const poly = rows
    .map(([, a], i) => pt(i, R * Math.max(0.06, a.fit as number)).join(","))
    .join(" ");
  const ring = (f: number) =>
    rows.map((_, i) => pt(i, R * f).join(",")).join(" ");
  return (
    <div className="radar">
      <svg viewBox="0 0 112 112" width="112" height="112" aria-hidden>
        {[0.33, 0.66, 1].map((f) => (
          <polygon key={f} points={ring(f)} className="radar-ring" />))}
        <polygon points={poly} className="radar-area" />
      </svg>
      {/* 능력치 표 — 숫자만 있는 막대는 믿을 근거가 없다. FM의 선수 능력치처럼
          수치·값·이유를 한 줄에 붙여, 왜 그 점수인지 눈으로 따라가게 한다. */}
      <div className="radar-lg">
        {rows.map(([k, a]) => {
          const n = Math.round((a.fit as number) * 100);
          const tier = n >= 70 ? "hi" : n >= 40 ? "mid" : "lo";
          return (
            <div className="radar-li" key={k}>
              <span className="radar-n">{AXIS_KO[k] ?? k}</span>
              <span className="radar-b">
                <i className={tier} style={{ width: `${Math.max(3, n)}%` }} />
              </span>
              <span className={`radar-num ${tier}`}>{n}</span>
              <span className="radar-why">
                {a.why || a.value}
              </span>
            </div>);
        })}
      </div>
    </div>
  );
}

/* memo인 이유 — 이 트리는 카드 하나당 SVG 폴리곤 4개 + 능력치 막대 10줄 +
   판독 행·신호·접점 목록이고 카드가 10장이다. 진단 실측: memo 0회라 입력
   onChange·진행 tick 폴링 등 모든 state 변경마다 이 트리 전체가 다시
   실행됐다. ont의 참조는 cands가 갈릴 때만 바뀌므로 memo가 정확히 걸러낸다. */
const OntologyView = memo(function OntologyView(
  { ont, sourceUrl }: { ont: Ont; sourceUrl: string }) {
  const known = Object.entries(ont.axes).filter(([, a]) => a.status !== "unknown");
  if (!known.length) return null;
  return (
    <div className="ont">
      {/* 접지 않는다 — 판독은 "연락할까"를 정하는 재료다. 토글 뒤에 두면
          대부분 열지 않고, 카드는 다시 한 줄 신호로 되돌아간다. */}
      <div className="ont-head">
        판독 {known.length}/{Object.keys(ont.axes).length}축
        {ont.confirmed_ratio !== undefined &&
          <span className="ont-ratio">근거 확인 {Math.round(ont.confirmed_ratio * 100)}%</span>}
      </div>
      {(
        <div className="ont-body">
          {/* 읽기 층 — 축 나열은 "그래서 연락할까"에 답하지 못한다.
              관측(상황·접점)과 추론을 나눠 보여주는 것이 기획안 §7.2 원칙. */}
          {ont.reading?.situation && (
            <div className="rd">
              <div className="rd-k">지금 이 회사는</div>
              <div className="rd-v">{ont.reading.situation}</div>
            </div>)}
          {ont.reading?.fit && (
            <div className="rd">
              <div className="rd-k">우리와 맞닿는 곳</div>
              <div className="rd-v">{ont.reading.fit}</div>
            </div>)}
          {ont.reading?.inference && (
            <div className="rd">
              <div className="rd-k rd-inf">추론 — 자료에 없는 해석</div>
              <div className="rd-v">{ont.reading.inference}</div>
            </div>)}
          {(ont.reading?.unknowns?.length ?? 0) > 0 && (
            <div className="rd">
              <div className="rd-k rd-ask">연락 전 확인할 것</div>
              <ul className="rd-ul">
                {ont.reading!.unknowns!.map((u, i) => <li key={i}>{u}</li>)}
              </ul>
            </div>)}
          <AxisRadar axes={ont.axes} />
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
                  {/* 출처 도메인 병기 — 접점이 후보 회사와 다른 도메인이면
                      사용자가 눈으로 잡는다. 웹 스니펫은 우리가 통제하지 않는
                      텍스트라 주입된 연락처가 섞일 수 있다(감사 확정 low). */}
                  {(() => {
                    const host = (u: string) => {
                      try { return new URL(u).hostname.replace(/^www\./, ""); }
                      catch { return ""; }
                    };
                    const src = host(sourceUrl);
                    const val = host(ct.value);
                    return val && src && val !== src
                      ? <span className="ct-warn" title={
                          `이 접점은 후보 출처(${src})와 다른 도메인(${val})입니다 — 확인하세요`}
                        >≠ {src}</span>
                      : null;
                  })()}
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
});

/** 명확화 질문 카드 — 선택지는 전부 실제 후보를 인용한 것만 온다(서버 집행).
 *  질문이 비어도 '다시 찾기/확정' 손잡이는 남는다 — 멀티턴의 최소 단위. */
function ClarifyCard({ qs, onRefine, onDone }: {
  qs: ClarifyQ[];
  onRefine: (answers: string[]) => Promise<boolean>;
  onDone: () => Promise<boolean>;
}) {
  const [picked, setPicked] = useState<Map<string, string>>(new Map());
  const [free, setFree] = useState("");
  const [used, setUsed] = useState(false);
  return (
    <div className="card" style={{ maxWidth: 600 }}>
      {qs.map((q) => (
        <div className="clar-q" key={q.id}>
          <div className="clar-title">{q.question}</div>
          <div className="clar-opts">
            {q.options.map((o) => (
              <button key={o.label} disabled={used}
                className={`clar-opt ${picked.get(q.id) === o.label ? "on" : ""}`}
                onClick={() => setPicked((m) => {
                  const n = new Map(m);
                  n.get(q.id) === o.label ? n.delete(q.id) : n.set(q.id, o.label);
                  return n;
                })}>
                {o.label}
                <span className="clar-n">{o.company_ids.length}곳</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="clar-q">
        <input className="clar-free" disabled={used} value={free}
          placeholder="직접 조건 추가 (예: 냉장 물류가 되는 곳만)"
          onChange={(e) => setFree(e.target.value)} />
      </div>
      <div className="card-foot">
        {/* 요청이 실패하면 손잡이가 살아남아야 한다 — 이전엔 클릭 즉시
            래칭해서, 실패하면 대화가 그 자리에서 끝났다. */}
        <button className="btn pri" disabled={used}
          onClick={async () => {
            setUsed(true);
            const ok = await onRefine(
              [...picked.values(), ...(free.trim() ? [free.trim()] : [])]);
            if (!ok) setUsed(false);
          }}>
          반영해서 다시 찾기
        </button>
        <button className="btn" disabled={used}
          onClick={async () => {
            setUsed(true);
            if (!await onDone()) setUsed(false);
          }}>이 정도면 확정</button>
      </div>
    </div>
  );
}

/** 업종 다중 선택 + 과거 실적 기반 키워드 추천.
 *  추천이 비어 있으면 그 자리를 비운다 — 이력이 없는데 그럴듯한 키워드를
 *  지어내면 추천이 아니라 또 하나의 추측이다. */
function SegmentPicker({ segments, recs, onSubmit }: {
  segments: Seg[]; recs: KwRec[];
  onSubmit: (segs: string[], extra: string[]) => Promise<boolean>;
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
            <span className="seg-lb">{sg.label}
              {sg.reach && REACH_TAG[sg.reach] && (
                <span className={`chip ${REACH_TAG[sg.reach][1]}`}
                  title="이 경로에서 첫 콜드 아웃리치가 실무자 답장으로 이어질 가능성">
                  {REACH_TAG[sg.reach][0]}</span>)}
            </span>
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
          onClick={async () => {
            setDone(true);
            if (!await onSubmit([...picked], [...kws])) setDone(false);
          }}>
          {picked.size ? `${picked.size}개 업종으로 검색` : "업종을 고르세요"}
        </button>
        <button className="btn" disabled={done}
          onClick={async () => {
            setDone(true);
            if (!await onSubmit([], [...kws])) setDone(false);
          }}>
          업종 안 나누고 검색
        </button>
      </div>
    </div>
  );
}

/** 첫 입력이 회사명으로 보이는가. 짧은 한 줄이면 이름, 아니면 자료다.
 *  URL·이메일은 이름이 아니다(붙여넣은 홈페이지 주소가 회사명이 되면 안 된다). */
/** 상호가 로마자가 아니면 영문 표기를 따로 받아야 한다 — 해외 메일에 한글·
 *  한자 상호를 그대로 넣으면 상대가 읽지 못한다. */
function needsLatinName(v: string): boolean {
  return [...v].some((c) => c.charCodeAt(0) >= 0x2e80);
}

function looksLikeName(v: string): boolean {
  if (v.length > 40 || v.includes("\n")) return false;
  if (/https?:\/\/|www\.|@/i.test(v)) return false;
  return true;
}

type BriefDraft = { region?: string; target_type?: string; notes?: string;
  purpose?: "revenue" | "poc"; why?: string };

function BriefForm({ onSubmit, draft }:
  { onSubmit: (intent: Record<string, unknown>) => void;
    draft?: BriefDraft }) {
  // 기본값을 비워 둔다. 예전엔 호텔 데모 값("일본"/"독립 호텔"/"객실
  // 리노베이션과 운영 개선")이 그대로 박혀 있어서, 프로필이 무엇이든 늘 그
  // 값으로 시작했다 — 탄소 MRV 회사에게 일본 호텔을 찾자고 제안하는 꼴이다.
  // 채워진 칸은 사용자가 '엔진이 내 프로필을 읽고 제안한 값'으로 읽는다.
  // 근거 없는 값을 채워 두는 것은 빈칸보다 나쁘다.
  const firedRef = useRef(false);
  const [fired, setFired] = useState(false);
  const [region, setRegion] = useState(draft?.region ?? "");
  const [ttype, setTtype] = useState(draft?.target_type ?? "");
  const [notes, setNotes] = useState(draft?.notes ?? "");
  const [count, setCount] = useState(10);
  const [purpose, setPurpose] =
    useState<"revenue" | "poc">(draft?.purpose ?? "revenue");
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
          {/* 지역은 비워 두면 엔진이 지역어를 검색어에 아예 넣지 않는다
              (retrieve.py의 '미지정' 규칙) — 빈칸이 곧 '전 세계'다. */}
          <label>지역<input value={region} placeholder="비워 두면 전 세계"
            onChange={(e) => setRegion(e.target.value)} /></label>
          <label>상대 유형<input value={ttype}
            placeholder="예: 자재를 납품받는 제조사"
            onChange={(e) => setTtype(e.target.value)} /></label>
          <label>제안 내용<input value={notes}
            placeholder="상대에게 무엇을 제안하나요"
            onChange={(e) => setNotes(e.target.value)} /></label>
          <label>찾을 수<input type="number" min={1} max={30} value={count}
            onChange={(e) => setCount(+e.target.value || 10)} /></label>
        </div>
        {draft?.why && (
          <p className="brief-why">
            프로필을 보고 위와 같이 채웠어요 — {draft.why} 고쳐도 됩니다.
          </p>
        )}
      </div>
      <div className="card-foot">
        {/* ref 래치 — state 가드는 다음 렌더까지 늦어 연타가 통과한다.
            실측: 이 버튼 5연타로 요청 5개·브리프 5개(LLM 5번)가 생겼고,
            사이드바에 후보 0짜리 유령 요청 4개가 남았다. */}
        <button className="btn pri" disabled={fired}
          onClick={() => {
            if (firedRef.current) return;
            firedRef.current = true;
            setFired(true);
            onSubmit({
              value_props: ["revenue_growth"], target_region: region,
              target_type: ttype, notes, lead_count: count, purpose,
            });
          }}>{fired ? "요청을 만들고 있어요…" : "이 조건으로 후보 찾기"}</button>
      </div>
    </div>
  );
}
