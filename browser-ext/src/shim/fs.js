// Node `fs` (동기 API 일부) — 서비스 워커 안의 메모리 파일 + IndexedDB 저장.
// browser/의 events.js·ontology.js·harness.js 등을 고치지 않고 그대로 싣기 위한 자리다(같은 규칙을 두 군데 두지 않게).
// 시작할 때 `ready`를 기다린 뒤에 모듈을 만든다. 쓰기는 잠깐 모았다가 IndexedDB에 저장한다.
import { Buffer } from 'buffer';

const DB = 'a2a-fs';
const STORE = 'files';
const files = new Map();          // 경로 → { buf: Uint8Array, len, mtime }
const dirs = new Set(['/']);
const dirty = new Set();
const removed = new Set();
const enc = new TextEncoder();
const dec = new TextDecoder();

const norm = (p) => {
  const parts = [];
  for (const s of String(p).split('/')) {
    if (!s || s === '.') continue;
    if (s === '..') parts.pop(); else parts.push(s);
  }
  return `/${parts.join('/')}`;
};
const parent = (p) => p.slice(0, p.lastIndexOf('/')) || '/';
const err = (code, p) => Object.assign(new Error(`${code}: ${p}`), { code, path: p });
const toBytes = (d) => (typeof d === 'string' ? enc.encode(d) : d instanceof Uint8Array ? d : enc.encode(String(d)));

function addDirs(p) {
  for (let d = parent(p); !dirs.has(d); d = parent(d)) dirs.add(d);
}
function put(p, bytes) {
  const buf = new Uint8Array(Math.max(64, bytes.length));
  buf.set(bytes);
  files.set(p, { buf, len: bytes.length, mtime: Date.now() });
  addDirs(p);
  touch(p);
}
function grow(f, add) {
  const need = f.len + add;
  if (need <= f.buf.length) return;
  const nb = new Uint8Array(Math.max(need, f.buf.length * 2));
  nb.set(f.buf.subarray(0, f.len));
  f.buf = nb;
}

// ── 저장 ──
let dbp = null;
const db = () => (dbp ||= new Promise((res, rej) => {
  const r = indexedDB.open(DB, 1);
  r.onupgradeneeded = () => r.result.createObjectStore(STORE);
  r.onsuccess = () => res(r.result);
  r.onerror = () => rej(r.error);
}));
let timer = null;
const schedule = () => { clearTimeout(timer); timer = setTimeout(() => flush().catch((e) => console.warn('[fs] 저장 실패', e)), 200); };
function touch(p) { dirty.add(p); removed.delete(p); schedule(); }
function drop(p) { dirty.delete(p); removed.add(p); schedule(); }
export async function flush() {
  clearTimeout(timer);
  if (!dirty.size && !removed.size) return;
  const d = await db();
  const tx = d.transaction(STORE, 'readwrite');
  const st = tx.objectStore(STORE);
  for (const p of dirty) { const f = files.get(p); if (f) st.put(f.buf.slice(0, f.len), p); }
  for (const p of removed) st.delete(p);
  dirty.clear();
  removed.clear();
  await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
}
export const ready = (async () => {
  try {
    const d = await db();
    const tx = d.transaction(STORE, 'readonly');
    const st = tx.objectStore(STORE);
    await new Promise((res, rej) => {
      const c = st.openCursor();
      c.onsuccess = () => {
        const cur = c.result;
        if (!cur) return res();
        const bytes = cur.value instanceof Uint8Array ? cur.value : new Uint8Array(cur.value);
        files.set(cur.key, { buf: bytes, len: bytes.length, mtime: Date.now() });
        addDirs(cur.key);
        cur.continue();
      };
      c.onerror = () => rej(c.error);
    });
  } catch (e) { console.warn('[fs] 저장소를 읽지 못함', e); }
})();
// 번들에 들어 있는 읽기 전용 파일(설정 등)
export function seed(p, text) { const k = norm(p); if (!files.has(k)) { put(k, enc.encode(text)); dirty.delete(k); } }
export const usage = () => { let n = 0; for (const f of files.values()) n += f.len; return { files: files.size, bytes: n }; };

// ── fs API ──
export function readFileSync(p, o) {
  const f = files.get(norm(p));
  if (!f) throw err('ENOENT', p);
  const e = typeof o === 'string' ? o : o?.encoding;
  const bytes = f.buf.subarray(0, f.len);
  return e ? dec.decode(bytes) : Buffer.from(bytes);
}
export function writeFileSync(p, data) { put(norm(p), toBytes(data)); }
export function appendFileSync(p, data) {
  const k = norm(p);
  const f = files.get(k);
  if (!f) { put(k, toBytes(data)); return; }
  const b = toBytes(data);
  grow(f, b.length);
  f.buf.set(b, f.len);
  f.len += b.length;
  f.mtime = Date.now();
  touch(k);
}
export function mkdirSync(p) { const k = norm(p); dirs.add(k); addDirs(k); }
export function existsSync(p) { const k = norm(p); return files.has(k) || dirs.has(k); }
export function statSync(p) {
  const k = norm(p);
  const f = files.get(k);
  if (!f && !dirs.has(k)) throw err('ENOENT', p);
  return { size: f ? f.len : 0, mtimeMs: f?.mtime || 0, mtime: new Date(f?.mtime || 0), isFile: () => !!f, isDirectory: () => !f };
}
export function readdirSync(p) {
  const k = norm(p);
  if (!dirs.has(k)) throw err('ENOENT', p);
  const pre = k === '/' ? '/' : `${k}/`;
  const out = new Set();
  for (const x of [...files.keys(), ...dirs]) if (x !== k && x.startsWith(pre)) out.add(x.slice(pre.length).split('/')[0]);
  return [...out].sort();
}
export function renameSync(a, b) {
  const ka = norm(a), kb = norm(b);
  const f = files.get(ka);
  if (!f) throw err('ENOENT', a);
  files.delete(ka);
  drop(ka);
  files.set(kb, f);
  addDirs(kb);
  touch(kb);
}
export function unlinkSync(p) { const k = norm(p); if (!files.delete(k)) throw err('ENOENT', p); drop(k); }
export function rmSync(p, o = {}) {
  const k = norm(p);
  if (files.has(k)) return unlinkSync(k);
  if (!dirs.has(k)) { if (o.force) return; throw err('ENOENT', p); }
  for (const x of [...files.keys()]) if (x.startsWith(`${k}/`)) unlinkSync(x);
  for (const x of [...dirs]) if (x === k || x.startsWith(`${k}/`)) dirs.delete(x);
}
const fds = new Map();
let nextFd = 3;
export function openSync(p) { const k = norm(p); if (!files.has(k)) throw err('ENOENT', p); fds.set(nextFd, k); return nextFd++; }
export function closeSync(fd) { fds.delete(fd); }
export function readSync(fd, buf, off, len, pos) {
  const f = files.get(fds.get(fd));
  if (!f) throw err('EBADF', String(fd));
  const start = pos ?? 0;
  const n = Math.max(0, Math.min(len, f.len - start));
  buf.set(f.buf.subarray(start, start + n), off);
  return n;
}

export default {
  readFileSync, writeFileSync, appendFileSync, mkdirSync, existsSync, statSync, readdirSync, renameSync, unlinkSync, rmSync,
  openSync, closeSync, readSync,
};
