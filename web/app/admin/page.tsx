"use client";
/* 플랫폼 어드민 콘솔 — 모든 테넌트·셀을 한 화면에서. 서버 원본은 /platform/v1.
 *
 * 권한은 GET /me의 role로 버튼을 가리지만, 그건 안내일 뿐이다. 서버가
 * 매 요청마다 platform_admins를 다시 확인하므로(platform.require) 화면이
 * 틀려도 데이터는 안 샌다 — 대신 서버가 거절하면 그 메시지를 그대로 보여
 * 준다. 조용히 삼키면 "왜 안 되는지" 모르는 콘솔이 된다.
 *
 * 실시간은 폴링이다(team.tsx와 같은 규칙). 배포 대상이 긴 연결을 붙잡지
 * 못하고, 메트릭 자체가 분 단위 롤업이라 더 촘촘히 볼 이유도 없다.
 *
 * 전역 상태 라이브러리는 쓰지 않는다 — 뷰끼리 공유하는 것은 me(역할)와
 * 셀 목록 정도라 props로 충분하고, 각 뷰는 열릴 때 자기 데이터를 새로 받는다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import { authHeaders, devUser, isConfigured } from "../supabase";
import "./admin.css";

// ═══════════ 타입 — dev 엔진 실측 JSON 기준 ═══════════

type Role = "superadmin" | "operator" | "viewer";
type Me = { user_id: string; email: string; role: Role };

/** 분위수 값. 히스토그램 마지막 칸은 서버가 inf로 준다 — JSON 직렬화
 *  방식에 따라 숫자(Infinity)·문자열("Infinity")·null 어느 것으로도 올 수
 *  있어 셋 다 받는다. */
type Pval = number | string | null;
type RouteSum = { requests: number; errors: number; error_rate: number;
  p50: Pval; p95: Pval; p99: Pval };
type Point = { minute: string; requests: number; errors: number;
  p50: Pval; p95: Pval; p99: Pval };
type Metrics = { minutes: number; summary: Record<string, RouteSum>;
  series: Record<string, Point[]> };
type Overview = {
  tenants: { active: number; suspended: number; other: number; users: number; open_alerts: number };
  last_15m: Record<string, RouteSum>;
  fanout: Record<string, number>;
  top: { slug: string; name: string; cell_id: string; requests: number; errors: number }[];
};
type Tenant = { id: string; slug: string; name: string; cell_id: string; plan: string;
  status: string; schema_version: number; retention_hot_months: number; legal_hold: boolean;
  archive_purge_months: number | null; created_at: string; users: number;
  req_15m: number; err_15m: number; open_alerts: number };
type Cell = { id: string; region: string; status: string; dedicated: boolean;
  max_tenants: number; created_at: string; tenants: number; reachable: boolean;
  size?: number; conns?: number; max_conns?: number; schemas?: number; ping_ms?: number;
  error?: string };
type MoveReport = { from?: string; to?: string; copy_sec?: number; copy_bytes?: number;
  bytes?: number; bytes_in_table?: number;
  write_pause_sec?: number; delta_messages?: number; delta?: string;
  rows?: Record<string, number> };
type Move = { id: number; tenant_id: string; slug: string; from_cell: string; to_cell: string;
  state: string; actor: string; started_at: string; fenced_at: string | null;
  switched_at: string | null; finished_at: string | null; report: MoveReport;
  error: string | null; phase?: string; worker?: string | null; heartbeat_at?: string | null;
  cancel_requested?: boolean };
const MOVE_ACTIVE = new Set(["queued", "copying", "fencing"]);
type Alert = { id: number; tenant_id: string | null; cell_id: string | null; kind: string;
  severity: "info" | "warning" | "critical"; opened_at: string; closed_at: string | null;
  detail: Record<string, unknown>; slug: string | null };
type AuditRow = { id: number; at: string; actor: string; action: string;
  tenant_id: string | null; target: string | null; reason: string | null;
  detail: Record<string, unknown> };
type OpsChannel = { id: number; name: string; last_seq: number; created_at: string };
type OpsMsg = { channel_id: number; seq: number; author: string; body: string;
  kind: string; created_at: string };
type Announcement = { id: number; author: string; body: string; created_at: string;
  targets: number; delivered: number; failed: number };
type AdmChannel = { id: number; name: string; visibility: string; kind: string;
  last_seq: number; last_message_at: string | null; archived_at: string | null; members: number };
type AdmMsg = { seq: number; parent_seq: number | null; author: string; body: string;
  kind: string; created_at: string; edited_at: string | null; deleted: boolean;
  reply_count: number };

// ═══════════ API ═══════════

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

/** 서버 에러 본문 → 사람이 읽을 문장. 엔진은 {"error":{code,message,details}},
 *  FastAPI 기본은 {"detail": ...} 두 형태다. 스키마 위반은 message가
 *  "스키마 위반"뿐이라 어느 칸이 틀렸는지 첫 항목을 덧붙인다. */
function errorText(status: number, j: any): string {
  const e = j?.error;
  if (e?.message) {
    const first = e.details?.errors?.[0];
    if (first?.loc) return `${e.message} (${first.loc.slice(1).join(".")}: ${first.msg})`;
    return e.message;
  }
  if (typeof j?.detail === "string") return j.detail;
  if (j?.detail) return JSON.stringify(j.detail);
  return `요청 실패 (HTTP ${status})`;
}

/** 모든 호출의 한 통로. 헤더는 매번 새로 만든다 — 토큰 갱신과 ?as= 전환이
 *  요청 시점에 반영돼야 하기 때문이다(렌더 중에는 부르지 않는다). */
async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers = await authHeaders();
  const r = await fetch(`/api/platform/v1${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  const text = await r.text();
  let j: any = null;
  try { j = text ? JSON.parse(text) : null; } catch { j = null; }
  if (!r.ok) throw new ApiError(r.status, j ? errorText(r.status, j) : (text.slice(0, 200) || `요청 실패 (HTTP ${r.status})`));
  return j as T;
}

function msgOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** GET 하나를 들고 있는 훅. every(ms)를 주면 폴링한다 — 탭이 가려져 있으면
 *  건너뛴다(보지도 않는 화면이 15초마다 셀을 찔러 ping을 재면 낭비다).
 *  늦게 도착한 이전 응답이 새 응답을 덮지 않도록 요청마다 번호를 붙인다. */
function useLoad<T>(path: string | null, every?: number) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const n = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (n === seq.current) { setData(d); setErr(null); }
    } catch (e) {
      if (n === seq.current) setErr(msgOf(e));
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(null);
    setErr(null);
    reload();
    if (!every) return;
    const id = setInterval(() => { if (!document.hidden) reload(); }, every);
    return () => clearInterval(id);
  }, [reload, every]);

  return { data, err, loading, reload };
}

const RANK: Record<Role, number> = { superadmin: 3, operator: 2, viewer: 1 };
function can(me: Me, need: Role): boolean { return RANK[me.role] >= RANK[need]; }

// ═══════════ 표기 ═══════════

/** p값은 버킷 상한이다(실제 지연이 아니라 "이 값 이하"). 마지막 칸은
 *  상한이 없으므로 ">2500ms"로 쓴다. */
function fmtMs(v: Pval): string {
  if (v === null || v === undefined) return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n) || n > 2500) return ">2500ms";
  return `≤${n}ms`;
}
function msNum(v: Pval): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isNaN(n)) return null;
  return Number.isFinite(n) ? n : 3000;      // 그래프에서는 상한 너머 한 칸으로 그린다
}
function fmtPct(r: number): string { return `${(r * 100).toFixed(r < 0.01 && r > 0 ? 2 : 1)}%`; }
function fmtN(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("ko-KR");
}
function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ko-KR", { month: "numeric", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtMB(bytes: number | undefined): string {
  return bytes === undefined ? "—" : `${(bytes / 1048576).toFixed(1)} MB`;
}
function compactJson(v: unknown): string {
  if (!v || (typeof v === "object" && !Object.keys(v as object).length)) return "";
  return JSON.stringify(v);
}

const ROUTE_KO: Record<string, string> = {
  post: "게시", list: "목록", edit: "수정·삭제", stream: "스트림", admin: "관리", other: "기타",
};
const STATUS_KO: Record<string, string> = {
  active: "활성", suspended: "정지", migrating: "이동 중", provisioning: "생성 중", deleted: "삭제",
  open: "열림", full: "가득 참", draining: "비우는 중", down: "중단",
};
const MOVE_KO: Record<string, string> = {
  queued: "대기", copying: "복사 중", fencing: "펜스", switched: "전환 완료", retired: "정리 완료",
  failed: "실패", rolled_back: "롤백", cancelled: "취소",
};
const SEV_KO: Record<string, string> = { info: "정보", warning: "경고", critical: "심각" };

function tone(status: string): string {
  if (["active", "open", "switched", "retired", "info"].includes(status)) return "green";
  if (["suspended", "down", "failed", "critical"].includes(status)) return "coral";
  if (["migrating", "draining", "full", "fencing", "copying", "warning", "rolled_back"].includes(status)) return "amber";
  return "blue";
}

// ═══════════ 공용 조각 ═══════════

function Badge({ s, label }: { s: string; label?: string }) {
  return <span className={`adm-badge adm-${tone(s)}`}>{label ?? STATUS_KO[s] ?? s}</span>;
}
function ErrBox({ msg }: { msg: string | null }) {
  return msg ? <div className="adm-err" role="alert">{msg}</div> : null;
}
function OkBox({ msg }: { msg: string | null }) {
  return msg ? <div className="adm-ok" role="status">{msg}</div> : null;
}
function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="adm-sec">
      <div className="adm-sec-h"><h2>{title}</h2>{right}</div>
      {children}
    </section>
  );
}
/** 넓은 표는 자기 상자 안에서만 가로로 민다 — 375px에서 페이지 전체가
 *  옆으로 밀리면 내비게이션까지 같이 사라진다. */
function TableBox({ children }: { children: ReactNode }) {
  return <div className="adm-tbox">{children}</div>;
}
function Empty({ children }: { children: ReactNode }) {
  return <div className="adm-empty">{children}</div>;
}
function Locked({ need }: { need: string }) {
  return <div className="adm-locked">{need} 이상 권한이 필요한 기능입니다.</div>;
}

/** 버튼 동작 하나의 진행·오류·결과를 묶는다. 실패 메시지는 서버 문장 그대로. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<string | void>) => {
    setBusy(true); setErr(null); setOk(null);
    try {
      const m = await fn();
      if (m) setOk(m);
      return true;
    } catch (e) {
      setErr(msgOf(e));
      return false;
    } finally { setBusy(false); }
  }, []);
  return { busy, err, ok, run, setErr, setOk };
}

// ═══════════ 뷰 정의 ═══════════

type ViewId = "overview" | "tenants" | "cells" | "alerts" | "moves" | "ops" | "ann" | "audit";
const VIEWS: { id: ViewId; label: string }[] = [
  { id: "overview", label: "개요" },
  { id: "tenants", label: "테넌트" },
  { id: "cells", label: "셀" },
  { id: "alerts", label: "알림" },
  { id: "moves", label: "이동 기록" },
  { id: "ops", label: "운영 채널" },
  { id: "ann", label: "공지" },
  { id: "audit", label: "감사 로그" },
];

export default function AdminPage() {
  // me는 마운트 후에만 받는다 — devUser()가 window를 읽어 서버 렌더와 어긋나기 때문.
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "forbidden" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<ViewId>("overview");

  const loadMe = useCallback(async () => {
    setState("loading");
    try {
      setMe(await api<Me>("/me"));
      setState("ok");
    } catch (e) {
      // 403은 "권한 없음"이라는 정상 응답이다 — 일반 오류와 화면을 가른다.
      if (e instanceof ApiError && e.status === 403) setState("forbidden");
      else { setErr(msgOf(e)); setState("error"); }
    }
  }, []);

  useEffect(() => {
    loadMe();
    // 새로고침해도 보던 뷰에 머물게 해시에 둔다(주소 공유도 된다).
    const h = window.location.hash.slice(1) as ViewId;
    if (VIEWS.some((v) => v.id === h)) setView(h);
  }, [loadMe]);

  const go = (v: ViewId) => {
    setView(v);
    try { window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${v}`); } catch { /* 해시는 편의 기능 */ }
  };

  if (state === "loading") return <div className="adm-root adm-center"><div className="adm-muted">불러오는 중…</div></div>;
  if (state === "forbidden") return <Forbidden />;
  if (state === "error" || !me)
    return (
      <div className="adm-root adm-center">
        <div className="adm-card adm-gate">
          <h1>콘솔을 열 수 없습니다</h1>
          <ErrBox msg={err} />
          <button className="adm-btn" onClick={loadMe}>다시 시도</button>
        </div>
      </div>
    );

  return (
    <div className="adm-root adm-shell">
      <nav className="adm-nav" aria-label="어드민 메뉴">
        <div className="adm-brand">플랫폼 어드민</div>
        <div className="adm-tabs" role="tablist">
          {VIEWS.map((v) => (
            <button key={v.id} role="tab" aria-selected={view === v.id}
              className={`adm-tab${view === v.id ? " on" : ""}`} onClick={() => go(v.id)}>
              {v.label}
            </button>
          ))}
        </div>
        <div className="adm-who">
          <span className="adm-who-mail">{me.email}</span>
          <Badge s={me.role === "superadmin" ? "critical" : me.role === "operator" ? "warning" : "info"}
            label={me.role} />
          {!isConfigured && <span className="adm-who-dev">dev</span>}
        </div>
      </nav>
      <main className="adm-main">
        {view === "overview" && <OverviewView />}
        {view === "tenants" && <TenantsView me={me} />}
        {view === "cells" && <CellsView me={me} />}
        {view === "alerts" && <AlertsView />}
        {view === "moves" && <MovesView me={me} />}
        {view === "ops" && <OpsView me={me} />}
        {view === "ann" && <AnnouncementsView me={me} />}
        {view === "audit" && <AuditView />}
      </main>
    </div>
  );
}

function Forbidden() {
  const [who, setWho] = useState("");
  // 누구로 거절됐는지 보여 준다 — dev에서 ?as=를 잘못 줬을 때 바로 알아챈다.
  useEffect(() => { if (!isConfigured) setWho(devUser()); }, []);
  return (
    <div className="adm-root adm-center">
      <div className="adm-card adm-gate">
        <h1>플랫폼 어드민 권한이 없습니다</h1>
        <p className="adm-muted">이 콘솔은 플랫폼 관리자(platform_admins)로 등록된 계정만 쓸 수 있습니다.
          권한이 필요하면 superadmin에게 요청하세요.</p>
        {who && <p className="adm-muted adm-mono">현재 dev 사용자: {who}</p>}
        <a className="adm-btn" href="/">워크스페이스로 돌아가기</a>
      </div>
    </div>
  );
}

// ═══════════ 1. 개요 ═══════════

function OverviewView() {
  const ov = useLoad<Overview>("/overview", 15000);
  const al = useLoad<{ alerts: Alert[] }>("/alerts?open_only=true", 15000);
  const o = ov.data;
  return (
    <>
      <div className="adm-title-row">
        <h1>개요</h1>
        <span className="adm-muted">15초마다 갱신{ov.loading ? " · 갱신 중…" : ""}</span>
      </div>
      <ErrBox msg={ov.err} />
      {o && (
        <>
          <div className="adm-kpis">
            <Kpi label="활성 테넌트" value={o.tenants.active} tone="green" />
            <Kpi label="정지 테넌트" value={o.tenants.suspended} tone={o.tenants.suspended ? "amber" : undefined} />
            <Kpi label="사용자" value={o.tenants.users} />
            <Kpi label="열린 알림" value={o.tenants.open_alerts} tone={o.tenants.open_alerts ? "coral" : undefined} />
          </div>
          {o.tenants.other > 0 && <p className="adm-muted">생성·이동 중 등 기타 상태 테넌트 {o.tenants.other}곳</p>}
          <Section title="최근 15분 · 경로별">
            <RouteTable summary={o.last_15m} />
          </Section>
          <div className="adm-grid2">
            <Section title="팬아웃 (이 인스턴스)">
              {Object.keys(o.fanout).length ? (
                <div className="adm-chips">
                  {Object.entries(o.fanout).map(([k, v]) => (
                    <span key={k} className="adm-chip"><span className="adm-mono">{k}</span> {fmtN(v)}</span>
                  ))}
                </div>
              ) : <Empty>이 인스턴스가 시작된 뒤 팬아웃 기록이 없습니다.</Empty>}
              <p className="adm-hint">팬아웃 수치는 응답한 엔진 인스턴스 메모리 기준입니다(인스턴스별로 다를 수 있음).</p>
            </Section>
            <Section title="상위 테넌트 (15분 요청)">
              {o.top.length ? (
                <ol className="adm-top">
                  {o.top.map((t) => (
                    <li key={t.slug}>
                      <span className="adm-top-n">{t.name} <span className="adm-mono adm-muted">{t.slug} · {t.cell_id}</span></span>
                      <span className="adm-mono">{fmtN(t.requests)}{t.errors ? <span className="adm-c-coral"> · 오류 {fmtN(t.errors)}</span> : null}</span>
                    </li>
                  ))}
                </ol>
              ) : <Empty>최근 15분 요청이 없습니다.</Empty>}
            </Section>
          </div>
        </>
      )}
      <Section title="열린 알림">
        <ErrBox msg={al.err} />
        {al.data && <AlertList alerts={al.data.alerts} />}
      </Section>
    </>
  );
}

function Kpi({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="adm-kpi">
      <div className="adm-kpi-l">{label}</div>
      <div className={`adm-kpi-v${tone ? ` adm-c-${tone}` : ""}`}>{fmtN(value)}</div>
    </div>
  );
}

function RouteTable({ summary }: { summary: Record<string, RouteSum> }) {
  const rows = Object.entries(summary).sort((a, b) => b[1].requests - a[1].requests);
  if (!rows.length) return <Empty>이 구간에 기록된 요청이 없습니다.</Empty>;
  return (
    <>
      <TableBox>
        <table className="adm-table">
          <thead><tr><th>경로</th><th className="r">요청</th><th className="r">오류</th><th className="r">오류율</th>
            <th className="r">p50</th><th className="r">p95</th><th className="r">p99</th></tr></thead>
          <tbody>
            {rows.map(([k, s]) => (
              <tr key={k}>
                <td>{ROUTE_KO[k] ?? k} <span className="adm-mono adm-muted">{k}</span></td>
                <td className="r adm-mono">{fmtN(s.requests)}</td>
                <td className="r adm-mono">{fmtN(s.errors)}</td>
                <td className={`r adm-mono${s.error_rate > 0.02 ? " adm-c-coral" : ""}`}>{fmtPct(s.error_rate)}</td>
                <td className="r adm-mono">{fmtMs(s.p50)}</td>
                <td className="r adm-mono">{fmtMs(s.p95)}</td>
                <td className="r adm-mono">{fmtMs(s.p99)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      <p className="adm-hint">p값은 히스토그램 버킷 상한입니다(5·10·25·50·100·250·500·1000·2500ms). "≤25ms"는 25ms 이하라는 뜻입니다.</p>
    </>
  );
}

function AlertList({ alerts }: { alerts: Alert[] }) {
  if (!alerts.length) return <Empty>알림이 없습니다.</Empty>;
  return (
    <TableBox>
      <table className="adm-table">
        <thead><tr><th>심각도</th><th>종류</th><th>대상</th><th>열림</th><th>닫힘</th><th>내용</th></tr></thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id}>
              <td><Badge s={a.severity} label={SEV_KO[a.severity] ?? a.severity} /></td>
              <td className="adm-mono">{a.kind}</td>
              <td>{a.slug ? `테넌트 ${a.slug}` : a.cell_id ? `셀 ${a.cell_id}` : "—"}</td>
              <td className="adm-nowrap">{fmtTime(a.opened_at)}</td>
              <td className="adm-nowrap">{a.closed_at ? fmtTime(a.closed_at) : <Badge s="warning" label="열림" />}</td>
              <td className="adm-mono adm-detail">{compactJson(a.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}

// ═══════════ 2. 테넌트 ═══════════

function TenantsView({ me }: { me: Me }) {
  const list = useLoad<{ tenants: Tenant[] }>("/tenants", 15000);
  const cells = useLoad<{ cells: Cell[] }>("/cells");
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const tenants = list.data?.tenants ?? [];
  const cur = tenants.find((t) => t.slug === sel) ?? null;

  return (
    <>
      <div className="adm-title-row">
        <h1>테넌트</h1>
        {can(me, "operator") && (
          <button className="adm-btn" onClick={() => setCreating((v) => !v)}>
            {creating ? "닫기" : "테넌트 만들기"}
          </button>
        )}
      </div>
      {creating && (
        <CreateTenant cells={cells.data?.cells ?? []}
          onDone={(slug) => { setCreating(false); list.reload(); setSel(slug); }} />
      )}
      <ErrBox msg={list.err} />
      {list.data && (tenants.length ? (
        <TableBox>
          <table className="adm-table adm-click">
            <thead><tr><th>이름 / slug</th><th>셀</th><th>플랜</th><th>상태</th><th className="r">사용자</th>
              <th className="r">요청 15분</th><th className="r">오류 15분</th><th className="r">열린 알림</th><th className="r">스키마</th></tr></thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id} className={t.slug === sel ? "on" : ""} tabIndex={0}
                  onClick={() => setSel(t.slug === sel ? null : t.slug)}
                  onKeyDown={(e) => { if (e.key === "Enter") setSel(t.slug === sel ? null : t.slug); }}>
                  <td><div className="adm-strong">{t.name}</div><div className="adm-mono adm-muted">{t.slug}</div></td>
                  <td className="adm-mono">{t.cell_id}</td>
                  <td>{t.plan}</td>
                  <td><Badge s={t.status} />{t.legal_hold && <Badge s="info" label="법적 보존" />}</td>
                  <td className="r adm-mono">{fmtN(t.users)}</td>
                  <td className="r adm-mono">{fmtN(t.req_15m)}</td>
                  <td className={`r adm-mono${t.err_15m ? " adm-c-coral" : ""}`}>{fmtN(t.err_15m)}</td>
                  <td className={`r adm-mono${t.open_alerts ? " adm-c-coral" : ""}`}>{fmtN(t.open_alerts)}</td>
                  <td className="r adm-mono">v{t.schema_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableBox>
      ) : <Empty>테넌트가 없습니다.</Empty>)}
      {sel && !cur && list.data && <ErrBox msg={`테넌트 ${sel}을(를) 목록에서 찾을 수 없습니다.`} />}
      {cur && (
        // key로 테넌트가 바뀌면 상세 패널 상태(사유·열람 중인 채널)를 통째로 버린다 —
        // A사 열람 사유가 B사 열람에 그대로 쓰이면 안 된다.
        <TenantDetail key={cur.slug} me={me} t={cur} cells={cells.data?.cells ?? []}
          onChanged={list.reload} onClose={() => setSel(null)} />
      )}
    </>
  );
}

function CreateTenant({ cells, onDone }: { cells: Cell[]; onDone: (slug: string) => void }) {
  const [f, setF] = useState({ slug: "", name: "", owner_uid: "", owner_email: "", plan: "standard", cell: "" });
  const a = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    a.run(async () => {
      const r = await api<{ slug: string; cell_id: string }>("/tenants", {
        body: { ...f, slug: f.slug.trim(), name: f.name.trim(), cell: f.cell || null } });
      onDone(r.slug);
    });
  };
  return (
    <form className="adm-card adm-form" onSubmit={submit}>
      <h3>새 테넌트</h3>
      <div className="adm-fgrid">
        <label>slug<input required value={f.slug} onChange={set("slug")} placeholder="acme" className="adm-mono"
          pattern="[a-z][a-z0-9_]{1,40}" title="영문 소문자로 시작, 소문자·숫자·밑줄 2~41자" /></label>
        <label>이름<input required value={f.name} onChange={set("name")} maxLength={120} placeholder="Acme Corp" /></label>
        <label>소유자 uid<input required value={f.owner_uid} onChange={set("owner_uid")} className="adm-mono" /></label>
        <label>소유자 이메일<input required type="email" value={f.owner_email} onChange={set("owner_email")} /></label>
        <label>플랜<input required value={f.plan} onChange={set("plan")} /></label>
        <label>셀
          <select value={f.cell} onChange={set("cell")}>
            <option value="">자동 배치 (가장 한가한 공용 셀)</option>
            {cells.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} · {STATUS_KO[c.status] ?? c.status}{c.dedicated ? " · 전용" : ""} · {c.tenants}/{c.max_tenants}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrBox msg={a.err} />
      <div className="adm-row"><button className="adm-btn" disabled={a.busy}>{a.busy ? "만드는 중…" : "만들기"}</button></div>
    </form>
  );
}

function TenantDetail({ me, t, cells, onChanged, onClose }:
  { me: Me; t: Tenant; cells: Cell[]; onChanged: () => void; onClose: () => void }) {
  return (
    <div className="adm-detail-panel">
      <div className="adm-title-row">
        <h2>{t.name} <span className="adm-mono adm-muted">{t.slug}</span></h2>
        <button className="adm-btn ghost" onClick={onClose}>닫기</button>
      </div>
      <div className="adm-facts">
        <span>셀 <b className="adm-mono">{t.cell_id}</b></span>
        <span>상태 <Badge s={t.status} /></span>
        <span>플랜 <b>{t.plan}</b></span>
        <span>스키마 <b className="adm-mono">v{t.schema_version}</b></span>
        <span>생성 <b>{fmtTime(t.created_at)}</b></span>
        <span className="adm-mono adm-muted">{t.id}</span>
      </div>
      <TenantMetrics slug={t.slug} />
      <div className="adm-grid2">
        <SettingsForm me={me} t={t} onChanged={onChanged} />
        <StatusControl me={me} t={t} onChanged={onChanged} />
      </div>
      <MoveControl me={me} t={t} cells={cells} onChanged={onChanged} />
      <ContentViewer me={me} slug={t.slug} />
      <Section title="이 테넌트의 감사 로그">
        <AuditTable slug={t.slug} limit={50} />
      </Section>
    </div>
  );
}

/** 최근 60분을 분 단위 칸으로 깐다. 서버는 요청이 있던 분만 주므로 빈 분을
 *  0으로 채워야 선이 시간축에 맞는다(빠진 분을 건너뛰면 급증처럼 보인다). */
function minuteGrid(m: Metrics, minutes: number) {
  const now = Math.floor(Date.now() / 60000);
  const keys = Array.from({ length: minutes }, (_, i) => now - minutes + 1 + i);
  const req = new Map<number, number>();
  const p95 = new Map<number, number | null>();
  for (const [route, pts] of Object.entries(m.series)) {
    for (const p of pts) {
      const k = Math.floor(new Date(p.minute).getTime() / 60000);
      req.set(k, (req.get(k) ?? 0) + p.requests);
      if (route === "post") p95.set(k, msNum(p.p95));
    }
  }
  return { keys, req: keys.map((k) => req.get(k) ?? 0), p95: keys.map((k) => p95.get(k) ?? null) };
}

function TenantMetrics({ slug }: { slug: string }) {
  const m = useLoad<Metrics>(`/tenants/${encodeURIComponent(slug)}/metrics?minutes=60`, 30000);
  const g = useMemo(() => (m.data ? minuteGrid(m.data, 60) : null), [m.data]);
  return (
    <Section title="최근 60분">
      <ErrBox msg={m.err} />
      {g && (
        <div className="adm-grid2">
          <Spark title="분당 요청 (전체 경로)" vals={g.req} fmt={(v) => fmtN(v)} cls="adm-s-blue" />
          <Spark title="게시 p95" vals={g.p95} fmt={(v) => (v >= 3000 ? ">2500ms" : `≤${v}ms`)} cls="adm-s-amber"
            ref500 />
        </div>
      )}
      {m.data && <RouteTable summary={m.data.summary} />}
    </Section>
  );
}

/** 인라인 SVG 스파크라인. null은 "그 분에 데이터 없음"이라 선을 끊는다 —
 *  0으로 그리면 지연이 0ms인 것처럼 읽힌다. */
function Spark({ title, vals, fmt, cls, ref500 }:
  { title: string; vals: (number | null)[]; fmt: (v: number) => string; cls: string; ref500?: boolean }) {
  const W = 300, H = 64, P = 4;
  const nums = vals.filter((v): v is number => v !== null);
  const max = Math.max(ref500 ? 600 : 1, ...nums);
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(1, vals.length - 1);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const segs: string[] = [];
  let cur: string[] = [];
  vals.forEach((v, i) => {
    if (v === null) { if (cur.length) segs.push(cur.join(" ")); cur = []; return; }
    cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  });
  if (cur.length) segs.push(cur.join(" "));
  const last = [...vals].reverse().find((v) => v !== null);
  return (
    <div className="adm-spark">
      <div className="adm-spark-h">
        <span>{title}</span>
        <span className="adm-mono">{last !== undefined && last !== null ? `최근 ${fmt(last)}` : "데이터 없음"} · 최대 {nums.length ? fmt(Math.max(...nums)) : "—"}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={cls} role="img" aria-label={title}>
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} className="adm-s-base" />
        {ref500 && <line x1={P} x2={W - P} y1={y(500)} y2={y(500)} className="adm-s-ref" />}
        {segs.map((s, i) => (s.includes(" ")
          ? <polyline key={i} points={s} className="adm-s-line" />
          : <circle key={i} cx={s.split(",")[0]} cy={s.split(",")[1]} r={1.8} className="adm-s-dot" />))}
      </svg>
      <div className="adm-spark-f"><span>60분 전</span>{ref500 && <span>점선: 알림 기준 500ms</span>}<span>지금</span></div>
    </div>
  );
}

function SettingsForm({ me, t, onChanged }: { me: Me; t: Tenant; onChanged: () => void }) {
  const [hot, setHot] = useState(String(t.retention_hot_months));
  const [purge, setPurge] = useState(t.archive_purge_months === null ? "" : String(t.archive_purge_months));
  const [hold, setHold] = useState(t.legal_hold);
  const [reason, setReason] = useState("");
  const a = useAction();
  const ro = !can(me, "operator");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    // 바뀐 칸만 보낸다 — 서버는 null을 "건드리지 않음"으로 읽으므로, 전부 보내면
    // 감사 로그에 바꾸지 않은 값까지 변경처럼 남는다.
    const body: Record<string, unknown> = { reason: reason.trim() };
    if (Number(hot) !== t.retention_hot_months) body.retention_hot_months = Number(hot);
    if (purge !== "" && Number(purge) !== t.archive_purge_months) body.archive_purge_months = Number(purge);
    // 비우면 '무기한 보존'으로 되돌린다 — 서버는 null을 "변경 없음"으로 읽으므로 별도 표시로 보낸다
    if (purge === "" && t.archive_purge_months !== null) body.clear_archive_purge = true;
    if (hold !== t.legal_hold) body.legal_hold = hold;
    if (Object.keys(body).length === 1) { a.setOk(null); a.setErr("바뀐 설정이 없습니다."); return; }
    if (hold === false && t.legal_hold &&
        !confirm(`${t.name}의 법적 보존을 해제합니다. 해제 후에는 영구 삭제 기간이 지난 아카이브가 삭제 대상이 됩니다. 계속할까요?`)) return;
    a.run(async () => {
      await api(`/tenants/${encodeURIComponent(t.slug)}`, { method: "PATCH", body });
      onChanged();
      return "저장했습니다.";
    });
  };

  return (
    <form className="adm-card adm-form" onSubmit={submit}>
      <h3>보존 설정</h3>
      <div className="adm-fgrid">
        <label>핫 보존 (개월, 1–120)
          <input type="number" min={1} max={120} required value={hot} disabled={ro} onChange={(e) => setHot(e.target.value)} /></label>
        <label>아카이브 영구 삭제 (개월, 12 이상)
          <input type="number" min={12} value={purge} disabled={ro} placeholder="미설정 (삭제 안 함)"
            onChange={(e) => setPurge(e.target.value)} /></label>
      </div>
      <label className="adm-check">
        <input type="checkbox" checked={hold} disabled={ro} onChange={(e) => setHold(e.target.checked)} />
        법적 보존 (켜면 아카이브 영구 삭제를 멈춥니다 — 아카이빙 자체는 무손실이라 계속됩니다)
      </label>
      {t.archive_purge_months !== null && (
        <p className="adm-hint">칸을 비우고 저장하면 영구 삭제 설정이 지워져 무기한 보존으로 돌아갑니다.</p>
      )}
      <label>변경 사유 (감사 로그에 남음)
        <input value={reason} disabled={ro} onChange={(e) => setReason(e.target.value)} placeholder="예: 고객 요청 #1234" /></label>
      <ErrBox msg={a.err} /><OkBox msg={a.ok} />
      {ro ? <Locked need="operator" /> :
        <div className="adm-row"><button className="adm-btn" disabled={a.busy}>{a.busy ? "저장 중…" : "저장"}</button></div>}
    </form>
  );
}

function StatusControl({ me, t, onChanged }: { me: Me; t: Tenant; onChanged: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAction();
  const suspend = t.status === "active";
  const canToggle = t.status === "active" || t.status === "suspended";
  const ro = !can(me, "operator");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = reason.trim();
    if (!r) { a.setErr("사유를 입력해야 합니다."); return; }
    const msg = suspend
      ? `${t.name}(${t.slug})을(를) 정지합니다. 이 기업의 모든 사용자가 즉시 채팅을 읽고 쓸 수 없게 됩니다. 계속할까요?`
      : `${t.name}(${t.slug})의 정지를 해제합니다. 이 기업 사용자들이 즉시 다시 접속할 수 있게 됩니다. 계속할까요?`;
    if (!confirm(msg)) return;
    a.run(async () => {
      await api(`/tenants/${encodeURIComponent(t.slug)}`, { method: "PATCH",
        body: { status: suspend ? "suspended" : "active", reason: r } });
      setReason("");
      onChanged();
      return suspend ? "정지했습니다." : "정지를 해제했습니다.";
    });
  };

  return (
    <form className="adm-card adm-form" onSubmit={submit}>
      <h3>{suspend ? "테넌트 정지" : "정지 해제"}</h3>
      <p className="adm-muted">현재 상태: <Badge s={t.status} /></p>
      {!canToggle ? <p className="adm-muted">이 상태({STATUS_KO[t.status] ?? t.status})에서는 정지·해제를 할 수 없습니다.</p> : (
        <>
          <label>사유 (필수, 감사 로그에 남음)
            <input required value={reason} disabled={ro} onChange={(e) => setReason(e.target.value)}
              placeholder={suspend ? "예: 결제 연체 3개월" : "예: 결제 확인"} /></label>
          <ErrBox msg={a.err} /><OkBox msg={a.ok} />
          {ro ? <Locked need="operator" /> : (
            <div className="adm-row">
              <button className={`adm-btn${suspend ? " danger" : ""}`} disabled={a.busy || !reason.trim()}>
                {a.busy ? "처리 중…" : suspend ? "정지" : "정지 해제"}
              </button>
            </div>
          )}
        </>
      )}
    </form>
  );
}

function MoveControl({ me, t, cells, onChanged }: { me: Me; t: Tenant; cells: Cell[]; onChanged: () => void }) {
  const targets = cells.filter((c) => c.id !== t.cell_id);
  const [to, setTo] = useState("");
  const [mid, setMid] = useState<number | null>(null);
  const a = useAction();
  const su = can(me, "superadmin");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!to) return;
    if (!confirm(`${t.name}을(를) 셀 ${t.cell_id} → ${to}(으)로 옮깁니다. 배경 작업으로 복사한 뒤, 펜스 구간 동안 이 기업의 쓰기가 잠시 멈추고 그동안 사용자는 "옮기는 중" 오류를 봅니다. 계속할까요?`)) return;
    a.run(async () => {
      // 대기열에 넣고 바로 돌아온다(202) — 진행은 아래 MoveProgress가 폴링한다.
      // 창을 닫아도 작업은 계속된다(작업자가 처리하고, 작업자가 죽으면 자동으로 되돌린다).
      const r = await api<{ move_id: number }>(`/tenants/${encodeURIComponent(t.slug)}/move`,
        { body: { to_cell: to } });
      setMid(r.move_id);
      return `이동 #${r.move_id}을(를) 대기열에 넣었습니다.`;
    });
  };

  return (
    <Section title="셀 이동">
      {!su ? <Locked need="superadmin" /> : (
        <form className="adm-row adm-wrap" onSubmit={submit}>
          <span className="adm-muted">현재 <b className="adm-mono">{t.cell_id}</b> →</span>
          <select value={to} onChange={(e) => setTo(e.target.value)} disabled={a.busy || mid !== null}>
            <option value="">대상 셀 선택</option>
            {targets.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} · {STATUS_KO[c.status] ?? c.status}{c.dedicated ? " · 전용" : ""} · {c.tenants}/{c.max_tenants}{c.reachable ? "" : " · 연결 불가"}
              </option>
            ))}
          </select>
          <button className="adm-btn danger" disabled={!to || a.busy || mid !== null}>이동</button>
        </form>
      )}
      <ErrBox msg={a.err} /><OkBox msg={a.ok} />
      {mid !== null && <MoveProgress me={me} id={mid} onDone={() => { onChanged(); }} />}
    </Section>
  );
}

/** 이동 하나의 진행 — 끝날 때까지 2초마다 다시 읽는다. */
function MoveProgress({ me, id, onDone }: { me: Me; id: number; onDone: () => void }) {
  const [m, setM] = useState<Move | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const a = useAction();
  // onDone은 부모가 매 렌더마다 새로 만든다 — 의존성에 넣으면 완료 알림 → 부모 갱신 →
  // 효과 재실행 → 다시 알림의 고리가 생긴다. 최신 함수만 ref로 들고 있는다.
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    let alive = true, notified = false;
    const pull = async () => {
      try {
        const r = await api<Move>(`/moves/${id}`);
        if (!alive) return;
        setM(r);
        if (!MOVE_ACTIVE.has(r.state) && !notified) { notified = true; done.current(); }
      } catch (e) { if (alive) setErr((e as Error).message); }
    };
    void pull();
    const t = setInterval(() => { if (!notified) void pull(); }, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);
  if (!m) return <ErrBox msg={err} />;
  return (
    <div className="adm-card">
      <div className="adm-row adm-wrap adm-between">
        <span><b>#{m.id}</b> <span className="adm-mono">{m.from_cell} → {m.to_cell}</span></span>
        <Badge s={m.state} label={MOVE_KO[m.state] ?? m.state} />
      </div>
      <MoveLive m={m} />
      {m.error && <div className="adm-err adm-mono">{m.error}</div>}
      {m.report && m.state === "switched" && <MoveReportView r={m.report} />}
      {(m.state === "queued" || m.state === "copying") && can(me, "superadmin") && !m.cancel_requested && (
        <div className="adm-row"><button className="adm-btn" disabled={a.busy} onClick={() => {
          if (!confirm("이동을 취소합니다. 복사 중이면 대상 셀의 복사본을 지우고 원래대로 둡니다. 계속할까요?")) return;
          a.run(async () => { await api(`/moves/${m.id}/cancel`, { method: "POST" }); return "취소를 요청했습니다."; });
        }}>취소</button></div>)}
      <ErrBox msg={a.err} /><OkBox msg={a.ok} />
    </div>
  );
}

/** 진행 중인 이동의 단계·작업자·하트비트. 하트비트가 오래 멈추면 작업자를 잃은 것이다. */
function MoveLive({ m }: { m: Move }) {
  if (!MOVE_ACTIVE.has(m.state)) return null;
  const ago = m.heartbeat_at ? Math.round((Date.now() - Date.parse(m.heartbeat_at)) / 1000) : null;
  return (
    <div className="adm-facts">
      <span>단계 <b className="adm-mono">{m.phase ?? m.state}</b></span>
      {m.report?.bytes !== undefined && <span>복사 <b>{(m.report.bytes / 1e6).toFixed(1)} MB</b></span>}
      {m.worker && <span>작업자 <b className="adm-mono">{m.worker}</b></span>}
      {ago !== null && <span className={ago > 60 ? "adm-warn" : ""}>하트비트 <b>{ago}초 전</b>
        {ago > 60 && " — 응답 없음이 계속되면 자동으로 되돌립니다"}</span>}
      {m.cancel_requested && <span className="adm-warn">취소 요청됨</span>}
    </div>
  );
}

function MoveReportView({ r }: { r: MoveReport }) {
  return (
    <div className="adm-report">
      <div className="adm-facts">
        {r.from && <span>경로 <b className="adm-mono">{r.from} → {r.to}</b></span>}
        <span>복사 <b className="adm-mono">{r.copy_sec ?? "—"}초</b></span>
        <span>쓰기 중단 <b className="adm-mono">{r.write_pause_sec ?? "—"}초</b></span>
        <span>델타 메시지 <b className="adm-mono">{r.delta_messages ?? (r.delta ? r.delta : "—")}</b></span>
        {r.copy_bytes !== undefined && <span>복사 행 <b className="adm-mono">{fmtN(r.copy_bytes)}</b></span>}
      </div>
      {r.rows && (
        <div className="adm-chips">
          {Object.entries(r.rows).map(([k, v]) => <span key={k} className="adm-chip"><span className="adm-mono">{k}</span> {fmtN(v)}</span>)}
        </div>
      )}
    </div>
  );
}

// ── 본문 열람 ──

/** 사유를 먼저 받고, 확정된 사유로만 부른다. 서버는 사유를 필수로 받지 않지만
 *  (빈 문자열도 기록됨) 고객이 access_log에서 "왜 봤는지"를 읽을 수 있어야
 *  '전체 열람' 정책이 신뢰를 잃지 않는다 — 그래서 화면이 강제한다.
 *  사유를 바꾸려면 열람을 닫고 다시 시작한다: 열람 도중 사유가 바뀌면 같은
 *  세션의 기록이 서로 다른 사유로 흩어진다. */
function ContentViewer({ me, slug }: { me: Me; slug: string }) {
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState<string | null>(null);
  const [chans, setChans] = useState<AdmChannel[] | null>(null);
  const [cid, setCid] = useState<number | null>(null);
  const a = useAction();

  if (!can(me, "operator"))
    return <Section title="본문 열람"><Banner /><Locked need="operator" /></Section>;

  const start = (e: FormEvent) => {
    e.preventDefault();
    const r = draft.trim();
    if (!r) return;
    a.run(async () => {
      const q = new URLSearchParams({ reason: r });
      const d = await api<{ channels: AdmChannel[] }>(`/tenants/${encodeURIComponent(slug)}/channels?${q}`);
      setReason(r); setChans(d.channels); setCid(null);
    });
  };
  const stop = () => { setReason(null); setChans(null); setCid(null); a.setErr(null); };

  return (
    <Section title="본문 열람" right={reason ? <button className="adm-btn ghost" onClick={stop}>열람 종료</button> : undefined}>
      <Banner />
      {!reason ? (
        <form className="adm-row adm-wrap" onSubmit={start}>
          <input className="adm-grow" value={draft} onChange={(e) => setDraft(e.target.value)} required
            placeholder="열람 사유 (필수) — 예: 신고 #88 조사, 고객 요청 복구 확인" />
          <button className="adm-btn" disabled={!draft.trim() || a.busy}>{a.busy ? "여는 중…" : "열람 시작"}</button>
        </form>
      ) : (
        <p className="adm-muted">사유: <b>{reason}</b></p>
      )}
      <ErrBox msg={a.err} />
      {reason && chans && (
        <div className="adm-viewer">
          <ul className="adm-chlist">
            {chans.length === 0 && <li className="adm-muted">채널이 없습니다.</li>}
            {chans.map((c) => (
              <li key={c.id}>
                <button className={`adm-ch${cid === c.id ? " on" : ""}`} onClick={() => setCid(c.id)}>
                  <span className="adm-ch-n">#{c.name}</span>
                  <span className="adm-ch-m">
                    <span className={`adm-badge adm-${c.visibility === "private" ? "amber" : "blue"}`}>
                      {c.visibility === "private" ? "비공개" : c.visibility === "public" ? "공개" : c.visibility}</span>
                    {c.kind !== "channel" && <span className="adm-badge adm-blue">{c.kind}</span>}
                    {c.archived_at && <span className="adm-badge adm-amber">보관됨</span>}
                    <span className="adm-muted adm-mono">{c.members}명 · {c.last_seq}건</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {cid !== null
            ? <ChannelMessages key={cid} slug={slug} cid={cid} reason={reason} />
            : <Empty>왼쪽에서 채널을 고르세요.</Empty>}
        </div>
      )}
    </Section>
  );
}

function Banner() {
  return <div className="adm-banner" role="note">이 열람은 플랫폼 감사 로그와 해당 기업의 열람 기록에 남습니다</div>;
}

const PAGE = 100;

function ChannelMessages({ slug, cid, reason }: { slug: string; cid: number; reason: string }) {
  const [msgs, setMsgs] = useState<AdmMsg[]>([]);
  const [ch, setCh] = useState<{ name: string; visibility: string } | null>(null);
  const [more, setMore] = useState(false);
  const a = useAction();
  const ex = useAction();
  const base = `/tenants/${encodeURIComponent(slug)}/channels/${cid}`;

  // 열람 1회 = 감사 1행이다. 자동 새로고침을 넣으면 보지도 않는 사이 기록이
  // 쌓이므로, 처음 한 번과 사용자가 누른 '이전 메시지'만 부른다.
  const load = useCallback((before?: number) => a.run(async () => {
    const q = new URLSearchParams({ reason, limit: String(PAGE) });
    if (before !== undefined) q.set("before", String(before));
    const d = await api<{ channel: { name: string; visibility: string }; messages: AdmMsg[] }>(`${base}/messages?${q}`);
    setCh(d.channel);
    setMsgs((prev) => (before === undefined ? d.messages : [...d.messages, ...prev]));
    setMore(d.messages.length === PAGE && (d.messages[0]?.seq ?? 1) > 1);
  }), [base, reason]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);

  /** 내보내기는 <a href>로 못 건다 — 인증이 헤더(Bearer / X-Dev-User)라 링크로
   *  열면 401이다. 받아서 Blob으로 저장시킨다. */
  const doExport = () => ex.run(async () => {
    const q = new URLSearchParams({ reason });
    const r = await fetch(`/api/platform/v1${base}/export?${q}`, { headers: await authHeaders(), cache: "no-store" });
    if (!r.ok) {
      let j: any = null;
      try { j = await r.json(); } catch { /* 본문 없음 */ }
      throw new Error(j ? errorText(r.status, j) : `내보내기 실패 (HTTP ${r.status})`);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const el = document.createElement("a");
    el.href = url;
    el.download = `${slug}-${ch?.name ?? cid}.ndjson`;
    document.body.appendChild(el); el.click(); el.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return `내보냈습니다 (${fmtN(blob.size)} 바이트).`;
  });

  return (
    <div className="adm-msgs">
      <div className="adm-row adm-wrap adm-between">
        <b>#{ch?.name ?? cid}{ch?.visibility === "private" && <span className="adm-badge adm-amber">비공개</span>}</b>
        <button className="adm-link" onClick={doExport} disabled={ex.busy}>
          {ex.busy ? "내보내는 중…" : "전체 내보내기 (NDJSON, 아카이브 포함)"}</button>
      </div>
      <ErrBox msg={ex.err} /><OkBox msg={ex.ok} />
      {more && <button className="adm-btn ghost adm-full" disabled={a.busy} onClick={() => load(msgs[0]?.seq)}>
        {a.busy ? "불러오는 중…" : "이전 메시지"}</button>}
      <ErrBox msg={a.err} />
      {!a.busy && !msgs.length && !a.err && <Empty>메시지가 없습니다.</Empty>}
      <ul className="adm-mlist">
        {msgs.map((m) => (
          <li key={m.seq} className={`adm-m${m.parent_seq ? " reply" : ""}${m.deleted ? " del" : ""}`}>
            <div className="adm-m-h">
              <b>{m.author}</b>
              <span className="adm-mono adm-muted">#{m.seq}{m.parent_seq ? ` ↳ #${m.parent_seq}` : ""} · {fmtTime(m.created_at)}
                {m.edited_at ? " · 수정됨" : ""}{m.reply_count ? ` · 답글 ${m.reply_count}` : ""}{m.kind !== "text" ? ` · ${m.kind}` : ""}</span>
            </div>
            <div className="adm-m-b">{m.deleted && <span className="adm-badge adm-coral">삭제됨</span>}{m.body}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ═══════════ 3. 셀 ═══════════

const CELL_STATUS: { s: string; why: string }[] = [
  { s: "open", why: "새 테넌트 자동 배치와 이동 대상이 됩니다" },
  { s: "full", why: "자동 배치에서 빠집니다(명시 지정·이동은 가능)" },
  { s: "draining", why: "새 배치와 이동 대상에서 모두 빠집니다 — 기존 테넌트를 비울 때" },
  { s: "down", why: "새 배치와 이동 대상에서 모두 빠집니다 — 장애 표시" },
];

function CellsView({ me }: { me: Me }) {
  const cells = useLoad<{ cells: Cell[] }>("/cells", 30000);
  return (
    <>
      <div className="adm-title-row">
        <h1>셀</h1>
        <span className="adm-muted">30초마다 실측{cells.loading ? " · 재는 중…" : ""}</span>
      </div>
      <ErrBox msg={cells.err} />
      <div className="adm-cards">
        {cells.data?.cells.map((c) => <CellCard key={c.id} me={me} c={c} onChanged={cells.reload} />)}
      </div>
      {cells.data && !cells.data.cells.length && <Empty>등록된 셀이 없습니다.</Empty>}
    </>
  );
}

function CellCard({ me, c, onChanged }: { me: Me; c: Cell; onChanged: () => void }) {
  const [next, setNext] = useState(c.status);
  const a = useAction();
  useEffect(() => setNext(c.status), [c.status]);
  const connRatio = c.conns !== undefined && c.max_conns ? c.conns / c.max_conns : null;

  const apply = () => {
    const why = CELL_STATUS.find((x) => x.s === next)?.why ?? "";
    if (!confirm(`셀 ${c.id}의 상태를 ${STATUS_KO[c.status] ?? c.status} → ${STATUS_KO[next] ?? next}(으)로 바꿉니다. ${why}. 이미 이 셀에 있는 테넌트 ${c.tenants}곳의 트래픽은 바뀌지 않습니다. 계속할까요?`)) return;
    a.run(async () => {
      await api(`/cells/${encodeURIComponent(c.id)}`, { method: "PATCH", body: { status: next } });
      onChanged();
      return "변경했습니다.";
    });
  };

  return (
    <div className="adm-card adm-cell">
      <div className="adm-row adm-between">
        <h3 className="adm-mono">셀 {c.id}</h3>
        <span><Badge s={c.status} />{c.dedicated && <Badge s="info" label="전용" />}</span>
      </div>
      <div className="adm-muted adm-mono">{c.region}</div>
      <div className={`adm-reach ${c.reachable ? "adm-c-green" : "adm-c-coral"}`}>
        {c.reachable ? `● 연결됨 · ${c.ping_ms ?? "—"}ms` : "● 연결 불가"}
      </div>
      {c.error && <div className="adm-err adm-mono">{c.error}</div>}
      <dl className="adm-dl">
        <dt>DB 크기</dt><dd className="adm-mono">{fmtMB(c.size)}</dd>
        <dt>연결</dt><dd className={`adm-mono${connRatio !== null && connRatio > 0.8 ? " adm-c-coral" : ""}`}>
          {c.conns !== undefined ? `${c.conns} / ${c.max_conns}` : "—"}</dd>
        <dt>스키마</dt><dd className="adm-mono">{fmtN(c.schemas)}</dd>
        <dt>테넌트</dt><dd className={`adm-mono${c.tenants >= c.max_tenants ? " adm-c-amber" : ""}`}>{c.tenants} / {c.max_tenants}</dd>
      </dl>
      {can(me, "superadmin") ? (
        <div className="adm-row">
          <select value={next} onChange={(e) => setNext(e.target.value)} disabled={a.busy}>
            {CELL_STATUS.map((x) => <option key={x.s} value={x.s}>{STATUS_KO[x.s]} ({x.s})</option>)}
          </select>
          <button className="adm-btn" disabled={a.busy || next === c.status} onClick={apply}>
            {a.busy ? "변경 중…" : "상태 변경"}</button>
        </div>
      ) : <Locked need="superadmin" />}
      <ErrBox msg={a.err} /><OkBox msg={a.ok} />
    </div>
  );
}

// ═══════════ 4. 알림 ═══════════

function AlertsView() {
  const [all, setAll] = useState(false);
  const al = useLoad<{ alerts: Alert[] }>(`/alerts?open_only=${all ? "false" : "true"}`, 30000);
  const rows = al.data?.alerts ?? [];
  const open = rows.filter((a) => !a.closed_at);
  const closed = rows.filter((a) => a.closed_at);
  return (
    <>
      <div className="adm-title-row">
        <h1>알림</h1>
        <label className="adm-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />닫힌 알림도 보기</label>
      </div>
      <ErrBox msg={al.err} />
      {al.data && (
        <>
          <Section title={`열림 (${open.length})`}><AlertList alerts={open} /></Section>
          {all && <Section title={`닫힘 (${closed.length})`}><AlertList alerts={closed} /></Section>}
          {all && rows.length >= 200 && <p className="adm-hint">최근 200건까지만 표시합니다.</p>}
        </>
      )}
    </>
  );
}

// ═══════════ 5. 이동 기록 ═══════════

function MovesView({ me }: { me: Me }) {
  const mv = useLoad<{ moves: Move[] }>("/moves", 15000);
  return (
    <>
      <div className="adm-title-row"><h1>이동 기록</h1><span className="adm-muted">최근 100건</span></div>
      <ErrBox msg={mv.err} />
      {mv.data && (mv.data.moves.length ? (
        <div className="adm-stack">
          {mv.data.moves.map((m) => <MoveRow key={m.id} me={me} m={m} onChanged={mv.reload} />)}
        </div>
      ) : <Empty>이동 기록이 없습니다.</Empty>)}
    </>
  );
}

function MoveRow({ me, m, onChanged }: { me: Me; m: Move; onChanged: () => void }) {
  const a = useAction();
  const finalize = () => {
    if (!confirm(`이동 #${m.id}을(를) 정리합니다. 원본 셀 ${m.from_cell}에 남아 있는 ${m.slug}의 이전 스키마를 삭제하며 되돌릴 수 없습니다(이후 롤백 불가). 계속할까요?`)) return;
    a.run(async () => {
      const r = await api<{ move_id: number; dropped: string }>(`/moves/${m.id}/finalize`, { method: "POST" });
      onChanged();
      return `정리 완료 — ${r.dropped} 삭제`;
    });
  };
  return (
    <div className="adm-card">
      <div className="adm-row adm-wrap adm-between">
        <span><b>#{m.id}</b> <span className="adm-strong">{m.slug}</span> <span className="adm-mono">{m.from_cell} → {m.to_cell}</span></span>
        <Badge s={m.state} label={MOVE_KO[m.state] ?? m.state} />
      </div>
      <div className="adm-facts">
        <span>실행 <b>{m.actor}</b></span>
        <span>시작 <b>{fmtTime(m.started_at)}</b></span>
        {m.fenced_at && <span>펜스 <b>{fmtTime(m.fenced_at)}</b></span>}
        {m.switched_at && <span>전환 <b>{fmtTime(m.switched_at)}</b></span>}
        {m.finished_at && <span>종료 <b>{fmtTime(m.finished_at)}</b></span>}
      </div>
      <MoveLive m={m} />
      {m.error && <div className="adm-err adm-mono">{m.error}</div>}
      {m.report && m.state === "switched" && <MoveReportView r={m.report} />}
      {(m.state === "queued" || m.state === "copying") && can(me, "superadmin") && !m.cancel_requested && (
        <div className="adm-row"><button className="adm-btn" disabled={a.busy} onClick={() => {
          if (!confirm(`이동 #${m.id}을(를) 취소합니다. 계속할까요?`)) return;
          a.run(async () => { await api(`/moves/${m.id}/cancel`, { method: "POST" }); onChanged(); return "취소를 요청했습니다."; });
        }}>취소</button></div>)}
      {m.state === "switched" && (can(me, "superadmin")
        ? <div className="adm-row"><button className="adm-btn danger" disabled={a.busy} onClick={finalize}>
            {a.busy ? "정리 중…" : "정리 (원본 스키마 삭제)"}</button></div>
        : <Locked need="superadmin" />)}
      <ErrBox msg={a.err} /><OkBox msg={a.ok} />
    </div>
  );
}

// ═══════════ 6. 운영 채널 ═══════════

function OpsView({ me }: { me: Me }) {
  const [name, setName] = useState("ops");
  const chans = useLoad<{ channels: OpsChannel[] }>(can(me, "operator") ? "/ops/channels" : null);
  if (!can(me, "operator")) return (<><div className="adm-title-row"><h1>운영 채널</h1></div><Locked need="operator" /></>);
  // 서버가 ops·incidents를 항상 만들어 두지만, 목록이 아직 없을 때도 탭은 보이게 한다.
  const names = chans.data?.channels.map((c) => c.name) ?? ["incidents", "ops"];
  return (
    <>
      <div className="adm-title-row"><h1>운영 채널</h1><span className="adm-muted">5초마다 갱신</span></div>
      <ErrBox msg={chans.err} />
      <div className="adm-seg">
        {names.map((n) => (
          <button key={n} className={`adm-seg-b${n === name ? " on" : ""}`} onClick={() => setName(n)}>#{n}</button>
        ))}
      </div>
      <OpsChat key={name} name={name} />
    </>
  );
}

function OpsChat({ name }: { name: string }) {
  const [msgs, setMsgs] = useState<OpsMsg[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const after = useRef(0);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const send = useAction();
  const path = `/ops/channels/${encodeURIComponent(name)}/messages`;

  /** seq로 병합한다 — 내가 보낸 글은 POST 응답으로 먼저 붙고, 다음 폴링에도
   *  다시 오므로 seq가 겹친다. */
  const merge = useCallback((got: OpsMsg[]) => {
    if (!got.length) return;
    after.current = Math.max(after.current, ...got.map((m) => m.seq));
    setMsgs((prev) => {
      const by = new Map(prev.map((m) => [m.seq, m]));
      for (const m of got) by.set(m.seq, m);
      return [...by.values()].sort((a, b) => a.seq - b.seq);
    });
  }, []);

  const pull = useCallback(async () => {
    try {
      // 서버는 한 번에 100건까지 준다 — 처음 열 때 밀린 글이 많으면 다 받을 때까지 이어 받는다.
      for (let i = 0; i < 20; i++) {
        const d = await api<{ messages: OpsMsg[] }>(`${path}?after=${after.current}`);
        merge(d.messages);
        if (d.messages.length < 100) break;
      }
      setErr(null);
    } catch (e) { setErr(msgOf(e)); }
  }, [path, merge]);

  useEffect(() => {
    pull();
    const id = setInterval(() => { if (!document.hidden) pull(); }, 5000);
    return () => clearInterval(id);
  }, [pull]);

  // 맨 아래를 보고 있을 때만 따라 내려간다 — 위로 올려 읽는 중에 새 글이 끌어내리면 놓친다.
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  const submit = () => {
    const body = text.trim();
    if (!body || send.busy) return;
    send.run(async () => {
      const m = await api<OpsMsg>(path, { body: { body } });
      setText("");
      stick.current = true;
      merge([m]);
    });
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 한글 조합 중 Enter는 글자 확정이다 — 그때 보내면 마지막 글자가 잘린다.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  return (
    <div className="adm-card adm-chat">
      <div className="adm-chat-list" ref={box}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
        {!msgs.length && <Empty>#{name}에 아직 글이 없습니다.</Empty>}
        {msgs.map((m) => (
          <div key={m.seq} className={`adm-m${m.kind === "system" ? " sys" : ""}`}>
            <div className="adm-m-h"><b>{m.author}</b><span className="adm-mono adm-muted">{fmtTime(m.created_at)}</span></div>
            <div className="adm-m-b">{m.body}</div>
          </div>
        ))}
      </div>
      <ErrBox msg={err} /><ErrBox msg={send.err} />
      <div className="adm-composer">
        <textarea value={text} maxLength={8000} rows={2} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          placeholder={`#${name}에 메시지 (Enter 보내기 · Shift+Enter 줄바꿈)`} />
        <button className="adm-btn" disabled={!text.trim() || send.busy} onClick={submit}>{send.busy ? "…" : "보내기"}</button>
      </div>
    </div>
  );
}

// ═══════════ 7. 공지 ═══════════

function AnnouncementsView({ me }: { me: Me }) {
  const list = useLoad<{ announcements: Announcement[] }>("/announcements", 30000);
  const [text, setText] = useState("");
  const post = useAction();
  const op = can(me, "operator");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    if (!confirm("모든 활성 테넌트의 #announcements에 게시됩니다. 게시 후에는 회수할 수 없습니다. 계속할까요?")) return;
    post.run(async () => {
      const r = await api<{ announcement_id: number; delivered: number; failed: string[] }>("/announcements", { body: { body } });
      setText("");
      list.reload();
      return `공지 #${r.announcement_id}: ${r.delivered}곳 전달${r.failed.length ? ` · 실패 ${r.failed.length}곳 (${r.failed.join(", ")})` : ""}`;
    });
  };

  return (
    <>
      <div className="adm-title-row"><h1>공지</h1></div>
      {op ? (
        <form className="adm-card adm-form" onSubmit={submit}>
          <h3>전체 공지 작성</h3>
          <textarea rows={4} maxLength={8000} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="모든 활성 테넌트의 #announcements 채널에 시스템 메시지로 올라갑니다." />
          <ErrBox msg={post.err} /><OkBox msg={post.ok} />
          <div className="adm-row adm-between">
            <span className="adm-muted adm-mono">{text.length} / 8000</span>
            <button className="adm-btn danger" disabled={!text.trim() || post.busy}>{post.busy ? "게시 중…" : "게시"}</button>
          </div>
        </form>
      ) : <Locked need="operator" />}
      <Section title="최근 공지">
        <ErrBox msg={list.err} />
        {list.data && (list.data.announcements.length ? (
          <div className="adm-stack">
            {list.data.announcements.map((a) => <AnnRow key={a.id} a={a} op={op} onChanged={list.reload} />)}
          </div>
        ) : <Empty>공지가 없습니다.</Empty>)}
      </Section>
    </>
  );
}

function AnnRow({ a, op, onChanged }: { a: Announcement; op: boolean; onChanged: () => void }) {
  const r = useAction();
  const pending = a.targets - a.delivered;
  const retry = () => r.run(async () => {
    // 재시도는 미전달 테넌트에만 간다(서버의 멱등 키가 중복 게시를 막음) — 확인창은 생략한다.
    const d = await api<{ delivered: number; failed: string[] }>(`/announcements/${a.id}/retry`, { method: "POST" });
    onChanged();
    return `재시도: ${d.delivered}곳 전달${d.failed.length ? ` · 실패 ${d.failed.join(", ")}` : ""}`;
  });
  return (
    <div className="adm-card">
      <div className="adm-row adm-wrap adm-between">
        <span className="adm-mono adm-muted">#{a.id} · {a.author} · {fmtTime(a.created_at)}</span>
        <span className="adm-row">
          <Badge s={pending ? "warning" : "active"} label={`전달 ${a.delivered}/${a.targets}`} />
          {a.failed > 0 && <Badge s="critical" label={`실패 ${a.failed}`} />}
        </span>
      </div>
      <div className="adm-m-b">{a.body}</div>
      {pending > 0 && op && (
        <div className="adm-row"><button className="adm-btn ghost" disabled={r.busy} onClick={retry}>
          {r.busy ? "재시도 중…" : `미전달 ${pending}곳 재시도`}</button></div>
      )}
      <ErrBox msg={r.err} /><OkBox msg={r.ok} />
    </div>
  );
}

// ═══════════ 8. 감사 로그 ═══════════

function AuditView() {
  const tenants = useLoad<{ tenants: Tenant[] }>("/tenants");
  const [slug, setSlug] = useState("");
  const [limit, setLimit] = useState(200);
  return (
    <>
      <div className="adm-title-row"><h1>감사 로그</h1></div>
      <div className="adm-row adm-wrap">
        <select value={slug} onChange={(e) => setSlug(e.target.value)} aria-label="테넌트 필터">
          <option value="">전체 (플랫폼 작업 포함)</option>
          {tenants.data?.tenants.map((t) => <option key={t.slug} value={t.slug}>{t.name} ({t.slug})</option>)}
        </select>
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="표시 개수">
          {[50, 200, 500, 1000].map((n) => <option key={n} value={n}>최근 {n}건</option>)}
        </select>
      </div>
      <ErrBox msg={tenants.err} />
      <AuditTable slug={slug || null} limit={limit} tenants={tenants.data?.tenants} />
    </>
  );
}

function AuditTable({ slug, limit, tenants }: { slug: string | null; limit: number; tenants?: Tenant[] }) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (slug) q.set("slug", slug);
  const au = useLoad<{ rows: AuditRow[] }>(`/audit?${q}`);
  // 감사 행에는 slug가 없고 tenant_id(uuid)만 있다 — 읽을 수 있게 목록으로 바꿔 보여 준다.
  const bySlug = useMemo(() => new Map((tenants ?? []).map((t) => [t.id, t.slug])), [tenants]);
  if (au.err) return <ErrBox msg={au.err} />;
  if (!au.data) return <div className="adm-muted">불러오는 중…</div>;
  if (!au.data.rows.length) return <Empty>기록이 없습니다.</Empty>;
  return (
    <TableBox>
      <table className="adm-table">
        <thead><tr><th>시각</th><th>실행자</th><th>작업</th><th>대상</th><th>사유</th><th>세부</th></tr></thead>
        <tbody>
          {au.data.rows.map((r) => (
            <tr key={r.id}>
              <td className="adm-nowrap">{fmtTime(r.at)}</td>
              <td className="adm-mono">{r.actor}</td>
              <td><span className={`adm-badge adm-${r.action.startsWith("content.") ? "amber" : "blue"}`}>{r.action}</span></td>
              <td className="adm-mono">{r.target ?? "—"}{r.tenant_id && bySlug.get(r.tenant_id) && !r.target?.startsWith(bySlug.get(r.tenant_id)!)
                ? <span className="adm-muted"> ({bySlug.get(r.tenant_id)})</span> : null}</td>
              <td>{r.reason || <span className="adm-muted">—</span>}</td>
              <td className="adm-mono adm-detail">{compactJson(r.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}
