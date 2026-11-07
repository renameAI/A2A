// 메리와 함께 쓰기 — 보고서 옆 대화창의 두뇌. 심사역과 주고받으며 쓴다(한 번에 생성하지 않는다).
//
//   섹션 쓰기: 브리핑(구성 + 근거로 못 채우는 판단 질문) → 심사역 답(객관식/직접 입력, 결정으로 기록)
//             → 쓰기(종이에 실시간) → 검증·채점 결과를 대화로 → 도구가 찾은 문제만 고쳐 쓰기
//             → 남은 문제(근거 없는 주장)는 심사역에게 어떻게 할지 묻는다
//   심사역 말: 섹션을 고른 상태면 그 섹션을 지시대로 고쳐 쓰고("다음에도 적용할까요?" → 플레이북 교훈),
//             아니면 첨부 자료(PDF)를 직접 읽고 답한다
//   첨부: 자료를 올리면 바로 읽어 근거(쪽·인용)를 뽑고 대화에 알린다
// 질문은 기다린다 — 답이 올 때까지 다음 단계로 가지 않는다. 대화·생각·질문·결정은 모두 업무 지도에 남는다.
const { section: sectionOf } = require('./template');

const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n)}…` : s; };

function createCopilot({ room, pipe, frn = null, bridge = null, typeDeal = () => {}, send = () => {} }) {
  const busy = new Map();          // dealId → { what, stop }
  const waits = new Map();         // msgId → resolve
  const emit = (dealId, p) => send('invest:progress', { dealId, ...p });

  const say = (dealId, text, extra = {}) => room.say(dealId, { role: 'merry', kind: 'msg', text, ...extra });
  const stepMsg = (dealId, text, section = null) => room.say(dealId, { role: 'merry', kind: 'step', text, section });
  const thought = (dealId) => (label, text) => room.say(dealId, { role: 'merry', kind: 'thinking', label, text: short(text, 6000) });

  // 객관식 질문 — 답이 올 때까지 기다린다. 논의(질문 항목)에도 남긴다
  // 돌려주는 값: 답(문자열) 또는 null(건너뜀). 질문 항목 id는 lastAsked에
  const lastAsked = new Map();
  function ask(dealId, { text, options = null, section = null, why = '', item = true }) {
    const it = item ? room.addItem(dealId, { kind: 'question', text: `${text}${why ? `\n(왜: ${why})` : ''}`, section, options, byAgent: true }) : null;
    const msg = room.say(dealId, { role: 'merry', kind: 'ask', text, section, options, data: { why, itemId: it?.id || null } });
    lastAsked.set(dealId, it?.id || null);
    emit(dealId, { type: 'ask', msgId: msg.id });
    return new Promise((resolve) => waits.set(msg.id, { resolve, dealId, msg, it }));
  }

  // 심사역 답 — choice(선택지) 또는 text(직접), skip
  function answer(dealId, msgId, { choice = null, text = null, skip = false } = {}) {
    const w = waits.get(msgId);
    if (!w || w.dealId !== dealId) throw new Error('기다리는 질문이 아니에요');
    waits.delete(msgId);
    const value = skip ? null : String(choice ?? text ?? '').trim() || null;
    room.updateChat(dealId, msgId, { status: skip ? 'skipped' : 'answered', answer: value });
    if (value) {
      room.say(dealId, { role: 'user', kind: 'msg', text: value, section: w.msg.section });
      if (w.it) room.addItem(dealId, { kind: 'answer', text: value, replyTo: w.it.id, section: w.msg.section });
    }
    w.resolve(value);
    return { ok: true };
  }

  function guard(dealId, what) {
    if (busy.has(dealId)) throw new Error(`메리가 ${busy.get(dealId).what} 중이에요`);
    const st = { what, stopped: false };
    busy.set(dealId, st);
    emit(dealId, { type: 'busy', what });
    return st;
  }
  function release(dealId) {
    busy.delete(dealId);
    for (const [k, w] of waits) if (w.dealId === dealId) { waits.delete(k); room.updateChat(dealId, k, { status: 'skipped' }); w.resolve(null); }
    emit(dealId, { type: 'idle' });
  }
  const run = (dealId, what, fn) => {
    const st = guard(dealId, what);
    (async () => {
      try { await fn(st); }
      catch (e) { say(dealId, `문제가 생겨 멈췄어요: ${short(e.message || e, 300)}`, { data: { error: true } }); }
      finally { release(dealId); }
    })();
    return { started: true };
  };
  const stopped = (st) => { if (st.stopped) throw new Error('심사역이 멈췄어요'); };

  // 웹 조사 — 검토 열 때 자동으로, 또는 심사역이 "검색해/찾아봐"라고 하면. 실패해도 검토는 계속
  const WANTS_WEB = /(웹|인터넷|검색|찾아\s*(봐|보|줘)|조사|알아\s*봐|기사|뉴스)/;
  // 채널 글에는 [f:id] 대신 사람이 읽는 출처로
  function readableCites(dealId, text) {
    const d = room.get(dealId);
    return String(text || '').replace(/(\[f:[a-z0-9_]+\])+/g, (m) => {
      const ids = [...m.matchAll(/\[f:([a-z0-9_]+)\]/g)].map((x) => x[1]);
      const refs = [...new Set(ids.map((fid) => {
        const f = d.facts.find((x) => x.id === fid);
        if (!f) return null;
        if (f.url) return f.url;
        const src = d.sources.find((x) => x.id === f.sourceId);
        return `${src ? src.name.replace(/\.(pdf|md|txt|json)$/i, '') : '자료'}${f.page ? ` p.${f.page}` : ''}`;
      }).filter(Boolean))];
      return refs.length ? ` (${refs.join(', ')})` : '';
    });
  }

  // 사내 팀 채널에 올리기 — 초안을 보여 주고 대화창에서 승인받은 뒤에만(홈 에이전트와 같은 기준)
  const POST_RE = /(보내|올려|공유|게시|전달)/;
  async function postFlow(dealId, text) {
    if (!bridge?.channels) { say(dealId, '팀 채팅에 연결돼 있지 않아요.'); return; }
    let chans = [];
    try { chans = await bridge.channels(); } catch (e) { say(dealId, `채널 목록을 못 가져왔어요: ${short(e.message, 120)}`); return; }
    const names = chans.map((c) => c.name);
    // "#general에", "@투자-비공개로"처럼 조사가 붙어도 — 실제 채널 이름 중 가장 긴 앞부분 일치
    const tokens = (text.match(/[#@][\w가-힣-]+/g) || []).map((x) => x.slice(1));
    let name = null;
    for (const t of tokens) { const hit = names.filter((n) => t.startsWith(n)).sort((a, b) => b.length - a.length)[0]; if (hit) { name = hit; break; } }
    if (!name) {
      name = await ask(dealId, { text: '어느 채널에 올릴까요?', options: names.slice(0, 6), item: false });
      if (!name || !names.includes(name)) { say(dealId, '채널을 고르지 않아 올리지 않았어요.'); return; }
    }
    const ch = chans.find((c) => c.name === name);
    let last = 0;
    const draft = await pipe.answer(dealId, `${text}\n\n[메리에게] 이 답은 사내 채널 #${name}에 그대로 올라갑니다. 채널 글로 바로 쓸 수 있게 짧고 분명하게 쓰고, 대화 상대에게 하는 말("복사해서 올려 주세요" 등)은 넣지 마세요.`, {
      onThought: thought(dealId),
      onText: (_d, all) => { const now = Date.now(); if (now - last > 200) { last = now; emit(dealId, { type: 'reply', text: all }); } },
    });
    emit(dealId, { type: 'reply', text: null });
    const body = readableCites(dealId, draft).trim().slice(0, 7800);
    say(dealId, `#${name}에 올릴 글이에요:\n\n${body}`, { data: { draft: true } });
    const ok = await ask(dealId, { text: `이대로 #${name}에 올릴까요? (🤖 표시로 올라갑니다)`, options: ['올리기', '취소'], item: false });
    if (ok !== '올리기') { say(dealId, '올리지 않았어요.'); return; }
    const m = await bridge.post(ch.id, body, { taskId: `deal-${dealId}` });
    room.action(dealId, { kind: 'post', text: body, data: { channel: name, cid: ch.id, seq: m?.seq ?? null, approved_by: 'reviewer' } });
    say(dealId, `#${name}에 올렸어요.`);
  }

  // 홈 에이전트에게 맡기기 — 같은 에이전트(브라우저 탭·EMA·승인 게이트 그대로). 읽은 웹 페이지는 출처 URL이 달린 근거로
  async function browse(dealId, text) {
    if (!bridge?.delegate) return webLook(dealId, text);
    const d = room.get(dealId);
    stepMsg(dealId, '홈 에이전트에게 맡겼어요 — AI 패널에서 진행이 보이고, 누르기·입력·게시는 평소처럼 승인을 받습니다.');
    let r;
    try {
      r = await bridge.delegate(`[메리 딜룸 · ${d.company}${d.frn ? ` · FRN ${d.frn}` : ''} 투자 검토] ${text}\n찾은 사실은 출처 URL과 함께 정리해 주세요. 메일 발송·외부 게시·로그인은 하지 마세요.`);
    } catch (e) {
      stepMsg(dealId, `홈 에이전트를 쓸 수 없어 메리가 직접 웹 검색할게요(${short(e.message, 80)})`);
      return webLook(dealId, text);
    }
    room.action(dealId, { kind: 'delegate', text: `${text}\n\n[에이전트 답]\n${r.answer}`, data: { agent_task_id: r.taskId, status: r.status }, mode: 'shadow' });
    const pages = (bridge.reads?.(r.taskId) || []).slice(0, 6);
    let n = 0;
    for (const p of pages) {
      const src = room.addSource(dealId, { name: `웹 · ${short(p.title || p.url, 60)}.txt`, buf: Buffer.from(`${p.url}\n\n${p.text}`, 'utf8'), kind: 'web', mime: 'text/plain', url: p.url });
      try { n += (await pipe.extract(dealId, src.id, { onThought: thought(dealId) })).n; } catch (e) { stepMsg(dealId, `${short(p.url, 60)} 정리 실패: ${short(e.message, 80)}`); }
    }
    say(dealId, `홈 에이전트가 ${pages.length}개 페이지를 읽었고, 근거 ${n}개를 출처 링크와 함께 넣었어요(웹 공개자료 — 회사 자료로 확인 전까지는 보도 기준).${r.answer ? `\n\n[에이전트 답]\n${short(r.answer, 1500)}` : ''}`);
  }

  async function webLook(dealId, focus) {
    stepMsg(dealId, `공개 웹에서 ${room.get(dealId).company}을(를) 찾아보는 중…${focus ? ` (요청: ${short(focus, 60)})` : ''}`);
    try {
      const r = await pipe.research(dealId, { focus, hooks: { onThought: thought(dealId) } });
      const facts = room.get(dealId).facts.filter((f) => f.basis === 'web').slice(-r.n);
      say(dealId, `웹에서 공개 사실 ${r.n}개를 찾았어요(출처 링크 포함 — 회사 자료로 확인 전까지는 보도 기준).\n${facts.slice(0, 10).map((f) => `· ${f.label}: ${short(f.value, 60)}${f.url ? ` — ${f.url}` : ''}`).join('\n')}${r.summary ? `\n\n${r.summary}` : ''}${r.notFound?.length ? `\n못 찾은 것: ${r.notFound.slice(0, 5).join(', ')}` : ''}`);
    } catch (e) { stepMsg(dealId, `웹 조사 실패: ${short(e.message || e, 160)}`); }
  }

  // ── 섹션 함께 쓰기 ──
  function write(dealId, key) {
    const s = sectionOf(key);
    if (!s) throw new Error('모르는 섹션');
    return run(dealId, `${s.title} 쓰기`, async (st) => {
      const d = room.get(dealId);
      const cx = room.latestCompute(dealId);
      stepMsg(dealId, `${s.title} — 근거 ${d.facts.length}개, 유효한 결정 ${d.decisions.filter((x) => !x.supersededBy).length}개${cx ? `, 계산 변수 ${Object.keys(cx.vars).length}개` : ', 가정이 아직 잠기지 않음'}로 시작합니다`, key);
      if (!d.facts.length) say(dealId, '아직 읽은 자료가 없어요. IR·재무제표를 첨부해 주시면 근거를 뽑아 쓰겠습니다. 지금은 뼈대만 쓰고 숫자 칸은 [확인 필요]로 남길게요.', { section: key });
      const b = await pipe.brief(dealId, key, { onThought: thought(dealId) });
      stopped(st);
      say(dealId, `이렇게 쓰려고 해요.\n${b.outline.map((x, i) => `${i + 1}. ${x}`).join('\n')}${b.note ? `\n\n${b.note}` : ''}`, { section: key });
      for (const q of b.questions) {
        const v = await ask(dealId, { text: q.text, options: q.options?.length ? q.options : null, section: key, why: q.why });
        stopped(st);
        // 판단 질문에 고른 답은 결정으로 — 업무 지도에 "누가 무엇을 근거로 정했나"로 남는다
        // 선택지를 고른 답만 결정(탐색 단계)으로. 직접 쓴 말은 답으로만 — 지시면 따로 처리
        if (v && q.options?.includes(v)) {
          const qid = lastAsked.get(dealId);
          try { room.decide(dealId, { topic: short(q.text, 120), chosen: v, rationale: `${s.title} 작성 중 메리 질문에 대한 심사역 판단${q.why ? ` — ${q.why}` : ''}`, evidence: qid ? [qid] : [], resolves: qid ? [qid] : [], section: key }); } catch (e) { stepMsg(dealId, `결정 기록 실패: ${short(e.message, 100)}`); }
        } else if (v && WANTS_WEB.test(v)) await browse(dealId, v);
      }
      let last = 0;
      const r = await pipe.writeSection(dealId, key, {
        brief: b,
        hooks: {
          onThought: thought(dealId),
          onStep: (t) => emit(dealId, { type: 'step', section: key, text: t }),
          onDraft: (html) => { const now = Date.now(); if (now - last > 250) { last = now; emit(dealId, { type: 'draft', section: key, html }); } },
          onCheck: (c) => stepMsg(dealId, c.problems.length
            ? `검증 ${c.round ? '(고친 뒤) ' : ''}— 체크리스트 ${Math.round(c.score * 100)}%, 주장 ${c.claims}개 중 근거 불일치 ${c.unsupported}개. ${c.round ? '' : `문제 ${c.problems.length}개만 고쳐 쓸게요.`}\n${c.problems.slice(0, 6).map((p) => `· ${short(p, 140)}`).join('\n')}`
            : `검증 통과 — 체크리스트 ${Math.round(c.score * 100)}%, 주장 ${c.claims}개 모두 근거와 맞음`, key),
        },
      });
      emit(dealId, { type: 'draft', section: key, html: null });
      stopped(st);
      const bad = r.unsupportedClaims || [];
      say(dealId, `${s.title}을(를) 종이에 올렸어요 (체크리스트 ${r.summary.score}%${r.summary.rounds ? ', 한 번 고쳐 씀' : ''}). 고칠 곳은 이 대화로 말씀해 주세요.`, { section: key });
      if (bad.length) {
        const v = await ask(dealId, {
          text: `근거로 확인되지 않은 문장이 ${bad.length}개 남았어요:\n${bad.slice(0, 5).map((c) => `· "${short(c.text, 90)}"`).join('\n')}\n어떻게 할까요?`,
          options: ['[확인 필요]로 표시', '지우기', '그대로 두기(제가 확인)'], section: key, item: false,
        });
        stopped(st);
        if (v === '[확인 필요]로 표시' || v === '지우기') {
          const cur = room.get(dealId).sections[key];
          await pipe.writeSection(dealId, key, { base: cur.html, refine: false, instruction: `${v === '지우기' ? '다음 문장을 지우세요' : '다음 문장 끝에 [확인 필요: 근거 없음]을 붙이세요'}:\n${bad.map((c) => `- ${c.text}`).join('\n')}`,
            hooks: { onThought: thought(dealId), onDraft: (html) => emit(dealId, { type: 'draft', section: key, html }) } });
          emit(dealId, { type: 'draft', section: key, html: null });
          say(dealId, v === '지우기' ? '그 문장들을 지웠어요.' : '[확인 필요]로 표시했어요.', { section: key });
        } else if (v) {
          room.decide(dealId, { topic: `${s.title} 근거 미확인 문장 처리`, chosen: '심사역이 직접 확인하고 그대로 둠', section: key });
        }
      }
    });
  }

  // ── 심사역이 대화창에 쓴 말 ──
  function user(dealId, text, { section = null } = {}) {
    text = String(text || '').trim().slice(0, 8000);
    if (!text) throw new Error('내용이 비었어요');
    // 질문을 기다리는 중이면 그 질문의 직접 답으로
    const pending = [...waits.entries()].find(([, w]) => w.dealId === dealId);
    if (pending) return answer(dealId, pending[0], { text });
    room.say(dealId, { role: 'user', kind: 'msg', text, section });
    // 채널에 올려 달라는 말(#채널/@채널 + 보내·올려·공유) — 섹션 고쳐 쓰기보다 먼저
    if (/[#@][\w가-힣-]+/.test(text) && POST_RE.test(text) || /(채널|팀)[^\n]{0,12}(보내|올려|공유|게시)/.test(text)) {
      return run(dealId, '채널에 올리기', () => postFlow(dealId, text));
    }
    if (WANTS_WEB.test(text)) return run(dealId, '웹 찾기', () => browse(dealId, text));
    const sec = section && room.get(dealId).sections[section];
    if (sec) {
      const s = sectionOf(section);
      return run(dealId, `${s.title} 고쳐 쓰기`, async (st) => {
        room.addItem(dealId, { kind: 'comment', text: `[${s.title} 수정 지시] ${text}`, section });
        const r = await pipe.writeSection(dealId, section, { base: sec.html, instruction: text,
          hooks: { onThought: thought(dealId), onStep: (t) => emit(dealId, { type: 'step', section, text: t }),
            onDraft: (html) => emit(dealId, { type: 'draft', section, html }) } });
        emit(dealId, { type: 'draft', section, html: null });
        stopped(st);
        say(dealId, `말씀대로 고쳤어요 (체크리스트 ${r.summary.score}%${r.summary.unsupported ? `, 근거 불일치 ${r.summary.unsupported}` : ''}).`, { section });
        // 사람의 수정은 가장 좋은 교훈 — 다음 보고서에도 쓸지 묻는다(ACE식 플레이북)
        const v = await ask(dealId, { text: '이 지시를 다음 보고서에도 적용할까요?', options: ['이 섹션의 교훈으로 저장', '이번만'], section, item: false });
        if (v === '이 섹션의 교훈으로 저장') { pipe.addLesson(section, text, 'human'); say(dealId, '플레이북에 저장했어요. 다음 보고서의 이 섹션부터 붙습니다.', { section }); }
      });
    }
    return run(dealId, '답하기', async () => {
      let last = 0;
      const out = await pipe.answer(dealId, text, {
        onThought: thought(dealId),
        onText: (_d, all) => { const now = Date.now(); if (now - last > 200) { last = now; emit(dealId, { type: 'reply', text: all }); } },
      });
      emit(dealId, { type: 'reply', text: null });
      say(dealId, out || '(답을 만들지 못했어요)');
    });
  }

  // ── 첨부 — 올리면 바로 읽는다 ──
  function attached(dealId, sources) {
    if (!sources.length) return { started: false };
    room.say(dealId, { role: 'user', kind: 'msg', text: `📎 ${sources.map((s) => s.name).join(', ')}` });
    return run(dealId, '자료 읽기', async (st) => {
      for (const src of sources) {
        stepMsg(dealId, `${src.name}을(를) 읽는 중…`);
        const r = await pipe.extract(dealId, src.id, { onThought: thought(dealId) });
        stopped(st);
        const facts = room.get(dealId).facts.filter((f) => f.sourceId === src.id);
        const by = {};
        for (const f of facts) by[f.section || '기타'] = (by[f.section || '기타'] || 0) + 1;
        // 받기로 한 자료가 들어왔는지 — 열린 요청과 대조
        let matched = [];
        try { matched = await pipe.matchRequests(dealId, src.id); } catch { /* 대조 실패는 넘어간다 */ }
        for (const m of matched) room.updateItem(dealId, m.item, { status: m.status === 'partial' ? 'partial' : 'received', evidence: m.facts || [] }, { note: m.note || src.name });
        if (matched.length) {
          const items = room.get(dealId).items;
          say(dealId, `요청했던 자료 중 ${matched.length}개가 들어왔어요:\n${matched.map((m) => `· ${m.status === 'partial' ? '(일부) ' : ''}${short(items.find((x) => x.id === m.item)?.text, 80)}`).join('\n')}\n남은 요청: ${items.filter((x) => x.kind === 'request' && x.status === 'open').length}개`);
        }
        say(dealId, `${src.name}에서 근거 ${r.n}개를 뽑았어요(쪽 번호·원문 인용 포함).\n${Object.entries(by).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, n]) => `· ${sectionOf(k)?.title || k}: ${n}개`).join('\n')}${r.notes ? `\n${r.notes}` : ''}`);
      }
      if (!room.latestPack(dealId)) say(dealId, '가정이 아직 잠기지 않았어요. 가정·계산 탭에서 "근거로 초안 만들기" 후 잠그면 밸류에이션 숫자를 계산해서 쓸 수 있어요.');
    });
  }

  // ── FRN 연결: 대표·앵커의 지시를 붙여 넣으면 검토를 연다 ──
  // 확정하지 않는다: 단계는 '발굴', 지시 속 숫자는 [미확인 주장], 할 일은 "부족한 자료를 받아 내기"
  function intake(text, { instructor = '', channel = '' } = {}) {
    text = String(text || '').trim().slice(0, 8000);
    if (!text) throw new Error('지시 내용이 비었어요');
    const tmp = `intake_${Date.now()}`;
    let dealId = null;
    busy.set(tmp, { what: '지시 읽기' });
    send('invest:progress', { type: 'intake', state: 'start' });
    (async () => {
      try {
        const p = await pipe.parseDirective(text, {}) || {};
        const code = (p.frn || (text.match(/\b[A-Z]{3}-[A-Z]{2}-\d{2}-\d{3}\b/) || [])[0] || '').trim() || null;
        // FRN 등록 정보(EMA) — 읽기 전용 도구로
        let reg = null, regErr = null;
        if (code && frn) { try { reg = await frn.get(code); } catch (e) { regErr = e.message; } }
        else if (!code && p.company && frn) { try { reg = await frn.search(p.company); } catch (e) { regErr = e.message; } }
        const regName = reg && (reg.match(/"name"\s*:\s*"([^"]{1,80})"/) || [])[1];
        const company = (regName || p.company || code || '이름 미상 기업').replace(/\s*\(.*?\)\s*$/, '');
        const deal = room.create({ company, fund: p.fund || '', amount: Number(p.amount_hint) || null, frn: code });
        dealId = deal.id;
        busy.delete(tmp);
        busy.set(dealId, { what: '검토 열기', stopped: false });
        send('invest:progress', { type: 'intake', state: 'deal', dealId });
        typeDeal(dealId);
        const dr = room.directive(dealId, { text, instructor: instructor || p.instructor || '', assignees: p.assignees || [], ask: p.ask || '', fund: p.fund || '', channel });
        room.say(dealId, { role: 'user', kind: 'msg', text: `[${dr.instructor}의 지시]\n${text}` });
        // 지시 원문을 자료로(대화·답변에서 다시 읽게), 속 숫자는 미확인 주장 근거로
        const dsrc = room.addSource(dealId, { name: `지시 — ${dr.instructor}.txt`, buf: Buffer.from(text, 'utf8'), kind: 'directive', mime: 'text/plain' });
        const claims = (p.claims || []).map((c, i) => ({ key: `claim_${i + 1}`, label: c.label || `주장 ${i + 1}`, value: c.value ?? c.text, unit: c.unit || '', period: c.period || '', quote: c.text || '', section: c.section || '', basis: 'claim' }));
        if (claims.length) room.addFacts(dealId, dsrc.id, claims, { byAgent: true });
        if (reg) {
          const rsrc = room.addSource(dealId, { name: `FRN ${code || company}.json`, buf: Buffer.from(reg, 'utf8'), kind: 'frn', mime: 'text/plain' });
          stepMsg(dealId, `EMA에서 FRN ${code || ''} 등록 정보를 가져왔어요 — 근거로 정리하는 중…`);
          try { await pipe.extract(dealId, rsrc.id, { onThought: thought(dealId) }); } catch (e) { stepMsg(dealId, `등록 정보 정리 실패: ${short(e.message, 120)}`); }
        } else {
          say(dealId, code ? `FRN ${code} 등록 정보를 못 가져왔어요${regErr ? `(${short(regErr, 120)})` : ''}. EMA가 연결돼 있으면 다시 시도할 수 있어요.` : '지시에 FRN 코드가 없어요. 회사 이름으로 EMA를 찾지 못했습니다.');
        }
        say(dealId, `${company} 검토를 열었어요${code ? ` (FRN ${code})` : ''}. 지시 속 숫자 ${claims.length}개는 확인 전 주장으로만 남겼고, 결론은 내리지 않습니다.${p.context?.length ? `\n상황: ${p.context.join(' / ')}` : ''}`);
        // 공개 웹에서 먼저 찾아본다 — 받을 자료 목록이 웹으로 확인되는 것까지 요청하지 않게
        await webLook(dealId, '');
        // 무엇을 받아 내야 하는가
        const g = await pipe.gaps(dealId, { onThought: thought(dealId) }) || {};
        for (const r of (g.requests || []).slice(0, 14)) {
          room.addItem(dealId, { kind: 'request', text: `${r.what}${r.why ? `\n(왜: ${r.why})` : ''}`, section: r.section || null, from: r.from || null, priority: r.priority || null, byAgent: true });
        }
        if (g.summary) say(dealId, `지금 상태 — 확정 아님\n${g.summary}`);
        if (g.request_message) say(dealId, `${dr.assignees?.length ? `${dr.assignees.join(', ')}님께` : '회사에'} 보낼 자료 요청 초안이에요(보내지 않았어요 — 복사해서 쓰세요).\n\n${g.request_message}`, { data: { draft: true } });
        for (const q of pipe.normQuestions(g.questions).slice(0, 3)) {
          const v = await ask(dealId, { text: q.text, options: q.options?.length ? q.options : null, why: q.why });
          if (v && q.options?.includes(v)) {
            const qid = lastAsked.get(dealId);
            try { room.decide(dealId, { topic: short(q.text, 120), chosen: v, rationale: `검토 착수 시 심사역 판단${q.why ? ` — ${q.why}` : ''}`, evidence: qid ? [qid] : [], resolves: qid ? [qid] : [], kind: 'scoping' }); } catch (e) { stepMsg(dealId, `결정 기록 실패: ${short(e.message, 100)}`); }
          } else if (v && WANTS_WEB.test(v)) await browse(dealId, v);
        }
        say(dealId, `받을 자료 ${room.get(dealId).items.filter((x) => x.kind === 'request' && x.status === 'open').length}개를 논의·결정 탭의 "받을 자료"에 올렸어요. 자료가 오면 여기로 끌어다 놓아 주세요 — 들어온 것은 제가 체크합니다.`);
      } catch (e) {
        if (dealId) say(dealId, `검토를 여는 중 문제가 생겼어요: ${short(e.message || e, 300)}`, { data: { error: true } });
        else send('invest:progress', { type: 'intake', state: 'error', text: short(e.message || e, 300) });
      } finally {
        busy.delete(tmp);
        if (dealId) release(dealId);
        send('invest:progress', { type: 'intake', state: 'done', dealId });
      }
    })();
    return { started: true };
  }

  return {
    write, user, answer, attached, intake,
    stop: (dealId) => { const st = busy.get(dealId); if (st) { st.stopped = true; release(dealId); } return { ok: true }; },
    state: (dealId) => ({ busy: busy.get(dealId)?.what || null, waiting: [...waits.entries()].filter(([, w]) => w.dealId === dealId).map(([k]) => k) }),
  };
}

module.exports = { createCopilot };
