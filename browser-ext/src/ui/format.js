// 표시용 도우미 — 시간·단계 이름·요약
export const WALLS = [
  { id: 'sky', name: '하늘', tone: 'light' },
  { id: 'dawn', name: '새벽', tone: 'light' },
  { id: 'meadow', name: '초원', tone: 'light' },
  { id: 'sand', name: '모래', tone: 'light' },
  { id: 'dusk', name: '밤하늘', tone: 'dark' },
  { id: 'graphite', name: '흑연', tone: 'dark' },
];
export const STEP = {
  read_page: ['page', '페이지 읽기'], screenshot: ['image', '화면 보기'], navigate: ['globe', '이동'],
  go_back: ['back', '뒤로'], new_tab: ['tabs', '새 탭 열기'], list_tabs: ['list', '탭 목록'],
  switch_tab: ['tabs', '탭 전환'], click: ['cursor', '클릭'], type: ['keyboard', '입력'],
  select_option: ['list', '선택'], scroll: ['scroll', '스크롤'], list_channels: ['hash', '채널 목록'],
  post_to_channel: ['chat', '채널에 게시'], create_page: ['page', '결과 페이지 만들기'],
};
export const SUGS_NEW = [
  '국내 호텔 PMS(객실관리) 업체 5곳 찾아서 표로 정리해줘',
  '오늘 나온 B2B SaaS 투자 뉴스 3개 요약해줘',
  'DART에서 이번 주 단일판매·공급계약 공시 훑어줘',
];
export const SUGS_PAGE = [
  '이 페이지 요약해서 핵심만 알려줘',
  '이 회사 사이트에서 연락처(메일·전화·문의 폼)를 찾아줘',
  '이 페이지 내용을 3줄로 요약해서 #영업 채널에 올려줘',
];

export function hostOf(u) { try { return new URL(u).host.replace(/^www\./, ''); } catch { return ''; } }
export function hue(s) {
  let h = 0;
  for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) % 360;
  return `hsl(${h} 52% 52%)`;
}
export function ago(ms, now = Date.now()) {
  const s = Math.round((now - ms) / 1000);
  if (s < 45) return '방금';
  const m = Math.round(s / 60);
  if (m < 60) return `${m}분 전`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}시간 전` : `${Math.round(h / 24)}일 전`;
}
export function dur(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}
export function statusText(t, now = Date.now()) {
  if (t.status === 'working') return `작업 중 · ${dur(now - t.started)}`;
  const when = ago(t.ended || t.started, now);
  return { done: `완료 · ${when}`, error: `오류 · ${when}`, stopped: `중지됨 · ${when}` }[t.status] || when;
}
export function brief(name, input) {
  try {
    if (!input) return '';
    if (name === 'navigate' || name === 'new_tab') return hostOf(input.url) + (new URL(input.url).pathname.length > 1 ? '/…' : '');
    if (name === 'click' || name === 'select_option') return `[${input.index}]${input.value ? ' ' + input.value : ''}`;
    if (name === 'type') return `"${input.text}"${input.submit ? ' ⏎' : ''}`;
    if (name === 'create_page') return input.title;
    if (name === 'post_to_channel') return `#${String(input.channel).replace(/^#/, '')}`;
    if (name === 'switch_tab') return `탭 ${input.tab_id}`;
    if (name === 'scroll') return input.direction === 'up' ? '위로' : '아래로';
  } catch { /* 이상한 입력은 이름만 보여 준다 */ }
  return '';
}
export const STATUS_DOT = {
  working: 'bg-accent animate-breathe shadow-[0_0_0_3px_var(--color-accent-soft)]',
  done: 'bg-ok', error: 'bg-warn', stopped: 'bg-faint',
};

// 결과 페이지 → 채널용 마크다운(팀원은 채팅에서 바로 본다). main.js의 pageMarkdown과 같은 모양.
export function pageMarkdown(p) {
  const out = [`**📄 ${p.title}**`, p.subtitle || ''];
  for (const b of p.blocks || []) {
    if (b.type === 'stats') out.push(b.items.map((x) => `- ${x.label}: **${x.value}**`).join('\n'));
    if (b.type === 'table') out.push([`| ${b.columns.join(' | ')} |`, `|${b.columns.map(() => '---').join('|')}|`, ...b.rows.slice(0, 30).map((r) => `| ${r.join(' | ')} |`)].join('\n'));
    if (b.type === 'cards') out.push(b.items.map((c) => `- **${c.title}**${c.subtitle ? ` — ${c.subtitle}` : ''}${c.url ? ` ${c.url}` : ''}`).join('\n'));
    if (b.type === 'callout') out.push(`> **${b.title}** ${b.body || ''}`);
    if (b.type === 'list') out.push(b.items.map((x) => `- ${x}`).join('\n'));
    if (b.type === 'text') out.push(b.markdown);
    if (b.type === 'sources') out.push(`출처: ${b.items.map((x) => x.url).join(' · ')}`);
  }
  return out.filter(Boolean).join('\n\n');
}

export function timeOf(ms) {
  return new Date(ms).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' });
}
export function dayOf(ms) {
  const d = new Date(ms), t = new Date();
  const y = new Date(t); y.setDate(t.getDate() - 1);
  if (d.toDateString() === t.toDateString()) return '오늘';
  if (d.toDateString() === y.toDateString()) return '어제';
  return d.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
}
