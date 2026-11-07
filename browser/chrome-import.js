// 크롬 즐겨찾기 가져오기 — 크롬 프로필 폴더의 Bookmarks(JSON)만 읽는다. 비밀번호·쿠키는 건드리지 않는다.
// 여러 프로필이 있으면 모두 모아 주소 기준으로 중복을 뺀다. 북마크바를 먼저, 그다음 기타 북마크.
const fs = require('fs');
const path = require('path');
const os = require('os');

const chromeDir = () => process.env.A2A_CHROME_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'Google', 'Chrome');

function profiles() {
  const dir = chromeDir();
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => n === 'Default' || /^Profile \d+$/.test(n)); } catch { return []; }
  return names.map((n) => path.join(dir, n, 'Bookmarks')).filter((p) => fs.existsSync(p));
}

function walk(node, folder, out) {
  if (!node) return;
  if (node.type === 'url' && /^https?:\/\//.test(node.url || '')) {
    out.push({ url: node.url, title: String(node.name || node.url).slice(0, 80), folder });
  }
  for (const c of node.children || []) walk(c, node.type === 'folder' && node.name ? node.name : folder, out);
}

// existing: 지금 즐겨찾기 — 같은 주소는 건너뛴다
function importChrome(existing, { max = 300 } = {}) {
  const files = profiles();
  if (!files.length) return { ok: false, error: '크롬 즐겨찾기 파일을 찾지 못했어요', added: [], skipped: 0, profiles: 0 };
  const seen = new Set(existing.map((b) => b.url));
  const added = [];
  let skipped = 0;
  for (const f of files) {
    let roots;
    try { roots = JSON.parse(fs.readFileSync(f, 'utf8')).roots || {}; } catch { continue; }
    const out = [];
    walk(roots.bookmark_bar, '', out);
    walk(roots.other, '기타', out);
    for (const b of out) {
      if (seen.has(b.url)) { skipped++; continue; }
      if (added.length >= max) break;
      seen.add(b.url);
      added.push(b);
    }
  }
  return { ok: true, added, skipped, profiles: files.length };
}

module.exports = { importChrome };
