// 관련 기록 꺼내 붙이기 — 새 작업을 시작할 때 업무 지도에서 비슷한 지난 작업·언급된 기업을 골라 짧게 붙인다.
// 모델을 부르지 않는다(이 컴퓨터에서 글자 겹침으로만). 지도가 아무리 커져도 붙는 양은 limit·maxChars로 고정된다.
// 붙는 것은 데이터다 — <memory> 경계 안에 넣고, 무엇을 꺼냈는지는 agent.instruction의 data.recall로 남겨 지도에 "꺼내 씀"으로 잇는다.

const norm = (s) => String(s || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
function grams(s) {
  const t = norm(s);
  const out = new Set();
  for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
  return out;
}
// 지금 지시의 두 글자 묶음 중 지난 지시에도 있는 비율 — 짧은 지시가 긴 지시에 묻히지 않게 지금 쪽을 분모로
function overlap(a, b) {
  if (!a.size) return 0;
  let n = 0;
  for (const g of a) if (b.has(g)) n++;
  return n / a.size;
}
const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n)}…` : s; };
const day = (at) => (at ? `${at.slice(5, 7)}/${at.slice(8, 10)}` : '');

function createRecall({ onto, signals = () => [], sameType = () => [], limit = 3, maxChars = 1200, minScore = 0.35 }) {
  // typeId: 업무 유형(worktypes.js)이 정해졌으면 같은 유형의 지난 작업을 글자 겹침과 상관없이 먼저
  function recall(text, { typeId = null, typeName = '' } = {}) {
    const { nodes, edges, outIdx } = onto.raw();
    const q = grams(text);
    const qn = norm(text);
    const sigs = signals();
    const picked = [];

    // 지시에 이름(또는 FRN)이 그대로 나온 EMA 등록 기업 — 다시 찾지 않아도 되게 FRN을 알려 준다
    for (const c of nodes.values()) {
      if (c.type !== 'company') continue;
      const name = norm(c.label);
      const frn = norm(c.props.frn);
      if ((name.length >= 2 && qn.includes(name)) || (frn && qn.includes(frn))) {
        picked.push({ id: c.id, score: 2, line: `- EMA 등록 기업: ${short(c.label, 30)} (FRN ${c.props.frn}) — 상세는 ema__ema_registry_get으로 다시 확인` });
      }
    }

    // 비슷한 지난 작업 — 어떤 도구로 했고, 어떤 신호가 났고, 어떻게 끝났는지
    const kin = new Set(typeId ? sameType(typeId).map((id) => (id.startsWith('task:') ? id : `task:${id}`)) : []);
    const tasks = [];
    for (const t of nodes.values()) {
      if (t.type !== 'task' || !t.props.instruction || t.props.status === 'dropped') continue;
      const s = overlap(q, grams(t.props.instruction));
      // 같은 유형이면 회사가 달라도 꺼낸다 — 신호 없이 끝난 작업을 앞에(잘 된 흐름을 따르게)
      const clean = !sigs.some((x) => x.links.includes(t.id));
      if (kin.has(t.id)) tasks.push({ t, score: 1.5 + (clean ? 0.3 : 0) + s / 10, kin: true });
      else if (s >= minScore) tasks.push({ t, score: s });
    }
    tasks.sort((a, b) => b.score - a.score || String(b.t.last).localeCompare(String(a.t.last)));
    for (const { t, score } of tasks) {
      const out = (outIdx.get(t.id) || []).map((i) => edges[i]);
      const steps = out.filter((e) => e.r === 'has_step').map((e) => nodes.get(e.o)).filter(Boolean)
        .sort((a, b) => (a.props.order || 0) - (b.props.order || 0));
      const tools = [...new Set(steps.map((s) => s.props.tool).filter(Boolean))].slice(0, 5);
      const answer = out.filter((e) => e.r === 'produced').map((e) => nodes.get(e.o)).filter(Boolean).pop();
      const mine = sigs.filter((s) => s.links.includes(t.id));
      const sig = mine.map((s) => `${s.type}(${short(s.title, 50)})`).slice(0, 2);
      const parts = [`- 지난 작업 ${day(t.first)}${kin.has(t.id) && typeName ? ` (같은 업무 유형: ${short(typeName, 30)})` : ''}: "${short(t.props.instruction, 60)}"`,
        `  ${steps.length}단계 · 도구 ${tools.join(', ') || '없음'}${sig.length ? ` · 신호 ${sig.join(', ')}` : ''}`];
      // 틀린 것으로 드러난 답("없다"인데 있었음)은 다시 붙이지 않는다 — 같은 잘못을 따라 하게 된다
      if (mine.some((s) => s.type === '근거 없는 없다')) parts.push('  답: "없다"고 했지만 틀린 답이었음 — 이 작업의 결론은 쓰지 말 것');
      else if (answer) parts.push(`  답: ${short(onto.blobText(answer.props.hash, 400) || answer.label, 140)}`);
      picked.push({ id: t.id, score, line: parts.join('\n') });
    }

    picked.sort((a, b) => b.score - a.score);
    const items = [];
    let size = 0;
    for (const p of picked) {
      if (items.length >= limit || size + p.line.length > maxChars) continue;
      items.push(p);
      size += p.line.length;
    }
    return { ids: items.map((p) => p.id), lines: items.map((p) => p.line) };
  }
  return { recall };
}

module.exports = { createRecall };
