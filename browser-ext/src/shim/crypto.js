// Node `crypto` 중 browser/ 모듈이 쓰는 것만 — sha256 해시(동기), 난수 바이트.
import { Buffer } from 'buffer';
import { sha256 } from '@noble/hashes/sha256';

export function createHash(alg) {
  if (alg !== 'sha256') throw new Error(`지원하지 않는 해시: ${alg}`);
  const h = sha256.create();
  const api = {
    update(data, enc) { h.update(typeof data === 'string' ? Buffer.from(data, enc || 'utf8') : data); return api; },
    digest(enc) { const b = Buffer.from(h.digest()); return enc ? b.toString(enc) : b; },
  };
  return api;
}
export function randomBytes(n) { return Buffer.from(crypto.getRandomValues(new Uint8Array(n))); }
export const randomUUID = () => crypto.randomUUID();
export function timingSafeEqual(a, b) {
  if (a.length !== b.length) throw new RangeError('길이가 다름');
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}
export default { createHash, randomBytes, randomUUID, timingSafeEqual };
