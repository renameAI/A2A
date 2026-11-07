// 업무 기록 · 업무 지도(온톨로지) · 하네스 개선 · 업무 유형 · SSoT — Electron 앱(browser/)의 모듈을 그대로 싣는다.
// 규칙은 browser/에 하나만 있다. 여기는 서비스 워커에 잇는 자리(Electron main.js의 makeKnowledge·ontoCall)다.
// 파일은 shim/fs.js(IndexedDB)에 쌓인다 — 이 브라우저 프로필 안에만 있고 밖으로 나가지 않는다.
import '../shim/globals.js';
import { ready as fsReady, seed, usage } from '../shim/fs.js';
import cfgText from '../../../browser/collect.config.json?raw';
import { collect } from '../../../browser/events.js';
import { createOntology } from '../../../browser/ontology.js';
import { createWorkTypes } from '../../../browser/worktypes.js';
import { createHarness, MODELS } from '../../../browser/harness.js';
import { createRecall } from '../../../browser/recall.js';
import { createSsotRegistry } from '../../../browser/ssot.js';
import { handle, broadcast, on } from './hub.js';
import { store, loadStore, profile } from './state.js';

const USER_DATA = '/userData';
const DEFAULT_MODEL = 'claude-opus-5-5';

let onto = null;
let harness = null;
let worktypes = null;
let recaller = null;
let ssotReg = null;
let deps = null;                  // tasks.js가 넘겨 준다: { Agent, toolNames, chatChannels }

const changed = () => broadcast('onto:changed');
export const baseModel = () => (MODELS[store.prefs.agentModel] ? store.prefs.agentModel : DEFAULT_MODEL);

// 수집기는 시작하자마자 켠다(탭·채팅 기록) — 온톨로지·하네스는 에이전트 쪽이 Agent를 넘겨 준 뒤에
export const ready = (async () => {
  await fsReady;
  await loadStore();
  seed('/a2a/browser/collect.config.json', cfgText);
  collect.init(USER_DATA, { profile });
  onto = createOntology({
    root: () => collect.root,
    taskInfo: (id) => { const t = store.tasks.find((x) => x.id === id); return t && { title: t.title, status: t.status }; },
  });
})();

export async function attach(d) {
  await ready;
  deps = d;
  const apiKey = () => store.apiKey;
  worktypes = createWorkTypes({
    dir: `${USER_DATA}/worktypes`, onto, apiKey, signals: () => harness?.signals() || [],
    onChange: () => { harness?.syncOnto(); changed(); },
  });
  harness = createHarness({
    dir: `${USER_DATA}/harness`, onto, apiKey, Agent: d.Agent, more: () => worktypes.extraNodes(),
    typeOf: (taskId) => worktypes?.typeOf(taskId) || null, typeName: (id) => worktypes?.get(id)?.name || '', currentType: () => d.currentType(),
    toolNames: d.toolNames, ema: () => null, chatChannels: d.chatChannels,
    onChange: changed,
  });
  recaller = createRecall({ onto, signals: () => harness.signals(), sameType: (id) => worktypes.tasksOf(id) });
  ssotReg = createSsotRegistry({ dir: `${USER_DATA}/ssot`, onto, worktypes: () => worktypes });
  return { collect, onto, harness, worktypes, recaller };
}
export { collect };
export const knowledge = () => ({ onto, harness, worktypes, recaller, ssotReg });

const user = () => profile().user;
const ontoCall = {
  summary: () => { try { ssotReg?.sync(); } catch (e) { console.warn('[ssot]', e.message); } return { ...onto.summary(), ssot: ssotReg?.stats() || null }; },
  list: (type, q) => onto.list(typeof type === 'string' ? type : null, typeof q === 'string' ? q.slice(0, 100) : ''),
  node: (id) => { if (typeof id !== 'string') return null; const n = onto.node(id); return n && { ...n, ssot: ssotReg?.get(id) || null }; },
  ssotIssue: (id) => ssotReg.issue(String(id), user()),
  ssotSyncAll: () => ({ issued: ssotReg.sync(), ...ssotReg.stats() }),
  graph: (o = {}) => onto.graph({ center: typeof o.center === 'string' ? o.center : null, depth: Math.min(3, Number(o.depth) || 2),
    types: Array.isArray(o.types) ? o.types.map(String) : null, limit: Math.min(800, Number(o.limit) || 400) }),
  harness: () => ({ ...harness.state(), worktypes: worktypes.state().types, models: MODELS, baseModel: baseModel() }),
  propose: (sigId) => harness.propose(String(sigId)),
  probe: (lessonId, model) => harness.probe(String(lessonId), { model: typeof model === 'string' ? model : null }),
  proposeFromProbe: (probeId) => harness.proposeFromProbe(String(probeId)),
  clearTypeEdit: (typeId) => harness.clearType(String(typeId)),
  adopt: (lessonId) => harness.adopt(String(lessonId), user()),
  reject: (lessonId) => harness.reject(String(lessonId), user()),
  rollback: (vid) => harness.rollback(vid === null ? null : String(vid)),
  worktypes: () => worktypes.state(),
  backfillTypes: () => worktypes.backfill(),
  probeType: async (typeId, model) => {
    const t = worktypes.get(String(typeId));
    if (!t || !Object.hasOwn(MODELS, model)) throw new Error('유형이나 모델이 맞지 않아요');
    const a = MODELS[t.model] ? t.model : baseModel();
    if (a === model) throw new Error('지금 쓰는 모델과 같아요');
    const tasks = worktypes.runs(t.id, 2).map((r) => r.id);
    if (!tasks.length) throw new Error('이 유형에 다시 돌릴 작업이 없어요');
    const out = [];
    for (const task of tasks) out.push(await harness.probeModel(task, { a, b: model, typeId: t.id }));
    return out;
  },
  setTypeModel: (typeId, model) => worktypes.setModel(String(typeId), model === null ? null : (Object.hasOwn(MODELS, model) ? model : (() => { throw new Error('모르는 모델'); })())),
  addLesson: (l = {}) => harness.addLesson({ text: String(l.text || ''), edit: l.edit || {}, signal: l.signal || null, task: l.task || null, by: user() }),
  syncNow: () => ({ ok: false, reason: '업무 기록 올리기는 아직 Chromium 쪽에 없어요(기본 꺼짐)' }),
  // 주장 튜플 내보내기 — 다운로드 폴더에 JSONL
  exportClaims: async () => {
    const name = `a2a-claims-${new Date().toISOString().slice(0, 10)}.jsonl`;
    const body = onto.claims().map((c) => JSON.stringify(c)).join('\n') + '\n';
    const url = `data:application/x-ndjson;charset=utf-8,${encodeURIComponent(body)}`;
    await chrome.downloads.download({ url, filename: name, saveAs: false });
    return name;
  },
};
handle('onto:call', async (name, ...args) => {
  args = args.filter((x) => !(x && typeof x.postMessage === 'function'));   // 허브가 붙인 포트는 빼고
  if (!Object.hasOwn(ontoCall, name)) throw new Error('blocked');
  await ready;
  if (!harness && name !== 'summary' && name !== 'list' && name !== 'node' && name !== 'graph') throw new Error('아직 준비 중이에요');
  return ontoCall[name](...args);
});
handle('onto:usage', async () => { await ready; return usage(); });
on('map:open', () => chrome.tabs.create({ url: chrome.runtime.getURL('map.html') }));

globalThis.a2a = { ...(globalThis.a2a || {}), knowledge, collect: () => collect, fsUsage: usage };
