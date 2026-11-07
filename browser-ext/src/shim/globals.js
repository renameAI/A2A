// browser/ 모듈이 전역으로 쓰는 Node 것(Buffer) — 다른 import보다 먼저 불러온다
import { Buffer } from 'buffer';

globalThis.Buffer ??= Buffer;
