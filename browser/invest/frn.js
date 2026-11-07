// FRN(EMA Station 기업 등록) 읽기 — 브라우저에 붙은 EMA MCP(ema-mcp.js)의 읽기 전용 도구만 쓴다.
// 도구 이름·입력 모양은 EMA 서버가 정하므로 도구 목록(inputSchema)을 보고 맞춘다. 쓰기 도구는 부르지 않는다.
const FRN_RE = /\b([A-Z]{3}-[A-Z]{2}-\d{2}-\d{3})\b/;

function createFrn({ ema = () => null }) {
  const tools = () => { const e = ema(); return e && e.snapshot().status === 'on' ? e.agentTools() : []; };
  const pick = (re) => tools().find((t) => re.test(t.name) && !/\[EMA Station · 실행 전 사용자 승인\]/.test(t.description));
  // 필수 인자 하나짜리 도구에 값 하나를 넣는다(이름이 frn/id/query… 무엇이든)
  const argFor = (t, value, prefer) => {
    const props = Object.keys(t.input_schema?.properties || {});
    const req = t.input_schema?.required || [];
    const key = props.find((p) => prefer.test(p)) || req[0] || props[0] || 'query';
    return { [key]: value };
  };
  async function call(t, input) {
    const r = await ema().call(t.name, input);
    if (r.isError) throw new Error(r.text.slice(0, 300) || 'EMA 오류');
    return r.text;
  }
  return {
    FRN_RE,
    status: () => { const e = ema(); return e ? e.snapshot().status : 'off'; },
    async get(frn) {
      const t = pick(/registry_get|company_get|get_company|frn_get/);
      if (!t) throw new Error('EMA 기업 조회 도구가 없어요 — 마이페이지에서 EMA를 연결해 주세요');
      return call(t, argFor(t, frn, /^(frn|id|code|key)$/i));
    },
    async search(q) {
      const t = pick(/registry_search|company_search|search_compan/);
      if (!t) throw new Error('EMA 기업 검색 도구가 없어요');
      return call(t, argFor(t, q, /^(q|query|keyword|name|text)$/i));
    },
  };
}

module.exports = { createFrn, FRN_RE };
