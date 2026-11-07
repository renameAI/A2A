// 방문 기록 — userData/history.json. 같은 주소는 한 줄(마지막 방문 시각·횟수), 최근 5000개까지.
// 에이전트가 연 페이지는 표시해 둔다(사람이 본 것과 구분).
const { app } = require('electron');
const fs = require('fs');
const path = require('path');

const FILE = () => path.join(app.getPath('userData'), 'history.json');
const MAX = 5000;
let rows = null;            // [{ url, title, at, count, agent }]
let timer = null;

function load() {
  if (rows) return rows;
  try { rows = JSON.parse(fs.readFileSync(FILE(), 'utf8')); } catch { rows = []; }
  return rows;
}
function save() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    const tmp = `${FILE()}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(rows.slice(0, MAX)));
    fs.renameSync(tmp, FILE());
  }, 500);
}
const recordable = (u) => /^https?:\/\//.test(u);

module.exports = {
  visit(url, title, { agent = false } = {}) {
    if (!recordable(url)) return;
    load();
    const i = rows.findIndex((r) => r.url === url);
    const prev = i >= 0 ? rows.splice(i, 1)[0] : null;
    rows.unshift({ url, title: title || prev?.title || '', at: Date.now(), count: (prev?.count || 0) + 1, agent: agent || false });
    if (rows.length > MAX) rows.length = MAX;
    save();
  },
  title(url, title) {
    const r = load().find((x) => x.url === url);
    if (r && title && r.title !== title) { r.title = title; save(); }
  },
  // 검색어(제목·주소)로 거른 최근 순 목록
  query(q = '', limit = 300) {
    const s = String(q).toLowerCase().trim();
    return load().filter((r) => !s || r.url.toLowerCase().includes(s) || (r.title || '').toLowerCase().includes(s)).slice(0, limit);
  },
  // 주소창 자동완성 — 자주·최근 간 곳 먼저
  suggest(q, limit = 6) {
    const s = String(q).toLowerCase().trim();
    if (!s) return [];
    const now = Date.now();
    return load()
      .filter((r) => r.url.toLowerCase().includes(s) || (r.title || '').toLowerCase().includes(s))
      .map((r) => {
        const host = r.url.replace(/^https?:\/\/(www\.)?/, '');
        const prefix = host.toLowerCase().startsWith(s) ? 3 : 0;          // 주소 앞부분이 맞으면 크게
        const recency = Math.max(0, 2 - (now - r.at) / (7 * 864e5));        // 최근 1~2주 가산
        return { ...r, score: prefix + Math.log2(1 + r.count) + recency };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  },
  remove(url) { load(); rows = rows.filter((r) => r.url !== url); save(); },
  clear(range) {
    load();
    const since = { hour: 3600e3, day: 864e5 }[range];
    rows = since ? rows.filter((r) => r.at < Date.now() - since) : [];
    save();
  },
};
