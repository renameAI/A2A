// 비밀번호 금고 — 로컬 파일 하나(userData/vault.bin)를 통째로 암호화해 둔다.
//
// 암호화는 Electron safeStorage(macOS: 키체인이 지키는 키). 암호화를 못 쓰는 환경이면 저장 자체를
// 거부한다 — 평문으로 대신 저장하지 않는다. 풀어 둔 내용은 메인 프로세스 메모리에만 있고,
// 목록을 내줄 때도 비밀번호는 빼고 준다(보기·채우기 때만 한 건씩 꺼낸다).
const { app, safeStorage } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const FILE = () => path.join(app.getPath('userData'), 'vault.bin');
let data = null;          // { entries: [{ id, origin, username, password, created, lastUsed }], never: [origin] }
let broken = null;        // 파일은 있는데 못 풀면 — 덮어쓰지 않고 잠근다

function originOf(u) {
  try {
    const x = new URL(u);
    return /^https?:$/.test(x.protocol) ? x.origin : null;
  } catch { return null; }
}

function available() {
  return safeStorage.isEncryptionAvailable() && !broken;
}

function load() {
  if (data || broken) return data;
  try {
    const buf = fs.readFileSync(FILE());
    data = JSON.parse(safeStorage.decryptString(buf));
  } catch (e) {
    if (e.code === 'ENOENT') data = { entries: [], never: [] };
    else broken = String(e.message || e);      // 키가 바뀌었거나 파일이 깨짐 — 기존 파일을 지키려 잠근다
  }
  return data;
}

// 바꾸고 저장 — 저장이 실패하면 메모리도 되돌린다(디스크와 어긋나지 않게)
function commit(mutate) {
  const before = JSON.stringify(data);
  mutate();
  try { save(); } catch (e) { data = JSON.parse(before); throw e; }
}

function save() {
  if (!available()) throw new Error('이 컴퓨터에서 암호화 저장을 쓸 수 없어 비밀번호를 저장하지 않았습니다');
  const tmp = `${FILE()}.tmp`;
  fs.writeFileSync(tmp, safeStorage.encryptString(JSON.stringify(data)), { mode: 0o600 });
  fs.renameSync(tmp, FILE());
}

const pub = (e) => ({ id: e.id, origin: e.origin, username: e.username, created: e.created, lastUsed: e.lastUsed });

module.exports = {
  originOf,
  status: () => { load(); return { available: safeStorage.isEncryptionAvailable(), locked: !!broken }; },
  list() {
    if (!load()) return [];
    return data.entries.map(pub).sort((a, b) => (b.lastUsed || 0) - (a.lastUsed || 0));
  },
  forOrigin(origin) {
    if (!origin || !load()) return [];
    return data.entries.filter((e) => e.origin === origin).map(pub);
  },
  // 같은 사이트·아이디가 있으면 비밀번호만 바꾼다
  upsert(origin, username, password) {
    if (!originOf(origin) || typeof password !== 'string' || !password) throw new Error('잘못된 로그인 정보');
    if (!load()) throw new Error('금고를 열 수 없습니다');
    const now = Date.now();
    commit(() => {
      const hit = data.entries.find((e) => e.origin === origin && e.username === username);
      if (hit) { hit.password = password; hit.lastUsed = now; }
      else data.entries.push({ id: crypto.randomUUID(), origin, username: String(username || ''), password, created: now, lastUsed: now });
      data.never = data.never.filter((o) => o !== origin);
    });
  },
  // 이미 같은 값으로 저장돼 있으면 'same', 아이디는 같은데 비밀번호가 다르면 'changed', 없으면 'new'
  compare(origin, username, password) {
    if (!load()) return 'new';
    const hit = data.entries.find((e) => e.origin === origin && e.username === username);
    if (!hit) return 'new';
    return hit.password === password ? 'same' : 'changed';
  },
  touch(id) {
    const hit = load() && data.entries.find((e) => e.id === id);
    if (hit) { hit.lastUsed = Date.now(); try { save(); } catch { /* 사용 시각은 못 남겨도 그만 */ } }
  },
  secret(id) {
    const hit = load() && data.entries.find((e) => e.id === id);
    return hit ? { origin: hit.origin, username: hit.username, password: hit.password } : null;
  },
  remove(id) {
    if (!load()) throw new Error('금고를 열 수 없습니다');
    commit(() => { data.entries = data.entries.filter((e) => e.id !== id); });
  },
  isNever: (origin) => !!(load() && data.never.includes(origin)),
  never(origin) {
    if (!load() || data.never.includes(origin)) return;
    commit(() => { data.never.push(origin); });
  },
  file: FILE,
};
