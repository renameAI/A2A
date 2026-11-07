// 메리 투자심사보고서 — 섹션 본문 편집기.
// 종이(.mr-page .mr-doc) 안에서 그대로 고친다: 입력·삭제·선택·되돌리기·굵게/기울임/밑줄·제목 단계·목록·표 행.
// 글 속 표시 셋은 통째로 지워지는 칩으로 보여 주고, 저장할 때 원래 글자로 되돌린다.
//   [f:근거id]      → 위첨자 근거 번호(누르면 onCite)
//   {{var:이름}}    → 계산값 vars[이름].display — 안을 고칠 수 없다
//   [확인 필요: …]  → 빨간 표시 — 콜론 뒤 글만 고칠 수 있다
// Google Docs에서 빌린 동작과 이유는 DocEditor.README.md.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import './doc-editor.css';

// ── 허용 목록(Merry.jsx sanitize와 같은 범위) ──
const TAGS = new Set(['H2', 'H3', 'H4', 'P', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'STRONG', 'EM', 'U', 'DIV', 'BLOCKQUOTE', 'BR', 'SPAN']);
const ALIAS = { B: 'STRONG', I: 'EM', H1: 'H2', H5: 'H4', H6: 'H4' };          // 붙여넣기·execCommand가 만드는 태그
const DROP = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'TITLE', 'HEAD', 'IMG', 'PICTURE', 'VIDEO', 'AUDIO', 'CANVAS', 'SVG', 'MATH', 'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'TEMPLATE', 'NOSCRIPT', 'O:P', 'COLGROUP', 'COL', 'XML']);
const CLASSES = { DIV: ['box', 'caption'], TD: ['num'], TH: ['num'] };
const BLOCKS = new Set(['H2', 'H3', 'H4', 'P', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'DIV', 'BLOCKQUOTE']);
const MARKS = new Set(['STRONG', 'EM', 'U']);
const TOKEN = /\[f:([a-z0-9_]+)\]|\{\{var:([a-z0-9_]+)\}\}|\[확인 필요(?::\s*([^\]]*))?\]/g;
const MIME = 'application/x-merry-doc';                                          // 편집기끼리 복사할 때 표시 글자 그대로(Docs의 document-slice-clip과 같은 구실)
const IS_MAC = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform);

const isChip = (n) => n?.nodeType === 1 && n.hasAttribute('data-chip');
const tagOf = (el) => ALIAS[el.tagName.toUpperCase()] || el.tagName.toUpperCase();
const hasBlock = (el) => [...el.querySelectorAll('*')].some((x) => BLOCKS.has(tagOf(x)));
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, ' ');

function parse(html) {
  const t = document.createElement('template');   // template 안은 스크립트·이미지가 돌지 않는다
  t.innerHTML = html || '';
  return t.content;
}

function tbdToken(t) {
  t = t.replace(/\]/g, '］').replace(/\s+/g, ' ').trim();
  return t ? `[확인 필요: ${t}]` : '[확인 필요]';
}
// 칩(또는 메리 보기 화면에서 복사해 온 표시)을 원래 글자 표시로
function tokenOf(el) {
  const k = el.getAttribute('data-chip');
  if (k === 'f' || (el.tagName === 'SUP' && el.dataset.f)) return /^[a-z0-9_]+$/.test(el.dataset.f || '') ? `[f:${el.dataset.f}]` : '';
  if (k === 'var') return /^[a-z0-9_]+$/.test(el.dataset.var || '') ? `{{var:${el.dataset.var}}}` : '';
  if (k === 'tbd') return tbdToken(el.querySelector('.mr-tbd-t')?.textContent || '');
  if (el.classList?.contains('mr-tbd')) {
    const m = el.textContent.match(/^\s*확인 필요(?::\s*([\s\S]*))?$/);
    if (m) return tbdToken(m[1] || '');
  }
  return null;
}

// ── 정리: 허용한 태그·속성만 남긴다. paste면 Word·Docs 서식(style)을 굵게·기울임·밑줄로만 옮긴다 ──
function clean(parent, paste) {
  const doc = parent.ownerDocument;
  for (const c of [...parent.childNodes]) {
    if (c.nodeType === 3) continue;
    if (c.nodeType !== 1) { c.remove(); continue; }
    const tok = tokenOf(c);
    if (tok != null) { c.replaceWith(tok); continue; }
    const st = paste ? (c.getAttribute('style') || '').toLowerCase() : '';
    let tag = c.tagName.toUpperCase();
    if (DROP.has(tag) || /mso-list:\s*ignore/.test(st)) { c.remove(); continue; }   // Word 글머리 기호 글자
    // Google Docs는 복사한 HTML 전체를 <b style="font-weight:normal" id="docs-internal-guid-…">로 감싼다 — 굵게가 아니다
    if ((tag === 'B' || tag === 'STRONG') && /font-weight:\s*(normal|[1-4]00)\b/.test(st)) tag = 'SPAN';
    tag = ALIAS[tag] || tag;
    const wordLi = paste && /MsoListParagraph/i.test(c.getAttribute('class') || '');
    clean(c, paste);
    if (paste && /^(TR|TD|TH|THEAD|TBODY)$/.test(tag) && !c.parentElement?.closest('table')) { c.replaceWith(...c.childNodes, ' '); continue; }
    // style 서식 → 표시 태그(제목·머리 칸은 원래 굵으니 굵게는 빼고)
    if (paste && st) {
      const wrap = [];
      if (tag !== 'STRONG' && !/^(H2|H3|H4|TH)$/.test(tag) && /font-weight:\s*(bold|[6-9]00)\b/.test(st)) wrap.push('strong');
      if (tag !== 'EM' && /font-style:\s*italic/.test(st)) wrap.push('em');
      if (tag !== 'U' && /text-decoration[^;]*underline/.test(st)) wrap.push('u');
      if (wrap.length && c.firstChild) {
        const outer = doc.createElement(wrap[0]);
        let inner = outer;
        for (const w of wrap.slice(1)) { const n = doc.createElement(w); inner.append(n); inner = n; }
        inner.append(...c.childNodes);
        c.append(outer);
      }
    }
    const cls = (CLASSES[tag] || []).filter((x) => c.classList.contains(x));
    if (!TAGS.has(tag) || tag === 'SPAN' || (tag === 'DIV' && !cls.length && hasBlock(c))) { c.replaceWith(...c.childNodes); continue; }
    if (tag === 'DIV' && !cls.length) tag = 'P';
    // Docs·Word는 목록 항목·표 칸 안에 <p>를 둔다 — 줄바꿈으로 편다
    if (paste && tag === 'P' && /^(LI|TD|TH)$/.test(parent.tagName || '')) {
      const kids = [...c.childNodes];
      if (c.nextElementSibling) kids.push(doc.createElement('br'));
      c.replaceWith(...kids);
      continue;
    }
    if (MARKS.has(tag) && !c.textContent && !c.querySelector('br')) { c.remove(); continue; }
    const n = doc.createElement(tag.toLowerCase());
    if (cls.length) n.className = cls.join(' ');
    for (const a of ['colspan', 'rowspan']) if (/^\d{1,2}$/.test(c.getAttribute(a) || '')) n.setAttribute(a, c.getAttribute(a));
    if (wordLi) n.setAttribute('data-li', '');
    n.append(...c.childNodes);
    c.replaceWith(n);
  }
}
// Word 목록 문단(MsoListParagraph…)이 이어지면 글머리 목록으로 묶는다
function groupWordLists(root) {
  for (const p of [...root.querySelectorAll('p[data-li]')]) {
    if (!p.isConnected && !p.parentNode) continue;
    const prev = p.previousElementSibling;
    const li = p.ownerDocument.createElement('li');
    li.append(...p.childNodes);
    if (prev?.tagName === 'UL' && prev.hasAttribute('data-li')) { prev.append(li); p.remove(); continue; }
    const ul = p.ownerDocument.createElement('ul');
    ul.setAttribute('data-li', '');
    ul.append(li);
    p.replaceWith(ul);
  }
  for (const x of root.querySelectorAll('[data-li]')) x.removeAttribute('data-li');
}

// ── 글 표시 → 칩 ──
function paintVar(el, vars) {
  const name = el.dataset.var;
  const v = vars?.[name];
  const d = v && typeof v === 'object' ? v.display : v;
  const ok = d != null && d !== '';
  el.textContent = ok ? String(d) : name;
  el.classList.toggle('mr-var-missing', !ok);
  el.title = ok ? `계산값 · ${name}` : `계산값 없음 · ${name}`;
}
function chip(doc, m, ctx) {
  if (m[1]) {
    const el = doc.createElement('sup');
    el.className = 'mr-cite';
    el.dataset.chip = 'f';
    el.dataset.f = m[1];
    el.setAttribute('contenteditable', 'false');
    el.textContent = String(ctx.citeNo(m[1]));
    el.title = `근거 ${m[1]}`;
    return el;
  }
  if (m[2]) {
    const el = doc.createElement('span');
    el.className = 'mr-var';
    el.dataset.chip = 'var';
    el.dataset.var = m[2];
    el.setAttribute('contenteditable', 'false');
    paintVar(el, ctx.vars);
    return el;
  }
  const el = doc.createElement('span');
  el.className = 'mr-tbd';
  el.dataset.chip = 'tbd';
  el.setAttribute('contenteditable', 'false');
  const t = doc.createElement('span');
  t.className = 'mr-tbd-t';
  t.setAttribute('contenteditable', ctx.editable ? 'plaintext-only' : 'false');
  t.textContent = (m[3] || '').trim();
  el.append('확인 필요', t);
  return el;
}
function chipify(root, ctx) {
  const doc = root.ownerDocument;
  const tw = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts = [];
  while (tw.nextNode()) if (!tw.currentNode.parentElement?.closest('[data-chip]')) texts.push(tw.currentNode);
  for (const t of texts) {
    const s = t.nodeValue;
    TOKEN.lastIndex = 0;
    if (!TOKEN.test(s)) continue;
    TOKEN.lastIndex = 0;
    const frag = doc.createDocumentFragment();
    let last = 0, m;
    while ((m = TOKEN.exec(s))) {
      if (m.index > last) frag.append(s.slice(last, m.index));
      frag.append(chip(doc, m, ctx));
      last = TOKEN.lastIndex;
    }
    if (last < s.length) frag.append(s.slice(last));
    t.replaceWith(frag);
  }
}
function toEditable(html, ctx, paste = false) {
  const f = parse(html);
  clean(f, paste);
  if (paste) groupWordLists(f);
  chipify(f, ctx);
  // 음수 숫자 칸은 빨간 기울임(보기 화면과 같게) — 저장할 때는 빠진다
  for (const td of f.querySelectorAll('td')) if (/^\s*[(-−][\d,.]+\)?\s*$/.test(td.textContent)) td.classList.add('mr-neg');
  return f;
}

// ── 칩 → 글 표시: 제한 HTML로 저장 ──
function serNode(n) {
  if (n.nodeType === 3) return esc(n.nodeValue);
  if (n.nodeType !== 1) return '';
  const tok = tokenOf(n);
  if (tok != null) return tok;
  let tag = tagOf(n);
  if (DROP.has(tag)) return '';
  if (tag === 'BR') return '<br>';
  const cls = (CLASSES[tag] || []).filter((x) => n.classList.contains(x));
  if (!TAGS.has(tag) || tag === 'SPAN') return serKids(n, false);
  if (tag === 'DIV' && !cls.length) {
    if (hasBlock(n)) return serKids(n, true);
    tag = 'P';
  }
  const inner = serKids(n, false);
  if (MARKS.has(tag) && !inner.trim()) return inner;          // 빈 굵게 따위는 버린다
  let at = cls.length ? ` class="${cls.join(' ')}"` : '';
  for (const a of ['colspan', 'rowspan']) if (/^\d{1,2}$/.test(n.getAttribute(a) || '')) at += ` ${a}="${n.getAttribute(a)}"`;
  const t = tag.toLowerCase();
  return `<${t}${at}>${inner}</${t}>`;
}
// top이면 블록 사이에 떠 있는 글을 <p>로 묶는다(모든 글은 문단 안에 — Docs와 같음)
function serKids(node, top) {
  let out = '', run = [];
  const flush = () => {
    const inner = run.map(serNode).join('');
    out += inner.replace(/<br>/g, '').trim() ? `<p>${inner}</p>` : inner.replace(/<br>/g, '');
    run = [];
  };
  for (const c of node.childNodes) {
    if (!top) { out += serNode(c); continue; }
    const block = c.nodeType === 1 && !isChip(c) && (BLOCKS.has(tagOf(c)) || hasBlock(c));
    const blank = c.nodeType === 3 && !c.nodeValue.trim();
    if (block || (blank && !run.length)) { flush(); out += block && !BLOCKS.has(tagOf(c)) ? serKids(c, true) : serNode(c); } else run.push(c);
  }
  if (top) flush();
  return out;
}
function serializeRoot(root) {
  const h = serKids(root, true);
  return h === '<p><br></p>' ? '' : h;
}

/** 붙여넣은 HTML(Word·Google Docs·웹)을 제한 HTML로. 서식은 굵게·기울임·밑줄·제목·목록·표만 남는다. */
export function cleanPaste(html) {
  const f = parse(html);
  clean(f, true);
  groupWordLists(f);
  const box = document.createElement('div');
  box.append(f);
  return serializeRoot(box);
}
/** 제한 HTML을 편집기가 저장하는 모양으로 맞춘다(속성 순서·빈 태그·떠 있는 글). */
export function normalizeHtml(html) {
  const box = document.createElement('div');
  box.append(toEditable(html, { vars: {}, citeNo: () => 1, editable: false }));
  return serializeRoot(box);
}

// ── 캐럿 위치를 글자 수로(구조가 바뀌어도 되돌릴 수 있게). 블록에 들어갈 때마다 1을 더해 문단 경계를 구분한다 ──
function linear(root, node, off) {
  let n = 0, found = null;
  const walk = (el) => {
    const kids = el.childNodes;
    for (let i = 0; i < kids.length; i++) {
      if (el === node && i === off) { found = n; return true; }
      const c = kids[i];
      if (c === node && c.nodeType === 3) { found = n + off; return true; }
      if (c.nodeType === 3) n += c.length;
      else if (c.nodeType === 1) {
        if (c.tagName === 'BR') n += 1;
        else { if (BLOCKS.has(c.tagName)) n += 1; if (walk(c)) return true; }
      }
    }
    if (el === node) { found = n; return true; }
    return false;
  };
  walk(root);
  return found ?? n;
}
function locate(root, pos) {
  let n = 0, hit = null;
  const walk = (el) => {
    const kids = el.childNodes;
    for (let i = 0; i < kids.length; i++) {
      const c = kids[i];
      if (c.nodeType === 3) {
        if (pos <= n + c.length) { hit = [c, pos - n]; return true; }
        n += c.length;
      } else if (c.nodeType === 1) {
        if (c.tagName === 'BR') {
          if (pos <= n) { hit = [el, i]; return true; }
          n += 1;
        } else if (isChip(c) && c.dataset.chip !== 'tbd') {        // 칩 안으로는 캐럿을 넣지 않는다
          const len = c.textContent.length;
          if (pos < n + len) { hit = [el, pos <= n ? i : i + 1]; return true; }
          n += len;
        } else {
          const block = BLOCKS.has(c.tagName);
          if (block) n += 1;
          if (walk(c)) return true;
          if (block && pos <= n) { hit = [c, c.childNodes.length]; return true; }
        }
      }
    }
    return false;
  };
  if (!walk(root)) hit = [root, root.childNodes.length];
  return hit;
}

// ── 편집기 본체(명령형). React는 바깥 틀과 떠 있는 도구 막대만 그린다 — 본문 DOM은 이 쪽만 만진다 ──
function controller(rootRef, wrapRef, propsRef, setBar) {
  const s = {
    base: { raw: null, canon: '' }, saved: null, dirty: false, composing: false, wantSave: false,
    pending: null, pendingSaved: null, stack: [], i: -1, typingT: 0, saveT: 0, plain: false, focused: false, cites: new Map(),
  };
  const root = () => rootRef.current || s.el;     // 내려간 뒤(ref가 비어도) 마지막 저장을 하려고 붙잡아 둔다
  const P = () => propsRef.current;
  const ctx = () => ({
    vars: P().vars,
    citeNo: P().citeNo || ((id) => { if (!s.cites.has(id)) s.cites.set(id, s.cites.size + 1); return s.cites.get(id); }),
    editable: !P().readOnly,
  });
  const select = (range) => { const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); };
  const anchorEl = () => { const n = getSelection().anchorNode; return n?.nodeType === 1 ? n : n?.parentElement; };
  const inside = (el) => el && root()?.contains(el) ? el : null;
  const cellAt = () => inside(anchorEl()?.closest('td,th'));
  const blockAt = () => inside(anchorEl()?.closest('h2,h3,h4,p,li,blockquote,div,td,th'));
  const tbdHost = () => inside(anchorEl()?.closest('.mr-tbd-t'));
  const caretIn = (el, end) => { const r = document.createRange(); r.selectNodeContents(el); r.collapse(!end); select(r); };

  function getSel() {
    const sel = getSelection();
    const r = root();
    if (!sel.rangeCount || !r) return null;
    const g = sel.getRangeAt(0);
    if (!r.contains(g.startContainer)) return null;
    return { a: linear(r, g.startContainer, g.startOffset), b: linear(r, g.endContainer, g.endOffset) };
  }
  function setSel(p) {
    const r = root();
    if (!p || !r) return;
    try {
      const [an, ao] = locate(r, p.a), [bn, bo] = locate(r, p.b);
      const g = document.createRange();
      g.setStart(an, ao);
      g.setEnd(bn, bo);
      select(g);
    } catch { /* 위치가 사라졌으면 그냥 둔다 */ }
  }

  // 크롬 contenteditable이 남기는 것(style 붙은 span, <b>, <div>, 주석)을 제자리에서 정리. 바뀐 게 있을 때만 캐럿을 다시 놓는다
  function tidy() {
    const r = root();
    if (!r || s.composing) return;
    const sel = getSel();
    let changed = false;
    const visit = (el) => {
      for (const c of [...el.childNodes]) {
        if (c.nodeType === 8) { c.remove(); changed = true; continue; }
        if (c.nodeType !== 1) continue;
        if (isChip(c)) { if (c.hasAttribute('style')) { c.removeAttribute('style'); changed = true; } continue; }
        visit(c);
        const tag = c.tagName;
        let want = ALIAS[tag] || tag;
        if (DROP.has(want)) { c.remove(); changed = true; continue; }
        const div = want === 'DIV' && !c.classList.contains('box') && !c.classList.contains('caption');
        if (!TAGS.has(want) || want === 'SPAN' || (div && hasBlock(c))) { c.replaceWith(...c.childNodes); changed = true; continue; }
        if (div) want = 'P';
        if (want !== tag) {
          const n = document.createElement(want.toLowerCase());
          for (const a of ['class', 'colspan', 'rowspan']) if (c.hasAttribute(a)) n.setAttribute(a, c.getAttribute(a));
          n.append(...c.childNodes);
          c.replaceWith(n);
          changed = true;
          continue;
        }
        for (const a of [...c.attributes]) if (!['class', 'colspan', 'rowspan'].includes(a.name)) { c.removeAttribute(a.name); changed = true; }
      }
    };
    visit(r);
    // 문단 밖에 떠 있는 글은 문단으로
    let run = [];
    const wrap = () => {
      if (run.some((x) => x.nodeType === 1 || x.nodeValue.trim())) {
        const p = document.createElement('p');
        run[0].before(p);
        p.append(...run);
        changed = true;
      }
      run = [];
    };
    for (const c of [...r.childNodes]) {
      if (c.nodeType === 1 && BLOCKS.has(c.tagName)) wrap(); else run.push(c);
    }
    wrap();
    if (!r.firstChild) { r.innerHTML = '<p><br></p>'; changed = true; }
    if (changed && sel) setSel(sel);
  }

  // ── 되돌리기: 스냅숏(본문 HTML + 캐럿). 타자는 0.7초·문단 단위로 묶는다(Docs처럼 글자마다가 아니라) ──
  const snap = () => ({ html: root().innerHTML, sel: getSel() });
  function commit() {
    clearTimeout(s.typingT);
    s.typingT = 0;
    if (!root()) return;
    const n = snap(), cur = s.stack[s.i];
    if (cur && cur.html === n.html) { cur.sel = n.sel || cur.sel; return; }
    s.stack = s.stack.slice(0, s.i + 1);
    s.stack.push(n);
    if (s.stack.length > 300) s.stack.shift();
    s.i = s.stack.length - 1;
  }
  function markStart() {      // 새 타자 묶음이 시작되는 자리 — 되돌리면 캐럿이 여기로 온다
    const cur = s.stack[s.i];
    if (!s.typingT && cur && root().innerHTML === cur.html) cur.sel = getSel() || cur.sel;
  }
  function restore(st) {
    root().innerHTML = st.html;
    setSel(st.sel);
    changed();
    updateBar();
  }
  function undo() {
    if (s.composing) return;
    if (root().innerHTML !== s.stack[s.i]?.html) commit();
    if (s.i > 0) restore(s.stack[--s.i]);
  }
  function redo() {
    if (s.composing) return;
    if (root().innerHTML !== s.stack[s.i]?.html) commit();
    if (s.i < s.stack.length - 1) restore(s.stack[++s.i]);
  }

  // ── 저장: 1.5초 쉬면·포커스를 잃으면·⌘S. 고친 게 없으면 받은 글자 그대로 돌려준다 ──
  const out = () => { const c = serializeRoot(root()); return c === s.base.canon ? s.base.raw : c; };
  function flush(force) {
    clearTimeout(s.saveT);
    if (!root()) return;
    if (s.composing) { s.wantSave = true; return; }      // 조합 중인 글자는 아직 확정 전
    s.wantSave = false;
    const h = out();
    s.dirty = false;
    if (!force && h === s.saved) return;
    s.saved = h;
    P().onSave?.(h);
  }
  function changed() {
    s.dirty = true;
    clearTimeout(s.saveT);
    s.saveT = setTimeout(() => flush(false), 1500);
  }

  function render(html) {
    const r = root();
    s.cites = new Map();
    r.replaceChildren(toEditable(html, ctx()));
    if (!r.textContent.trim() && !r.querySelector('br,td,th')) r.innerHTML = '<p><br></p>';
    s.base = { raw: html, canon: serializeRoot(r) };
    s.saved = html;
    s.dirty = false;
    s.pending = null;
    s.stack = [{ html: r.innerHTML, sel: null }];
    s.i = 0;
  }
  function sync(html) {
    if (s.base.raw !== null && html === s.saved) return;        // 우리가 저장한 글이 되돌아온 것
    if (s.focused && s.dirty) { s.pending = html; s.pendingSaved = s.saved; return; }   // 고치는 중이면 손 글이 이긴다 — 포커스가 빠질 때 다시 본다
    render(html);
  }

  // ── 명령 ──
  function run(kind, arg) {
    const r = root();
    if (!r || P().readOnly) return;
    if (!r.contains(getSelection().anchorNode)) r.focus();
    commit();
    if (kind === 'mark') document.execCommand(arg);
    else if (kind === 'block') setBlock(arg);
    else if (kind === 'list') toggleList(arg);
    else if (kind === 'indent') keepSel(() => { for (const li of selectedBlocks().filter((b) => b.tagName === 'LI')) (arg < 0 ? (nestedLi(li) && outdentLi(li)) : indentLi(li)); });
    else if (kind === 'lift') keepSel(() => (nestedLi(arg) ? outdentLi(arg) : liftTop(arg, 'P')));
    else if (kind === 'row') addRow(arg === 'below');
    else if (kind === 'delrow') delRow();
    tidy();
    commit();
    changed();
    updateBar();
  }
  // ── 블록 명령 — 크롬 formatBlock·insertUnorderedList는 칩(contenteditable=false)이 든 문단을
  //    <p><h3>…</h3><sup>…</sup></p>, <p><ul>…</ul></p>처럼 깨뜨려서 직접 한다(Docs도 명령을 DOM이 아니라 문서 모델에 적용한다) ──
  const TEXT_BLOCKS = 'p,h2,h3,h4,li,blockquote';
  const isList = (n) => n?.nodeType === 1 && /^(UL|OL)$/.test(n.tagName);
  const moved = new Map();               // 바꿔 끼운 요소 → 새 요소(캐럿을 따라 옮기려고)
  const len = (n) => (n.nodeType === 3 ? n.length : n.childNodes.length);
  function keepSel(fn) {
    const sel = getSelection();
    const g = sel.rangeCount ? sel.getRangeAt(0) : null;
    const pts = g && [g.startContainer, g.startOffset, g.endContainer, g.endOffset];
    moved.clear();
    fn();
    if (!pts) return;
    const fix = (n) => { while (moved.has(n)) n = moved.get(n); return n; };
    const a = fix(pts[0]), b = fix(pts[2]);
    if (!a.isConnected || !b.isConnected) return;
    try {
      const ng = document.createRange();
      ng.setStart(a, Math.min(pts[1], len(a)));
      ng.setEnd(b, Math.min(pts[3], len(b)));
      select(ng);
    } catch { /* 캐럿 자리가 사라졌으면 그냥 둔다 */ }
  }
  function selectedBlocks() {
    const r = root(), sel = getSelection();
    if (!sel.rangeCount) return [];
    const g = sel.getRangeAt(0);
    const ok = (b) => b && r.contains(b) && b !== r && !b.closest('td,th');
    if (g.collapsed) {
      const n = g.startContainer;
      const b = (n.nodeType === 1 ? n : n.parentElement)?.closest(TEXT_BLOCKS);
      return ok(b) ? [b] : [];
    }
    // 목록 항목은 제 글(안쪽 목록 말고)이 걸릴 때만
    return [...r.querySelectorAll(TEXT_BLOCKS)].filter((b) => ok(b) && g.intersectsNode(b)
      && (b.tagName !== 'LI' || [...b.childNodes].some((n) => !isList(n) && g.intersectsNode(n))));
  }
  function retag(el, tag) {
    const n = document.createElement(tag.toLowerCase());
    n.append(...el.childNodes);
    el.replaceWith(n);
    moved.set(el, n);
    return n;
  }
  const nestedLi = (li) => li.parentElement?.parentElement?.tagName === 'LI';
  const siblingsAfter = (el) => { const a = []; for (let x = el.nextElementSibling; x; x = x.nextElementSibling) a.push(x); return a; };
  function outdentLi(li) {              // 한 단계 밖으로 — 뒤 항목들은 이 항목의 안쪽 목록으로
    const list = li.parentElement, pli = list.parentElement;
    const after = siblingsAfter(li);
    if (after.length) {
      let sub = [...li.children].find((c) => c.tagName === list.tagName);
      if (!sub) { sub = document.createElement(list.tagName.toLowerCase()); li.append(sub); }
      sub.append(...after);
    }
    pli.after(li);
    if (!list.children.length) list.remove();
    return li;
  }
  function indentLi(li) {               // 앞 항목의 안쪽 목록으로
    const prev = li.previousElementSibling;
    if (prev?.tagName !== 'LI') return;
    let sub = prev.lastElementChild;
    if (!isList(sub)) { sub = document.createElement(li.parentElement.tagName.toLowerCase()); prev.append(sub); }
    sub.append(li);
  }
  function liftTop(li, tag) {           // 바깥 목록의 항목을 문단·제목으로 — 목록은 그 자리에서 둘로 나뉜다
    const list = li.parentElement;
    const blk = document.createElement(tag.toLowerCase());
    const subs = [...li.children].filter(isList);
    for (const s of subs) s.remove();
    blk.append(...li.childNodes);
    if (!blk.firstChild) blk.append(document.createElement('br'));
    const after = siblingsAfter(li);
    li.remove();
    list.after(blk);
    let tail = blk;
    for (const s of subs) { tail.after(s); tail = s; }
    if (after.length) { const nl = document.createElement(list.tagName.toLowerCase()); nl.append(...after); tail.after(nl); }
    if (!list.children.length) list.remove();
    moved.set(li, blk);
    return blk;
  }
  function setBlock(tag) {
    const bs = selectedBlocks();
    if (!bs.length) return;
    const t = tag !== 'P' && bs.every((b) => b.tagName === tag) ? 'P' : tag;      // 같은 단계를 한 번 더 → 본문
    keepSel(() => {
      for (const b of bs) {
        if (b.tagName === 'LI') {                       // 목록 항목은 제목이 될 때만 목록에서 빠진다
          if (t === 'P') continue;
          let li = b;
          while (nestedLi(li)) li = outdentLi(li);
          liftTop(li, t);
        } else if (b.tagName !== t) retag(b, t);
      }
    });
  }
  function toggleList(type) {
    const bs = selectedBlocks();
    if (!bs.length) return;
    keepSel(() => {
      if (bs.every((b) => b.tagName === 'LI' && b.parentElement.tagName === type)) {    // 이미 그 목록 → 목록 풀기
        for (const b of bs) { let li = b; while (nestedLi(li)) li = outdentLi(li); liftTop(li, 'P'); }
        return;
      }
      let list = null;
      for (const b of bs) {
        if (b.tagName === 'LI') { if (b.parentElement.tagName !== type) retag(b.parentElement, type); list = null; continue; }
        const li = document.createElement('li');
        li.append(...b.childNodes);
        moved.set(b, li);
        const prev = b.previousElementSibling;
        if (prev?.tagName === type && (!list || prev === list)) { prev.append(li); b.remove(); list = prev; continue; }   // 바로 앞 목록에 잇는다
        list = document.createElement(type.toLowerCase());
        list.append(li);
        b.replaceWith(list);
      }
      const nx = list?.nextElementSibling;
      if (nx?.tagName === type) { list.append(...nx.children); nx.remove(); }
    });
  }
  // 캐럿이 블록 맨 앞인가(사이에 글·칩·줄바꿈이 없다)
  function atStart(blk) {
    const sel = getSelection();
    if (!sel.isCollapsed) return false;
    const g = document.createRange();
    g.selectNodeContents(blk);
    g.setEnd(sel.anchorNode, sel.anchorOffset);
    return !g.toString() && !g.cloneContents().querySelector('[data-chip],br');
  }

  function addRow(below) {
    const cell = cellAt();
    if (!cell) return;
    const tr = cell.closest('tr'), table = tr.closest('table');
    let target = tr, place = below ? 'afterend' : 'beforebegin', tag = null;
    if (tr.parentElement.tagName === 'THEAD' && below) {      // 머리 행 아래 = 본문 첫 행
      let body = table.querySelector(':scope > tbody');
      if (!body) { body = document.createElement('tbody'); table.append(body); }
      target = body; place = 'afterbegin'; tag = 'td';
    }
    const nr = document.createElement('tr');
    for (const c of tr.children) {
      const n = document.createElement(tag || c.tagName.toLowerCase());
      if (c.getAttribute('colspan')) n.setAttribute('colspan', c.getAttribute('colspan'));
      if (c.classList.contains('num')) n.className = 'num';
      n.append(document.createElement('br'));
      nr.append(n);
    }
    target.insertAdjacentElement(place, nr);
    caretIn(nr.firstElementChild, false);
  }
  function delRow() {
    const cell = cellAt();
    if (!cell) return;
    const tr = cell.closest('tr'), table = tr.closest('table');
    const next = tr.nextElementSibling || tr.previousElementSibling;
    tr.remove();
    for (const sec of table.querySelectorAll(':scope > thead, :scope > tbody')) if (!sec.querySelector('tr')) sec.remove();
    if (!table.querySelector('tr')) {
      const p = document.createElement('p');
      p.append(document.createElement('br'));
      table.replaceWith(p);
      caretIn(p, false);
    } else caretIn((next?.isConnected ? next : table.querySelector('tr')).firstElementChild, true);
  }
  function tab(back) {
    const cell = cellAt();
    if (cell) {             // Docs: Tab은 다음 칸, 마지막 칸에서는 새 행
      const table = cell.closest('table');
      const cells = [...table.querySelectorAll('th,td')].filter((c) => c.closest('table') === table);
      const nx = cells[cells.indexOf(cell) + (back ? -1 : 1)];
      if (nx) caretIn(nx, true);
      else if (!back) run('row', 'below');
      updateBar();
      return;
    }
    if (inside(anchorEl()?.closest('li'))) run('indent', back ? -1 : 1);
  }
  // Docs 자동 서식: 문단 맨 앞 "- " · "* " → 글머리, "1. " → 번호 목록. ⌘Z 한 번이면 "- "로 돌아간다
  function autoList() {
    const sel = getSelection();
    const blk = blockAt();
    if (!sel.isCollapsed || !blk || blk.tagName !== 'P' || blk.closest('li,td,th')) return false;
    const m = /^([-*•]|1[.)])[\s ]$/.exec(blk.textContent);
    if (!m) return false;
    commit();
    blk.replaceChildren(document.createElement('br'));
    caretIn(blk, false);
    toggleList(m[1].startsWith('1') ? 'OL' : 'UL');
    tidy(); commit(); changed(); updateBar();
    return true;
  }

  // 캐럿 바로 앞(dir -1)·뒤(+1)의 칩
  function chipNear(node, off, dir) {
    const r = root();
    let cand = null;
    if (node.nodeType === 3) {
      if (dir < 0 ? off > 0 : off < node.length) return null;
      cand = dir < 0 ? node.previousSibling : node.nextSibling;
      let p = node.parentNode;
      while (!cand && p && p !== r && MARKS.has(tagOf(p))) { cand = dir < 0 ? p.previousSibling : p.nextSibling; p = p.parentNode; }
    } else cand = dir < 0 ? node.childNodes[off - 1] : node.childNodes[off];
    while (cand?.nodeType === 3 && cand.nodeValue === '') cand = dir < 0 ? cand.previousSibling : cand.nextSibling;
    while (cand?.nodeType === 1 && MARKS.has(tagOf(cand))) cand = dir < 0 ? cand.lastChild : cand.firstChild;
    return isChip(cand) ? cand : null;
  }
  function removeChip(c) {
    commit();
    const g = document.createRange();
    g.setStartBefore(c);
    g.collapse(true);
    c.remove();
    select(g);
    commit(); changed(); updateBar();
  }
  function deleteChip(dir) {
    const sel = getSelection();
    if (!sel.rangeCount || !sel.isCollapsed) return false;
    const host = tbdHost();
    if (host) {               // 확인 필요 칩 안: 글을 다 지운 뒤 한 번 더 지우면 칩째로
      if (!host.textContent) { removeChip(host.parentElement); return true; }
      return false;
    }
    const c = chipNear(sel.anchorNode, sel.anchorOffset, dir);
    if (!c) return false;
    removeChip(c);
    return true;
  }

  // ── 붙여넣기·끌어 놓기 ──
  function textToHtml(text) {
    const lines = (text || '').replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n');
    const e = (l) => l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return lines.length === 1 ? e(lines[0]) : lines.map((l) => `<p>${e(l) || '<br>'}</p>`).join('');
  }
  function insertInline(range, nodes) {
    if (!nodes.length) return;
    const f = document.createDocumentFragment();
    f.append(...nodes);
    const last = f.lastChild;
    range.insertNode(f);
    const g = document.createRange();
    g.setStartAfter(last);
    g.collapse(true);
    select(g);
  }
  function insertRestricted(html) {
    const frag = toEditable(html, ctx(), true);
    let sel = getSelection();
    if (!sel.rangeCount) return;
    if (!sel.isCollapsed) document.execCommand('delete');
    sel = getSelection();
    let range = sel.getRangeAt(0);
    const empty = blockAt();
    if (empty?.childNodes.length === 1 && empty.firstChild.nodeName === 'BR') {     // 빈 문단 자리표시 <br>은 걷어 낸다
      empty.firstChild.remove();
      range = document.createRange();
      range.setStart(empty, 0);
      range.collapse(true);
      select(range);
    }
    const isBlock = (n) => n.nodeType === 1 && BLOCKS.has(n.tagName);
    const all = [...frag.childNodes];
    if (!all.some(isBlock)) return insertInline(range, all);
    const nodes = all.filter((n) => !(n.nodeType === 3 && !n.nodeValue.trim()));     // 블록 사이 빈 줄
    const blk = blockAt();
    if (!blk || /^(TD|TH)$/.test(blk.tagName) || blk.tagName === 'DIV') {      // 표 칸 안: 문단 대신 줄바꿈
      const inl = [];
      nodes.forEach((n, i) => {
        if (i) inl.push(document.createElement('br'));
        if (isBlock(n)) inl.push(...(/^(P|H2|H3|H4|BLOCKQUOTE)$/.test(n.tagName) ? n.childNodes : [document.createTextNode(n.textContent)]));
        else inl.push(n);
      });
      return insertInline(range, [...inl]);
    }
    // 지금 문단을 캐럿에서 둘로 나누고 그 사이에 넣는다
    const tailR = document.createRange();
    tailR.setStart(range.startContainer, range.startOffset);
    tailR.setEnd(blk, blk.childNodes.length);
    const tail = tailR.extractContents();
    let i = 0;
    while (i < nodes.length && !isBlock(nodes[i])) blk.append(nodes[i++]);
    if (i === 0 && /^(P|H2|H3|H4)$/.test(nodes[0].tagName)) blk.append(...nodes[i++].childNodes);
    let last = blk;
    for (; i < nodes.length; i++) {
      let n = nodes[i];
      if (!isBlock(n)) { const p = document.createElement('p'); p.append(n); n = p; }
      if (last.tagName === 'LI' && /^(UL|OL)$/.test(n.tagName)) {
        const items = [...n.children];
        for (const li of items) { last.after(li); last = li; }
        continue;
      }
      if (last.tagName === 'LI' && n.tagName !== 'LI') { const ul = last.parentElement; ul.after(n); last = n; continue; }
      last.after(n);
      last = n;
    }
    const caret = document.createRange();
    const textual = /^(P|H2|H3|H4|LI|BLOCKQUOTE)$/.test(last.tagName);
    const holder = textual ? last : document.createElement('p');
    if (!textual) last.after(holder);
    caret.setStart(holder, holder.childNodes.length);
    holder.append(tail);
    for (const b of [blk, holder]) if (!b.textContent && !b.querySelector('br,[data-chip]')) b.append(document.createElement('br'));
    caret.collapse(true);
    select(caret);
  }
  function insertData(dt, plain) {
    const mine = dt.getData(MIME), html = dt.getData('text/html'), text = dt.getData('text/plain');
    commit();
    if (tbdHost()) document.execCommand('insertText', false, (text || '').replace(/\s+/g, ' '));
    else if (!plain && mine) insertRestricted(mine);
    else if (!plain && html) insertRestricted(cleanPaste(html));
    else if (text) insertRestricted(textToHtml(text));
    tidy(); commit(); changed(); updateBar();
  }

  // ── 떠 있는 도구 막대: 글을 고르거나 표 안에 있을 때만 ──
  function updateBar() {
    const r = root(), w = wrapRef.current;
    const sel = getSelection();
    if (!r || !w || P().readOnly || !s.focused || s.composing || !sel.rangeCount || !r.contains(sel.anchorNode)) return setBar(null);
    const cell = cellAt();
    if (sel.isCollapsed && !cell) return setBar(null);
    const rect = (sel.isCollapsed ? cell.closest('tr') : sel.getRangeAt(0)).getBoundingClientRect();
    const wr = w.getBoundingClientRect();
    let y = rect.top - wr.top - 40;
    if (rect.top < 48) y = rect.bottom - wr.top + 8;      // 화면 위에 붙으면 아래로
    const blk = blockAt();
    setBar({
      x: Math.max(0, Math.min(rect.left - wr.left, wr.width - 460)), y,
      block: blk?.closest('li') ? 'LI' : blk?.tagName, table: !!cell,
      b: document.queryCommandState('bold'), i: document.queryCommandState('italic'), u: document.queryCommandState('underline'),
    });
  }

  // ── 이벤트 ──
  const MARK_KEYS = { KeyB: 'bold', KeyI: 'italic', KeyU: 'underline' };
  const HEAD_KEYS = { Digit0: 'P', Digit1: 'H2', Digit2: 'H3', Digit3: 'H4' };
  const on = {
    keydown(e) {
      if (P().readOnly) return;
      if (e.isComposing || e.keyCode === 229 || s.composing) return;     // 한글 조합 중 키는 IME 몫
      const mod = IS_MAC ? e.metaKey : e.ctrlKey, k = e.code;            // e.key는 한글 자판에서 'ㅠ'가 되므로 e.code로
      if (mod && !e.altKey && k === 'KeyZ') { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
      if (mod && !e.altKey && !e.shiftKey && k === 'KeyY') { e.preventDefault(); return redo(); }
      if (mod && !e.altKey && !e.shiftKey && k === 'KeyS') { e.preventDefault(); commit(); return flush(true); }
      if (mod && !e.altKey && !e.shiftKey && MARK_KEYS[k]) { e.preventDefault(); if (!tbdHost()) run('mark', MARK_KEYS[k]); return; }
      if (mod && e.altKey && HEAD_KEYS[k]) { e.preventDefault(); if (!tbdHost()) run('block', HEAD_KEYS[k]); return; }
      if (mod && e.shiftKey && (k === 'Digit7' || k === 'Digit8')) { e.preventDefault(); if (!tbdHost()) run('list', k === 'Digit7' ? 'OL' : 'UL'); return; }
      if (mod && e.shiftKey && k === 'KeyV') { s.plain = true; setTimeout(() => { s.plain = false; }, 800); return; }
      const li = inside(anchorEl()?.closest('li'));
      if (e.key === 'Backspace' && li && !tbdHost() && atStart(li)) { e.preventDefault(); return run('lift', li); }   // Docs: 항목 맨 앞 ⌫ → 글머리만 빠진다
      if (e.key === 'Backspace' || e.key === 'Delete') { if (deleteChip(e.key === 'Backspace' ? -1 : 1)) e.preventDefault(); return; }
      if (e.key === 'Enter') {
        const host = tbdHost();
        if (host) { e.preventDefault(); const g = document.createRange(); g.setStartAfter(host.parentElement); g.collapse(true); root().focus(); select(g); return; }
        if (li && !e.shiftKey && ![...li.childNodes].some((n) => !isList(n) && (n.textContent || n.nodeName !== 'BR' && n.nodeType === 1))) { e.preventDefault(); run('lift', li); }   // 빈 항목에서 ↵ → 목록 끝내기
        return;
      }
      if (e.key === 'Tab') { e.preventDefault(); return tab(e.shiftKey); }
      if (e.key === 'Escape') root().blur();
    },
    beforeinput(e) {
      if (e.inputType === 'historyUndo') { e.preventDefault(); return undo(); }
      if (e.inputType === 'historyRedo') { e.preventDefault(); return redo(); }
      if (!s.composing) markStart();
    },
    input(e) {
      if (P().readOnly) return;
      if (e.isComposing || s.composing) { s.dirty = true; return; }     // 조합 중엔 DOM·저장·되돌리기를 건드리지 않는다
      if (e.inputType === 'insertText' && e.data === ' ' && autoList()) return;
      tidy();
      if (e.inputType === 'insertParagraph') commit();
      else { clearTimeout(s.typingT); s.typingT = setTimeout(commit, 700); }
      changed();
      updateBar();
    },
    compositionstart() {
      markStart();
      s.composing = true;
      clearTimeout(s.saveT);
      setBar(null);
    },
    compositionend() {
      s.composing = false;
      clearTimeout(s.typingT);
      s.typingT = setTimeout(commit, 700);
      if (s.wantSave) flush(false);
      changed();
    },
    paste(e) {
      e.preventDefault();
      if (P().readOnly) return;
      insertData(e.clipboardData, s.plain);
      s.plain = false;
    },
    copy(e) { clip(e, false); },
    cut(e) { clip(e, true); },
    dragstart(e) { e.preventDefault(); },                 // 끌어서 옮기기는 막는다(복사·잘라내기로)
    drop(e) {
      e.preventDefault();
      if (P().readOnly || !e.dataTransfer) return;
      const g = document.caretRangeFromPoint?.(e.clientX, e.clientY);
      if (!g || !root().contains(g.startContainer)) return;
      select(g);
      insertData(e.dataTransfer, false);
    },
    click(e) {
      const f = e.target.closest?.('sup.mr-cite');
      if (f && root().contains(f)) { e.preventDefault(); P().onCite?.(f.dataset.f); }
    },
    focusin() {
      if (s.focused) return;
      s.focused = true;
      document.execCommand('defaultParagraphSeparator', false, 'p');
      document.execCommand('styleWithCSS', false, false);
    },
    focusout(e) {
      if (root().contains(e.relatedTarget) || wrapRef.current?.contains(e.relatedTarget)) return;
      s.focused = false;
      commit();
      flush(false);
      setBar(null);
      if (s.pending != null) {
        const p = s.pending;
        s.pending = null;
        if (s.saved === s.pendingSaved) render(p);
      }
    },
  };
  function clip(e, cut) {
    const sel = getSelection(), r = root();
    if (!sel.rangeCount || sel.isCollapsed || tbdHost()) return;
    const g = sel.getRangeAt(0);
    if (!r.contains(g.commonAncestorContainer)) return;
    const box = document.createElement('div');
    box.append(g.cloneContents());
    e.preventDefault();
    e.clipboardData.setData('text/plain', sel.toString());
    e.clipboardData.setData('text/html', box.innerHTML);
    e.clipboardData.setData(MIME, serializeRoot(box));
    if (cut && !P().readOnly) {
      commit();
      document.execCommand('delete');
      tidy(); commit(); changed();
    }
  }
  function setReadOnly(ro) {
    const r = root();
    r.setAttribute('contenteditable', ro ? 'false' : 'true');
    for (const t of r.querySelectorAll('.mr-tbd-t')) t.setAttribute('contenteditable', ro ? 'false' : 'plaintext-only');
  }
  function repaintVars() { for (const el of root().querySelectorAll('[data-chip="var"]')) paintVar(el, P().vars); }
  function mount(el) { s.el = el; }
  function unmount() { if (s.dirty || s.composing) { s.composing = false; flush(false); } clearTimeout(s.typingT); clearTimeout(s.saveT); }

  return { on, sync, run, flush, commit, undo, redo, setReadOnly, repaintVars, updateBar, mount, unmount, state: s };
}

const BAR = [
  ['block', 'P', '본문', '⌘⌥0'], ['block', 'H2', '절', '⌘⌥1'], ['block', 'H3', '항', '⌘⌥2'], ['block', 'H4', '■ 소제목', '⌘⌥3'],
  null,
  ['mark', 'bold', 'B', '⌘B'], ['mark', 'italic', 'I', '⌘I'], ['mark', 'underline', 'U', '⌘U'],
  null,
  ['list', 'UL', '– 목록', '⌘⇧8'], ['list', 'OL', '1. 목록', '⌘⇧7'],
];

/**
 * 섹션 본문 편집기.
 * @param {string}   html      제한 HTML(토큰 그대로: [f:…] {{var:…}} [확인 필요: …])
 * @param {Function} onSave    (html) => void — 1.5초 쉼·포커스 빠짐·⌘S. 바뀐 게 없으면 포커스 빠짐·쉼에는 부르지 않는다
 * @param {boolean}  readOnly
 * @param {object}   vars      { 이름: { display } }
 * @param {Function} onCite    (factId) => void
 * @param {Function} [citeNo]  (factId) => 번호 — 보고서 전체 근거 번호를 맞추려면 넘긴다(없으면 섹션 안 순서)
 */
export default function DocEditor({ html, onSave, readOnly = false, vars, onCite, citeNo }) {
  const rootRef = useRef(null), wrapRef = useRef(null);
  const props = useRef({});
  props.current = { onSave, onCite, vars, citeNo, readOnly };
  const [bar, setBar] = useState(null);
  const E = useMemo(() => controller(rootRef, wrapRef, props, setBar), []);

  useLayoutEffect(() => { E.sync(html ?? ''); }, [E, html]);
  useLayoutEffect(() => { E.setReadOnly(readOnly); }, [E, readOnly]);
  useEffect(() => { E.repaintVars(); }, [E, vars]);
  useEffect(() => {
    const r = rootRef.current;
    E.mount(r);
    const pairs = Object.entries(E.on);
    for (const [t, f] of pairs) r.addEventListener(t, f);
    const onSel = () => { if (E.state.focused) E.updateBar(); };
    document.addEventListener('selectionchange', onSel);
    return () => {
      for (const [t, f] of pairs) r.removeEventListener(t, f);
      document.removeEventListener('selectionchange', onSel);
      E.unmount();
    };
  }, [E]);

  return (
    <div ref={wrapRef} className="mr-de">
      {bar && (
        <div className="mr-de-bar mr-noprint" style={{ left: bar.x, top: bar.y }} onMouseDown={(e) => e.preventDefault()}>
          {BAR.map((b, i) => (b ? (
            <button key={i} type="button" tabIndex={-1} title={`${b[2]} (${b[3]})`}
              className={`${b[0] === 'mark' ? `mr-de-${b[1]}` : ''} ${(b[0] === 'block' && bar.block === b[1]) || (b[0] === 'mark' && bar[b[1][0]]) ? 'on' : ''}`}
              onClick={() => E.run(b[0], b[1])}>{b[2]}</button>
          ) : <span key={i} className="mr-de-sep" />))}
          {bar.table && (
            <>
              <span className="mr-de-sep" />
              <button type="button" tabIndex={-1} title="위에 행 넣기" onClick={() => E.run('row', 'above')}>＋ 위 행</button>
              <button type="button" tabIndex={-1} title="아래에 행 넣기 (마지막 칸에서 Tab)" onClick={() => E.run('row', 'below')}>＋ 아래 행</button>
              <button type="button" tabIndex={-1} title="이 행 지우기" onClick={() => E.run('delrow')}>행 삭제</button>
            </>
          )}
        </div>
      )}
      <div ref={rootRef} className="mr-de-body" lang="ko" spellCheck={false}
        role="textbox" aria-multiline="true" aria-label="보고서 본문" aria-readonly={readOnly || undefined} />
    </div>
  );
}
