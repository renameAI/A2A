// 메리 딜룸 화면 점검 — A2A_SELFTEST_DIR=<폴더> A2A_SELFTEST_ONLY=merry npm run dev
// 즐겨찾기 "메리"로 열기 → 투자 건 → 논의·결정 → 가정 잠금·검산 → 업무 지도에 결정 노드.
// A2A_MERRY_PDF=<보고서·IR PDF 경로> + API 키가 있으면 실제 Claude로: 근거 추출 → 가정 초안 → 잠금 → 섹션 작성·검증·채점 → 질문·강점/우려.
const path = require('path');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) {
  const t0 = Date.now();
  for (;;) { const v = await fn(); if (v) return v; if (Date.now() - t0 > ms) return null; await sleep(200); }
}

module.exports = async function merrySelftest(ctx, step, dir, winShot) {
  const sh = ctx.shell().webContents;
  const js = (code) => sh.executeJavaScript(code);
  const inv = (name, ...args) => js(`a2a.invoke('invest:call', ${JSON.stringify(name)}, ...${JSON.stringify(args)})`);
  const clickTab = (label) => js(`(() => { const b = [...document.querySelectorAll('nav button')].find((x) => x.innerText.trim().startsWith(${JSON.stringify(label)})); if (b) b.click(); return !!b; })()`);

  // 1) 즐겨찾기 → 메리 탭
  const bm = await js(`[...document.querySelectorAll('aside button')].some((b) => b.title === 'a2a://메리')`);
  step('merry-bookmark', !!bm, bm ? '즐겨찾기에 메리' : '즐겨찾기에 없음');
  await js(`a2a.send('bookmark:open', 'a2a://메리')`);
  const opened = await until(() => js(`document.body.innerText.includes('투자 딜룸')`));
  step('merry-open', !!opened, opened ? 'a2a://메리 화면' : '안 열림');
  // 메리에서 홈 → 새 탭 화면(메리를 빈 탭으로 잘못 보지 않는지), 다시 메리
  await js(`a2a.send('nav:home')`);
  const home = await until(() => js(`!document.getElementById('card')?.className.includes('bg-card') && !document.body.innerText.includes('투자 딜룸')`), 4000);
  step('merry-home', !!home, home ? '홈 → 새 탭 화면' : '홈을 눌러도 메리에 남음');
  await js(`a2a.send('bookmark:open', 'a2a://메리')`);
  await until(() => js(`document.body.innerText.includes('투자 딜룸')`));
  // 주소창 a2a:// → 우리 화면, 사이드바·대화창 폭 끌기, 우리 화면 즐겨찾기 별
  await js(`a2a.send('nav:home')`); await sleep(400);
  await js(`a2a.send('nav:go', 'a2a://업무지도')`);
  const mapOk = await until(() => js(`!!document.getElementById('workmap')`), 4000);
  step('addr-internal', !!mapOk, mapOk ? 'a2a://업무지도 → 업무 지도' : '주소창 a2a:// 안 됨');
  await js(`a2a.send('nav:go', '메리')`);
  await until(() => js(`document.body.innerText.includes('투자 딜룸')`));
  const dragBy = async (sel, dx) => {
    const r = await js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()`);
    if (!r) return false;
    sh.sendInputEvent({ type: 'mouseDown', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    for (let i = 1; i <= 5; i++) { sh.sendInputEvent({ type: 'mouseMove', x: r.x + Math.round((dx * i) / 5), y: r.y, modifiers: ['leftButtonDown'] }); await sleep(40); }
    sh.sendInputEvent({ type: 'mouseUp', x: r.x + dx, y: r.y, button: 'left', clickCount: 1 });
    await sleep(500);
    return true;
  };
  const sideBefore = await js(`document.querySelector('aside')?.getBoundingClientRect().width`);
  await dragBy('#resize-sidebar', 80);
  const sideAfter = await js(`document.querySelector('aside')?.getBoundingClientRect().width`);
  step('resize-sidebar', sideAfter > sideBefore + 40, `사이드바 ${Math.round(sideBefore)} → ${Math.round(sideAfter)}px`);
  // 사이드바엔 열린 탭 줄도 같은 주소를 단다 — 개수로 센다
  const bmCount = () => js(`[...document.querySelectorAll('aside button')].filter((b) => b.title === 'a2a://메리').length`);
  const n0 = await bmCount();
  await js(`a2a.send('bookmark:toggle')`); await sleep(400);
  const bmOff = (await bmCount()) === n0 - 1;
  await js(`a2a.send('bookmark:toggle')`); await sleep(400);
  const bmOn = (await bmCount()) === n0;
  step('internal-bookmark', bmOff && bmOn, `별로 메리 즐겨찾기 빼기 ${bmOff} · 다시 넣기 ${bmOn}`);

  // 2) 투자 건 + 논의·결정 + 가정
  const deal = await inv('create', { company: '샘플수산', fund: '샘플 임팩트 펀드', amount: 300000000, method: '신주 / RCPS', vision: '지역 수산물로 지속가능한 가공 밸류체인을 만든다' });
  await inv('setStage', deal.id, 'internal_ir', '2024-11-04');
  await inv('setStage', deal.id, 'impact_committee', '2024-11-15');
  const q = await inv('addItem', deal.id, { kind: 'question', text: '적용 PER 밴드를 Peer 평균에서 어떻게 할인할까요?', section: 'valuation' });
  const d1 = await inv('decide', deal.id, { topic: '적용 PER 밴드', chosen: '14 / 15 / 16배', rationale: 'Peer 평균 21.25배에 신규 상장 할인율 22.2~33.6% 반영', resolves: [q.id], evidence: [q.id], section: 'valuation' });
  await inv('addItem', deal.id, { kind: 'bear', text: '육상 양식은 에너지 사용량 비교우위가 아직 확인되지 않음', section: 'risk' });

  const pdf = process.env.A2A_MERRY_PDF;
  if (pdf && process.env.A2A_MERRY_INTERACTIVE) {
    // 실제 흐름 그대로: 첨부 → 메리가 읽음 → 가정 잠금 → "메리와 함께 쓰기"(질문에는 화면의 선택지를 눌러 답함) → 대화로 고쳐 쓰기
    const view = () => inv('deal', deal.id);
    const idle = async (ms, shotAt) => {
      const t0 = Date.now();
      let shot = false;
      for (;;) {
        // 열린 질문이 있으면 화면의 첫 선택지를 누른다(없으면 건너뛰기)
        const clicked = await js(`(() => { const card = document.querySelector('[data-ask="open"]'); if (!card) return null; const b = card.querySelector('button[data-opt]') || [...card.querySelectorAll('button')].find((x) => x.innerText.includes('건너뛰기')); if (!b) return null; const t = b.innerText; b.click(); return t; })()`);
        if (clicked) { if (shotAt && !shot) { shot = true; } step('merry-ask-click', true, clicked.slice(0, 80)); await sleep(800); continue; }
        const v = await view();
        if (!v.copilot.busy) return v;
        if (shotAt && Date.now() - t0 > 25000 && !shot) { shot = true; await winShot(ctx, path.join(dir, shotAt)); }
        if (Date.now() - t0 > ms) return v;
        await sleep(1500);
      }
    };
    await clickTab('보고서');
    const t0 = Date.now();
    const bytes = require('fs').readFileSync(pdf);
    const src = await js(`a2a.invoke('invest:call', 'attachBytes', ${JSON.stringify(deal.id)}, ${JSON.stringify(path.basename(pdf))}, new Uint8Array(${JSON.stringify([...bytes])}))`);
    await inv('readAttached', deal.id, [src.id]);
    let v = await idle(600000, 'merry-i1-reading.png');
    const nf = v.deal.facts.length;
    step('merry-i-extract', nf >= 30, `근거 ${nf}개 · ${Math.round((Date.now() - t0) / 1000)}초 · 마지막 말: ${(v.chat.at(-1)?.text || '').slice(0, 200)}`);
    if (nf) {
      const dr = await inv('draftPack', deal.id);
      const lk = await inv('lockPack', deal.id, dr.pack, { note: '자체 점검 — 초안 그대로' });
      step('merry-i-lock', lk.version >= 1, `${lk.checks.map((c) => `[${c.status}] ${c.message}`).join(' | ')} · 빈 칸 ${dr.gaps?.length || 0}`);
    }
    await inv('write', deal.id, 'valuation');
    v = await idle(900000, 'merry-i2-writing.png');
    const sec = v.sections.valuation;
    step('merry-i-write', !!sec, sec ? JSON.stringify(sec.summary) : '섹션 없음');
    const kinds = {};
    for (const m of v.chat) kinds[m.kind] = (kinds[m.kind] || 0) + 1;
    step('merry-i-chat', (kinds.thinking || 0) >= 1 && (kinds.ask || 0) >= 1, JSON.stringify(kinds));
    await winShot(ctx, path.join(dir, 'merry-i3-written.png'));
    if (sec) {
      await inv('say', deal.id, 'Peer 선정 기준을 1차(사업)·2차(재무)·3차(일반) 세 단계 표로 정리해 주세요.', 'valuation');
      v = await idle(600000, null);
      step('merry-i-revise', v.chat.some((m) => m.text.startsWith('말씀대로 고쳤어요')), (v.chat.filter((m) => m.role === 'merry' && m.kind === 'msg').at(-1)?.text || '').slice(0, 200));
    }
    await inv('say', deal.id, '이 회사의 2023년 실제 매출과 근거 쪽수를 알려줘');
    v = await idle(300000, null);
    step('merry-i-qa', /\d/.test(v.chat.at(-1)?.text || ''), (v.chat.at(-1)?.text || '').slice(0, 300));
    await winShot(ctx, path.join(dir, 'merry-i4-final.png'));
    const dc = v.deal.decisions.length;
    step('merry-i-decisions', dc >= 2, `결정 ${dc}개: ${v.deal.decisions.map((x) => `${x.topic} → ${x.chosen}`).join(' / ').slice(0, 400)}`);
  } else if (pdf) {
    const t0 = Date.now();
    try {
      const src = await inv('addSourcePath', deal.id, pdf);
      const ex = await inv('extract', deal.id, src.id);
      step('merry-extract', ex.n >= 30, `근거 ${ex.n}개 · ${Math.round((Date.now() - t0) / 1000)}초 · ${ex.notes || ''}`);
      const dr = await inv('draftPack', deal.id);
      step('merry-draft', !!dr?.pack?.deal, `빈 칸 ${dr?.gaps?.length || 0}: ${(dr?.gaps || []).slice(0, 4).join(' / ')}`);
      const lk = await inv('lockPack', deal.id, dr.pack, { note: '자체 점검 — 초안 그대로' });
      step('merry-lock', lk.version === 1, lk.checks.map((c) => `[${c.status}] ${c.message}`).join(' | '));
      for (const key of ['valuation', 'overview', 'exec']) {
        const w = await inv('writeNow', deal.id, key);
        step(`merry-write-${key}`, !!w?.summary, JSON.stringify(w?.summary));
      }
      const pl = await inv('plan', deal.id);
      step('merry-plan', pl.asked >= 1, `질문 ${pl.asked}개`);
      const pp = await inv('perspectives', deal.id);
      step('merry-perspectives', pp.n >= 2, `강점·우려 ${pp.n}개`);
    } catch (e) { step('merry-live', false, String(e.message || e).slice(0, 400)); }
  } else {
    const pack = { deal: { amount: 300000000, price: 150000, shares: 2000, postShares: 20000, investDate: '2025-01-15', exitDate: '2029-01-15', exitShares: 21000 },
      pnl: { reviewer: { 2029: { revenue: 30000e6, cogs: 16000e6, sga: 10000e6, op: 4000e6, ni: 3000e6 } } },
      valuation: { exitYear: 2029, basis: 'reviewer', perBand: [14, 15, 16], peers: [{ name: 'Peer A', per: 24 }, { name: 'Peer B', per: 21 }, { name: 'Peer C', per: 11 }, { name: 'Peer D', per: 29 }], discount: [0.222, 0.336] } };
    const lk = await inv('lockPack', deal.id, pack, { note: '자체 점검' });
    step('merry-lock', lk.version === 1, lk.checks.map((c) => `[${c.status}] ${c.message}`).join(' | '));
  }

  // 3) 화면 — 보고서(종이) · 논의·결정 · 가정·계산
  await sleep(800);
  await winShot(ctx, path.join(dir, 'merry-1-report.png'));
  const pages = await js(`document.querySelectorAll('.mr-page').length`);
  // 종이 책상이 스크롤되는지 — 바퀴(wheel)로
  const deskBox = await js(`(() => { const d = document.querySelector('.mr-desk'); const r = d.getBoundingClientRect(); const chain = []; for (let e = d; e && e !== document.body; e = e.parentElement) chain.push(e.tagName + '.' + String(e.className).slice(0, 40) + ' h=' + e.clientHeight + '/' + e.scrollHeight); return { x: r.left + r.width / 2, y: r.top + r.height / 2, chain }; })()`);
  sh.sendInputEvent({ type: 'mouseWheel', x: Math.round(deskBox.x), y: Math.round(deskBox.y), deltaX: 0, deltaY: -600, canScroll: true });
  await sleep(600);
  const scrolled = await js(`document.querySelector('.mr-desk').scrollTop`);
  step('merry-scroll', scrolled > 0, `scrollTop ${scrolled} · ${deskBox.chain.join(' < ')}`);
  step('merry-report-pages', pages >= 10, `종이 ${pages}장`);
  const font = await js(`getComputedStyle(document.querySelector('.mr-page')).fontFamily`);
  step('merry-doc-font', /Malgun|Apple SD Gothic/.test(font), font);
  for (const [i, key] of [['2', 'valuation'], ['3', 'exec']]) {
    const has = await js(`(() => { const p = [...document.querySelectorAll('.mr-page')].find((x) => x.querySelector('h1')?.innerText.startsWith(${JSON.stringify(key === 'valuation' ? 'VII' : 'Executive')})); if (!p) return false; p.scrollIntoView(); return !!p.querySelector('table, ul, p'); })()`);
    await sleep(300);
    await winShot(ctx, path.join(dir, `merry-${i}-${key}.png`));
    if (pdf) step(`merry-page-${key}`, !!has, has ? '본문 있음' : '본문 없음');
  }
  await until(() => js(`!!document.getElementById('merry-chat')`), 4000);
  const chatBefore = await js(`document.getElementById('merry-chat')?.getBoundingClientRect().width`);
  await dragBy('#resize-merry-chat', -100);
  const chatAfter = await js(`document.getElementById('merry-chat')?.getBoundingClientRect().width`);
  step('resize-chat', chatAfter > chatBefore + 50, `대화창 ${Math.round(chatBefore)} → ${Math.round(chatAfter)}px · ${await js(`[document.body.innerText.includes('투자 딜룸'), document.body.innerText.includes('메리와 함께 쓰기'), !!document.getElementById('merry-chat'), document.querySelectorAll('nav button').length].join(',')`)}`);
  await clickTab('논의');
  await sleep(500);
  await winShot(ctx, path.join(dir, 'merry-4-discuss.png'));
  await clickTab('가정');
  await sleep(500);
  await winShot(ctx, path.join(dir, 'merry-5-pack.png'));
  await clickTab('근거');
  await sleep(500);
  await winShot(ctx, path.join(dir, 'merry-6-facts.png'));

  // 4) 업무 지도 — 결정·투자 건·업무 유형
  const node = await until(() => js(`a2a.invoke('onto:call', 'node', 'decision:${d1.id}')`), 6000);
  step('merry-onto-decision', !!node, node ? `${node.label || node.node?.label || ''}` : '결정 노드 없음');
  const dl = await js(`a2a.invoke('onto:call', 'list', 'deal', '')`);
  step('merry-onto-deal', Array.isArray(dl) && dl.length >= 1, JSON.stringify(dl?.slice?.(0, 1)));
  const wt = await js(`a2a.invoke('onto:call', 'worktypes')`);
  const names = JSON.stringify(wt).match(/투자 심사보고서/);
  step('merry-worktype', !!names, names ? '업무 유형 투자 > 투자 심사보고서' : JSON.stringify(wt).slice(0, 200));
  await js(`a2a.send('map:open')`);
  await sleep(1200);
  await js(`(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim().startsWith('투자 건')); if (b) b.click(); })()`);
  await sleep(800);
  await winShot(ctx, path.join(dir, 'merry-7-workmap.png'));
};
