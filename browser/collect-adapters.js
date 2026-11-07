// 다음 단계 확장 지점 — 브라우저 밖의 업무(사내 플랫폼 MCP, 사내 루틴 에이전트)를 같은 스키마로 들여오는 어댑터.
// 이번 범위는 인터페이스와 검사뿐이다. 실제 어댑터(큐브·스테이션 MCP, dot·메리 루틴)는 아직 없다.
//
// 어댑터는 플랫폼 고유의 것(인증, 폴링/웹훅, 문서 모양)을 맡고, 수집기는 공통의 것(마스킹, 콘텐츠 해시,
// 중복 제거, first/revisit/echo, 증거 후보 판정, append-only 기록)을 맡는다. 어댑터는 해시를 직접 만들지 않는다.
//
// 가정(확인 전): 큐브 = MYSCube 사업관리, 스테이션 = ema-station, dot·메리 = 사내 루틴 에이전트.
// 인터페이스는 이 가정에 기대지 않는다 — platform 문자열과 doc_type만 바뀐다(docs/EVENTS.md '어댑터').

/**
 * 바깥에서 들어오는 기록 하나. 수집기가 이벤트 봉투(schema_version, event_id, recorded_at …)를 채운다.
 * @typedef {object} ExternalRecord
 * @property {string} kind            `<platform>.<동사>` — 예: 'myscube.doc_snapshot', 'routine.run_start', 'routine.output'
 * @property {'human'|'agent'} actor_kind  이 행동을 한 쪽(루틴 에이전트의 산출물이면 'agent')
 * @property {string} [actor]         사람 id 또는 'agent:<루틴 이름>'
 * @property {'external'|'human'|'agent'} [content_origin]  원문을 쓴 쪽. content가 있으면 필수
 * @property {string} platform        'myscube' | 'ema-station' | 'routine:dot' | 'routine:메리' …
 * @property {string} [doc_type]      플랫폼 문서 유형 — 'project', 'contract', 'meeting_note', 'routine_report' …
 * @property {string} [author]        원문 작성자(ζ)
 * @property {string} [approval_status]  플랫폼의 결재·승인 상태(ζ). 독립 라벨이 아니다
 * @property {string} [source_url]    원문 위치(사람이 열 수 있는 주소 또는 'myscube://project/123')
 * @property {string} [title]
 * @property {string} [observed_at]   플랫폼 쪽 시각(ISO) — 문서 수정 시각, 루틴 실행 시각
 * @property {string} [content]       원문 텍스트. 수집기가 마스킹·해시·중복 제거한다
 * @property {object} [span]          π — { kind, start, end, within? }
 * @property {string} [task_id]       루틴 실행 id 등(없으면 null)
 * @property {object} [context]       c — { routine, question, thread, source_ref, source_version }
 * @property {string[]} [derived_from]  에이전트 산출물이 근거로 쓴 원문의 'sha256:…' 해시
 * @property {boolean} [derived_from_unknown]  근거를 알 수 없는 에이전트 산출물
 * @property {boolean} [candidate]    증거 후보로 올릴 원문인가(처음 본 사람·외부 원문일 때만 실제로 켜진다)
 * @property {object} [data]          플랫폼별 덧붙임(작게)
 */

/**
 * @typedef {object} Adapter
 * @property {string} name
 * @property {string} platform
 * @property {(sink: {ingest: (r: ExternalRecord) => object|undefined, hashOf: (text: string) => string}) => (void|Promise<void>)} start
 * @property {() => void} stop
 */

const KIND = /^[a-z][a-z0-9_-]*(:[^\s.]+)?\.[a-z_]+$/;

// 들어오는 기록의 규칙 — 특히 순환 방지: 에이전트가 쓴 원문은 derived_from이나 derived_from_unknown이 반드시 있어야 한다
function validateRecord(r) {
  const errs = [];
  if (!r || typeof r !== 'object') return ['기록이 객체가 아님'];
  if (typeof r.kind !== 'string' || !KIND.test(r.kind)) errs.push('kind는 <platform>.<동사> 모양');
  if (r.kind && /^(web|agent|chat|approval|collector)\./.test(r.kind)) errs.push('브라우저 안쪽 종류(web./agent./chat./approval.)는 어댑터가 쓸 수 없음');
  if (!['human', 'agent'].includes(r.actor_kind)) errs.push('actor_kind는 human|agent');
  if (typeof r.platform !== 'string' || !r.platform) errs.push('platform 필수');
  if (r.content != null) {
    if (typeof r.content !== 'string') errs.push('content는 문자열');
    if (!['external', 'human', 'agent'].includes(r.content_origin)) errs.push('content가 있으면 content_origin 필수');
  }
  if (r.content_origin === 'agent' && !(Array.isArray(r.derived_from) && r.derived_from.length) && !r.derived_from_unknown) {
    errs.push('에이전트 산출물은 derived_from 또는 derived_from_unknown:true 필수(순환 방지)');
  }
  if (r.derived_from && !(Array.isArray(r.derived_from) && r.derived_from.every((h) => /^(sha256:)?[0-9a-f]{64}$/.test(h)))) {
    errs.push('derived_from은 sha256 해시 목록');
  }
  if (r.label_kind && r.label_kind !== 'approval_signal') errs.push('어댑터는 독립 라벨(D_t)을 만들 수 없음 — label_kind는 approval_signal만');
  return errs;
}

// 수집기에 어댑터용 입구를 만든다. 잘못된 기록은 쌓지 않고 이유를 돌려준다.
function createSink(collector) {
  return {
    hashOf: (text) => collector.hashOf(text),
    ingest(r) {
      const errs = validateRecord(r);
      if (errs.length) return { ok: false, errors: errs };
      const ev = collector.external(r);
      return ev ? { ok: true, event: ev } : { ok: false, errors: ['수집이 꺼져 있음'] };
    },
  };
}

module.exports = { validateRecord, createSink };
