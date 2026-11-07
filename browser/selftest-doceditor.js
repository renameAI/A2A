// 메리 문서 편집기(renderer/src/merry/DocEditor.jsx) 자체 점검 — 일회용 Electron 창에서만 돈다(켜 둔 앱·9555 포트와 무관).
//   npx electron selftest-doceditor.js
//   A2A_SELFTEST_DIR=<폴더>를 주면 그 아래에 빌드·스크린샷·결과(result.json)를 남긴다(없으면 임시 폴더).
// 점검: ① 손대지 않고 저장하면 받은 HTML 그대로 ② 한글 insertText·IME 조합 ③ 근거 칩 지우면 토큰째로 ④ Word·Docs 붙여넣기 정리
//       + 되돌리기/다시하기, ⌘B, 제목 단계, 표 마지막 칸 Tab → 새 행, 계산값 칩 표시
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

const dir = process.env.A2A_SELFTEST_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'a2a-doceditor-'));
fs.mkdirSync(dir, { recursive: true });
app.setPath('userData', path.join(dir, 'userData'));

const SAMPLE = `<h2>1. 밸류에이션</h2>
<p>회수 시점 기준 <strong>기본 시나리오 IRR</strong>은 {{var:exit_irr_base}}이며, Peer 평균 PER은 21.38배이다[f:f_abc123]. &amp; 비교 &lt;표&gt; 참고</p>
<ul>
<li>적용 PER 밴드 <em>14 / 15 / 16배</em>[f:f_def456]</li>
<li>할인율 근거 [확인 필요: 코스닥 신규 상장 할인율 출처]<ul><li>안쪽 항목</li></ul></li>
</ul>
<div class="caption">[표 1] 시나리오별 회수 가치</div>
<table><thead><tr><th>구분</th><th class="num">보수</th><th class="num">기본</th></tr></thead>
<tbody><tr><th>EV</th><td class="num">{{var:ev_low}}</td><td class="num" colspan="1">{{var:ev_base}}</td></tr>
<tr><th>증감</th><td class="num">(1,200)</td><td class="num">[확인 필요]</td></tr></tbody></table>
<div class="box"><ul><li>요약 상자 <u>밑줄</u><br>둘째 줄</li></ul></div>
<blockquote>인용 블록</blockquote>
<h3>1.1 항</h3><h4>소제목</h4><p>끝 문단[f:f_abc123]</p>`;
const VARS = { exit_irr_base: { display: '23.4%' }, ev_low: { display: '412억 원' }, ev_base: { display: '480억 원' } };

const HARNESS = `
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import DocEditor, { cleanPaste, normalizeHtml } from './DocEditor.jsx';
import '../merry.css';
window.__saves = []; window.__cites = []; window.__badSaves = 0; window.__ev = { start: 0, end: 0 }; window.__composing = false;
window.__DE = { cleanPaste, normalizeHtml };
document.addEventListener('compositionstart', () => { window.__ev.start++; window.__composing = true; }, true);
document.addEventListener('compositionend', () => { window.__ev.end++; window.__composing = false; }, true);
function App() {
  const [html, setHtml] = useState(window.__SAMPLE);
  return (
    <div className="mr-desk" style={{ height: '100vh', overflowY: 'auto', padding: '20px 0' }}>
      <section className="mr-page mr-doc">
        <h1 className="mr-chapter">VII. 손익 및 수익성 분석</h1>
        <DocEditor html={html} vars={window.__VARS}
          onSave={(h) => { if (window.__composing) window.__badSaves++; window.__saves.push(h); setHtml(h); }}
          onCite={(id) => window.__cites.push(id)} />
      </section>
    </div>
  );
}
createRoot(document.getElementById('root')).render(<App />);
`;

function build() {
  const esbuild = require('esbuild');
  esbuild.buildSync({
    stdin: { contents: HARNESS, resolveDir: path.join(__dirname, 'renderer/src/merry'), loader: 'jsx', sourcefile: 'harness.jsx' },
    bundle: true, outfile: path.join(dir, 'harness.js'), jsx: 'automatic', format: 'iife', target: 'chrome130',
    define: { 'process.env.NODE_ENV': '"development"' }, logLevel: 'error',
  });
  fs.writeFileSync(path.join(dir, 'index.html'), `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="harness.css">
<style>html,body{margin:0}</style><div id="root"></div>
<script>window.__SAMPLE=${JSON.stringify(SAMPLE)};window.__VARS=${JSON.stringify(VARS)};</script><script src="harness.js"></script>`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const step = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`); };

app.whenReady().then(async () => {
  let code = 1;
  try {
    build();
    const win = new BrowserWindow({ show: false, width: 1000, height: 1300, webPreferences: { contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(dir, 'index.html'));
    const wc = win.webContents;
    const js = (src) => wc.executeJavaScript(src);
    const dbg = wc.debugger;
    dbg.attach('1.3');
    const cdp = (m, p) => dbg.sendCommand(m, p || {});
    await cdp('Emulation.setFocusEmulationEnabled', { enabled: true });
    const META = process.platform === 'darwin' ? 4 : 2, SHIFT = 8, ALT = 1;
    const key = async (k, codeName, vk, mods = 0) => {
      await cdp('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code: codeName, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods });
      await cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code: codeName, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods });
    };
    const html = () => js(`window.__DE.normalizeHtml(document.querySelector('.mr-de-body').innerHTML.replace(/<sup[^>]*data-f="([a-z0-9_]+)"[^>]*>.*?<\\/sup>/g,'[f:$1]'))`);
    const lastSave = () => js('window.__saves[window.__saves.length-1] ?? null');
    const saves = () => js('window.__saves.length');
    const caretEndOf = (sel) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return true; })()`);
    await sleep(300);
    await js(`document.querySelector('.mr-de-body').focus()`);

    // 칩 모양
    const chips = await js(`({ cite: [...document.querySelectorAll('sup.mr-cite')].map(x=>x.textContent).join(','), v: document.querySelector('[data-var="exit_irr_base"]').textContent, tbd: document.querySelectorAll('.mr-tbd').length, ce: document.querySelector('.mr-var').getAttribute('contenteditable') })`);
    step('chips-render', chips.cite === '1,2,1' && chips.v === '23.4%' && chips.tbd === 2 && chips.ce === 'false', JSON.stringify(chips));

    // ① 손대지 않고 저장
    await js(`document.querySelector('.mr-de-body').blur()`);
    await sleep(100);
    step('roundtrip-blur-no-save', (await saves()) === 0, `저장 ${await saves()}번`);
    await js(`document.querySelector('.mr-de-body').focus()`);
    await caretEndOf('.mr-de-body h2');
    await key('s', 'KeyS', 83, META);
    await sleep(100);
    const rt = await lastSave();
    step('roundtrip-identical', rt === SAMPLE, rt === SAMPLE ? `${SAMPLE.length}자 그대로` : `다름:\n${rt}`);
    const canon = await js(`window.__DE.normalizeHtml(${JSON.stringify(SAMPLE)})`);
    const canon2 = await js(`window.__DE.normalizeHtml(${JSON.stringify(canon)})`);
    step('normalize-stable', canon === canon2 && /\[f:f_abc123\]/.test(canon) && /\{\{var:ev_base\}\}/.test(canon) && /\[확인 필요: 코스닥/.test(canon) && /\[확인 필요\]/.test(canon), canon === canon2 ? '정규형 고정점' : canon2);

    // ② 한글 — insertText
    await caretEndOf('.mr-de-body h4');
    await cdp('Input.insertText', { text: ' 가나다' });
    await sleep(50);
    const h4 = await js(`document.querySelector('.mr-de-body h4').textContent`);
    step('korean-insertText', h4 === '소제목 가나다', h4);

    // ② 한글 — IME 조합(ㅎ→하→한 확정, ㄱ→그→글 확정)
    const before = await saves();
    for (const t of ['ㅎ', '하', '한']) await cdp('Input.imeSetComposition', { text: t, selectionStart: t.length, selectionEnd: t.length });
    await cdp('Input.insertText', { text: '한' });
    for (const t of ['ㄱ', '그', '글']) await cdp('Input.imeSetComposition', { text: t, selectionStart: t.length, selectionEnd: t.length });
    await cdp('Input.insertText', { text: '글' });
    await sleep(50);
    const ime = await js(`({ t: document.querySelector('.mr-de-body h4').textContent, ev: window.__ev })`);
    step('korean-ime', ime.t === '소제목 가나다한글' && ime.ev.start >= 2 && ime.ev.end >= 2, `${ime.t} · compositionstart ${ime.ev.start} / end ${ime.ev.end}`);
    await sleep(1800);
    const s2 = await lastSave();
    step('autosave-1.5s', (await saves()) > before && s2.includes('<h4>소제목 가나다한글</h4>') && (await js('window.__badSaves')) === 0, `조합 중 저장 ${await js('window.__badSaves')}번`);

    // 되돌리기 / 다시하기
    await cdp('Input.insertText', { text: 'XYZ' });
    await sleep(900);
    await key('z', 'KeyZ', 90, META);
    const u1 = await js(`document.querySelector('.mr-de-body h4').textContent`);
    await key('z', 'KeyZ', 90, META | SHIFT);
    const u2 = await js(`document.querySelector('.mr-de-body h4').textContent`);
    step('undo-redo', !u1.includes('XYZ') && u2.endsWith('XYZ'), `⌘Z → "${u1}" · ⇧⌘Z → "${u2}"`);

    // ③ 근거 칩 지우기 — 칩 바로 뒤 캐럿에서 Backspace
    await js(`(() => { const c = document.querySelector('.mr-de-body p sup.mr-cite'); const r = document.createRange(); r.setStartAfter(c); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await key('Backspace', 'Backspace', 8);
    await sleep(50);
    const h3 = await html();
    const p1 = h3.match(/<p>회수[\s\S]*?<\/p>/)?.[0] || '';
    step('chip-delete-whole', !p1.includes('[f:') && p1.includes('21.38배이다. &amp; 비교'), p1);
    // 계산값 칩 — 앞에서 Delete
    await js(`(() => { const c = document.querySelector('[data-var="exit_irr_base"]'); const r = document.createRange(); r.setStartBefore(c); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await key('Delete', 'Delete', 46);
    const p1b = ((await html()).match(/<p>회수[\s\S]*?<\/p>/)?.[0]) || '';
    step('var-chip-delete-whole', !p1b.includes('{{var:') && !p1b.includes('exit_irr') && p1b.includes('IRR</strong>은 이며'), p1b);
    await key('z', 'KeyZ', 90, META);
    step('chip-delete-undo', (await html()).includes('{{var:exit_irr_base}}'), '⌘Z로 칩이 돌아옴');

    // 근거 칩 누르기 → onCite
    await js(`document.querySelector('sup.mr-cite').click()`);
    step('cite-click', (await js('window.__cites[0]')) === 'f_def456' || (await js('window.__cites[0]')) === 'f_abc123', await js('window.__cites.join(",")'));

    // ④ 붙여넣기 정리
    const paste = (data) => js(`(() => { const dt = new DataTransfer(); for (const [k, v] of Object.entries(${JSON.stringify(data)})) dt.setData(k, v);
      document.querySelector('.mr-de-body').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()`);
    const GDOCS = '<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1a2b3c"><p dir="ltr" style="line-height:1.38;margin-top:0pt;margin-bottom:0pt;"><span style="font-size:11pt;font-family:Arial,sans-serif;color:#ff0000;background-color:transparent;font-weight:700;vertical-align:baseline;">굵은 글</span><span style="font-size:11pt;font-family:Arial,sans-serif;color:#000000;font-weight:400;">보통 글</span></p><ul style="margin-top:0;"><li dir="ltr" style="list-style-type:disc;font-size:11pt;"><p dir="ltr" role="presentation" style="line-height:1.38;"><span style="font-size:11pt;font-style:italic;">기울인 항목</span></p></li></ul><h1 dir="ltr"><span style="font-size:20pt;font-weight:400">문서 제목</span></h1></b><br class="Apple-interchange-newline">';
    const WORD = `<html xmlns:o="urn:schemas-microsoft-com:office:office"><head><meta name=Generator content="Microsoft Word 15"><style>p.MsoNormal{font-family:"맑은 고딕";color:blue}</style></head><body lang=KO>
<!--StartFragment--><p class=MsoNormal style='margin:0;font-size:12pt'><span lang=EN-US style='font-family:"맑은 고딕";color:blue;text-decoration:underline'>워드 밑줄<o:p></o:p></span></p>
<p class=MsoListParagraphCxSpFirst style='mso-list:l0 level1 lfo1'><![if !supportLists]><span style='font-family:Symbol;mso-list:Ignore'>·<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp;</span></span><![endif]>워드 목록 하나</p>
<p class=MsoListParagraphCxSpLast style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>·</span>워드 목록 둘</p>
<script>window.__xss=1</script><img src=x onerror="window.__xss=2"><table style="border:1px solid red" class=MsoTableGrid><tr><td width=100 style='background:yellow'><p class=MsoNormal>칸</p></td></tr></table><!--EndFragment--></body></html>`;
    await caretEndOf('.mr-de-body blockquote');
    await paste({ 'text/html': GDOCS, 'text/plain': '굵은 글보통 글\n기울인 항목\n문서 제목' });
    await sleep(50);
    await paste({ 'text/html': WORD, 'text/plain': '워드' });
    await sleep(50);
    await paste({ 'text/html': '<p>칩 복사 <sup class="mr-cite" data-chip="f" data-f="f_zzz999" contenteditable="false" style="color:red">9</sup> 끝</p>' });
    await sleep(50);
    const ph = await html();
    const tags = [...new Set([...ph.matchAll(/<([a-z0-9]+)[\s>]/g)].map((m) => m[1]))];
    const allowed = ['h2', 'h3', 'h4', 'p', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'strong', 'em', 'u', 'div', 'blockquote', 'br', 'span'];
    const bad = ['style=', 'font-family', 'color', 'docs-internal-guid', '<b ', '<b>', '<span', 'Mso', 'o:p', 'script', '<img', 'onerror', 'dir=', 'Apple-'].filter((x) => ph.includes(x));
    const checks = {
      bold: ph.includes('<strong>굵은 글</strong>보통 글'),
      italicList: /<ul><li><em>기울인 항목<\/em><\/li><\/ul>/.test(ph),
      h1toH2: ph.includes('<h2>문서 제목'),
      wordU: ph.includes('<u>워드 밑줄</u>'),
      wordList: /<ul><li>워드 목록 하나<\/li><li>워드 목록 둘<\/li><\/ul>/.test(ph),
      table: /<table><tbody><tr><td>칸<\/td><\/tr><\/tbody><\/table>/.test(ph),
      chipToken: ph.includes('칩 복사 [f:f_zzz999] 끝'),
      noXss: (await js('window.__xss')) === undefined,
    };
    const okPaste = !bad.length && tags.every((t) => allowed.includes(t)) && Object.values(checks).every(Boolean);
    step('paste-sanitized', okPaste, `${JSON.stringify(checks)} · 남은 금지어 ${JSON.stringify(bad)} · 태그 ${tags.join(',')}`);
    fs.writeFileSync(path.join(dir, 'after-paste.html'), ph);

    // ⌘B · 제목 단계(⌘⌥2 → h3)
    await js(`(() => { const p = [...document.querySelectorAll('.mr-de-body p')].find(x => x.textContent.startsWith('끝 문단')); const t = p.firstChild; const r = document.createRange(); r.setStart(t, 0); r.setEnd(t, 1); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await key('b', 'KeyB', 66, META);
    await key('2', 'Digit2', 50, META | ALT);
    const fh = await html();
    step('bold-and-heading', fh.includes('<h3><strong>끝</strong> 문단[f:f_abc123]</h3>'), fh.match(/<h3>(?!1\.1)[\s\S]*?<\/h3>/)?.[0]);

    // 목록 — 칩이 든 문단에 ⌘⇧8, 항목 맨 앞 ⌫로 풀기, Tab/⇧Tab 들여쓰기, "- " 자동 글머리
    const caretStartOf = (sel) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); const r = document.createRange(); r.selectNodeContents(el); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await js(`(() => { const p = [...document.querySelectorAll('.mr-de-body p')].find(x => x.textContent.startsWith('회수')); const r = document.createRange(); r.setStart(p.firstChild, 3); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await key('8', 'Digit8', 56, META | SHIFT);
    const l1 = await html();
    await caretStartOf('.mr-de-body > ul:first-of-type > li');
    await key('Backspace', 'Backspace', 8);          // 항목 맨 앞 ⌫ → 글머리만 빠짐
    const l2 = await html();
    await js(`(() => { const li = [...document.querySelectorAll('.mr-de-body li')].find(x => x.textContent.startsWith('할인율')); const r = document.createRange(); r.setStart(li.firstChild, 2); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await key('Tab', 'Tab', 9);
    const l3 = await html();
    await key('Tab', 'Tab', 9, SHIFT);
    const l4 = await html();
    await js(`(() => { const b = document.querySelector('.mr-de-body'); const p = document.createElement('p'); p.innerHTML = '<br>'; b.append(p); const r = document.createRange(); r.setStart(p, 0); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await cdp('Input.insertText', { text: '-' });
    await cdp('Input.insertText', { text: ' ' });
    await cdp('Input.insertText', { text: '자동 항목' });
    const l5 = await html();
    const lists = {
      ulWithChips: /<ul><li>회수 시점 기준 <strong>기본 시나리오 IRR<\/strong>은 \{\{var:exit_irr_base\}\}이며/.test(l1) && !/<p><ul>|<p><h/.test(l1),
      liftBack: /<p>회수 시점 기준/.test(l2) && !/<li>회수/.test(l2),
      indent: /<li>적용 PER 밴드 <em>14 \/ 15 \/ 16배<\/em>\[f:f_def456\]<ul><li>할인율 근거/.test(l3),
      outdent: /\[f:f_def456\]<\/li>\s*<li>할인율 근거/.test(l4),
      autoBullet: /<ul><li>자동 항목<\/li><\/ul>$/.test(l5),
    };
    step('lists', Object.values(lists).every(Boolean), JSON.stringify(lists));
    if (!Object.values(lists).every(Boolean)) fs.writeFileSync(path.join(dir, 'lists.txt'), [l1, l2, l3, l4, l5].join('\n\n=====\n\n'));

    // 표: 마지막 칸에서 Tab → 새 행, 도구 막대에 행 버튼
    const rows0 = await js(`document.querySelectorAll('.mr-de-body table tr').length`);
    await caretEndOf('.mr-de-body table tbody tr:last-child td:last-child');
    await key('Tab', 'Tab', 9);
    await sleep(50);
    const rows1 = await js(`document.querySelectorAll('.mr-de-body table tr').length`);
    await cdp('Input.insertText', { text: '새 행' });
    await sleep(50);
    const bar = await js(`[...document.querySelectorAll('.mr-de-bar button')].map(b => b.textContent).join('|')`);
    const th = await html();
    step('table-tab-new-row', rows1 === rows0 + 1 && /<tr><th>새 행<\/th><td class="num"><br><\/td><td class="num"><br><\/td><\/tr>/.test(th) && bar.includes('행 삭제'), `행 ${rows0}→${rows1} · 막대: ${bar}`);

    // 최종 저장(⌘S)이 제한 HTML이고 토큰이 살아 있는지
    await key('s', 'KeyS', 83, META);
    await sleep(50);
    const fin = await lastSave();
    step('final-save-tokens', /\{\{var:ev_low\}\}/.test(fin) && /\[f:f_def456\]/.test(fin) && /\[확인 필요: 코스닥 신규 상장 할인율 출처\]/.test(fin) && !/data-chip|contenteditable|mr-neg/.test(fin), `${fin.length}자`);
    fs.writeFileSync(path.join(dir, 'final.html'), fin);

    win.setSize(1000, 1300);
    const img = await wc.capturePage();
    if (!img.isEmpty()) fs.writeFileSync(path.join(dir, 'doceditor.png'), img.toPNG());
    code = results.every((r) => r.ok) ? 0 : 1;
  } catch (e) {
    step('selftest-crash', false, String(e.stack || e));
  }
  fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify({ dir, results }, null, 2));
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} 통과 · ${dir}`);
  app.exit(code);
});
