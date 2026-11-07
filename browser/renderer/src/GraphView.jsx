// 그래프 뷰 — 옵시디언처럼: 노드는 점(유형별 색), 선은 관계. 끌어서 옮기고, 휠로 확대, 빈 곳을 끌면 화면 이동.
// 배치는 d3-force(물리 시뮬레이션). 그리기는 캔버스 — 노드 수백 개도 가볍게.
import { useEffect, useRef } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';

export const TYPE_COLOR = {
  person: '#3d6fe0', agent: '#ff6b45', task: '#e2a03f', step: '#a3a9b3', source: '#21a366', output: '#c2410c',
  approval: '#8b5cf6', channel: '#0ea5e9', message: '#7dd3fc', site: '#64748b', platform: '#0f766e', company: '#16a34a',
  harness: '#db2777', signal: '#ef4444', lesson: '#f59e0b', probe: '#6366f1', worktype: '#0891b2',
  deal: '#111827', decision: '#7c2d12', issue: '#ca8a04', assumption: '#4338ca', claim: '#15803d', stage: '#9ca3af', directive: '#be123c', project: '#0d9488', fund: '#a16207', disbursement: '#1d4ed8',   // 메리 딜룸 · Work ID
};

export default function GraphView({ data, selected, onSelect, dark = false, height = '100%' }) {
  const canvas = useRef(null);
  const st = useRef({ k: 1, x: 0, y: 0, nodes: [], links: [], hover: null, drag: null, pan: null });

  useEffect(() => {
    const c = canvas.current;
    if (!c || !data) return;
    const s = st.current;
    const old = new Map(s.nodes.map((n) => [n.id, n]));
    s.nodes = data.nodes.map((n) => ({ ...n, ...(old.get(n.id) ? { x: old.get(n.id).x, y: old.get(n.id).y } : {}) }));
    const ids = new Set(s.nodes.map((n) => n.id));
    s.links = data.edges.filter((e) => ids.has(e.s) && ids.has(e.o)).map((e) => ({ source: e.s, target: e.o, r: e.r }));
    const deg = new Map();
    for (const l of s.links) { deg.set(l.source, (deg.get(l.source) || 0) + 1); deg.set(l.target, (deg.get(l.target) || 0) + 1); }
    for (const n of s.nodes) n.r = 3 + Math.min(9, Math.sqrt(deg.get(n.id) || 0) * 1.6) + (n.id === selected ? 2 : 0);
    const sim = forceSimulation(s.nodes)
      .force('link', forceLink(s.links).id((d) => d.id).distance(64).strength(0.45))
      .force('charge', forceManyBody().strength(-150))
      .force('center', forceCenter(0, 0))
      .force('collide', forceCollide().radius((d) => d.r + 3))
      .alphaDecay(0.035);
    let raf = 0;
    const draw = () => {
      const ctx = c.getContext('2d');
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth, h = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.translate(w / 2 + s.x, h / 2 + s.y);
      ctx.scale(s.k, s.k);
      const near = new Set();
      const focus = s.hover || selected;
      if (focus) for (const l of s.links) { if (l.source.id === focus) near.add(l.target.id); if (l.target.id === focus) near.add(l.source.id); }
      ctx.lineWidth = 1 / s.k;
      for (const l of s.links) {
        const on = focus && (l.source.id === focus || l.target.id === focus);
        ctx.strokeStyle = on ? (dark ? 'rgba(255,170,140,.85)' : 'rgba(255,107,69,.75)') : (dark ? 'rgba(255,255,255,.12)' : 'rgba(30,40,55,.13)');
        ctx.beginPath(); ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y); ctx.stroke();
      }
      for (const n of s.nodes) {
        const dim = focus && n.id !== focus && !near.has(n.id);
        ctx.globalAlpha = dim ? 0.25 : 1;
        ctx.fillStyle = TYPE_COLOR[n.type] || '#94a3b8';
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
        if (n.id === selected) { ctx.lineWidth = 2.5 / s.k; ctx.strokeStyle = dark ? '#fff' : '#16202b'; ctx.stroke(); }
        // 이름표 — 확대했거나, 고른 것·그 이웃·연결 많은 것
        if (s.k > 1.6 || n.id === focus || (near.has(n.id) && near.size <= 14) || (!focus && n.r > 9)) {
          ctx.globalAlpha = dim ? 0.3 : 0.92;
          ctx.fillStyle = dark ? '#e6e9ee' : '#26313d';
          ctx.font = `${11 / Math.max(1, s.k * 0.9)}px -apple-system, "Apple SD Gothic Neo", sans-serif`;
          ctx.fillText(String(n.label).slice(0, 26), n.x + n.r + 3, n.y + 3.5);
        }
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    c.dataset.nodes = String(s.nodes.length);
    return () => { cancelAnimationFrame(raf); sim.stop(); };
  }, [data, selected, dark]);

  // 화면 좌표 → 그래프 좌표, 그 자리의 노드
  const at = (e) => {
    const c = canvas.current, s = st.current;
    const r = c.getBoundingClientRect();
    const x = (e.clientX - r.left - r.width / 2 - s.x) / s.k, y = (e.clientY - r.top - r.height / 2 - s.y) / s.k;
    let best = null, bd = Infinity;
    for (const n of s.nodes) { const d = Math.hypot(n.x - x, n.y - y); if (d < n.r + 4 / s.k && d < bd) { best = n; bd = d; } }
    return { x, y, n: best };
  };
  return (
    <canvas ref={canvas} id="graph-canvas" style={{ width: '100%', height }} className="block cursor-grab active:cursor-grabbing"
      onWheel={(e) => { const s = st.current; s.k = Math.max(0.2, Math.min(4, s.k * (e.deltaY < 0 ? 1.1 : 0.9))); }}
      onMouseDown={(e) => { const p = at(e); const s = st.current; if (p.n) { s.drag = p.n; p.n.fx = p.n.x; p.n.fy = p.n.y; s.moved = false; } else s.pan = { x: e.clientX - s.x, y: e.clientY - s.y }; }}
      onMouseMove={(e) => {
        const s = st.current;
        if (s.drag) { const p = at(e); s.drag.fx = p.x; s.drag.fy = p.y; s.drag.x = p.x; s.drag.y = p.y; s.moved = true; return; }
        if (s.pan) { s.x = e.clientX - s.pan.x; s.y = e.clientY - s.pan.y; return; }
        s.hover = at(e).n?.id || null;
      }}
      onMouseUp={() => { const s = st.current; if (s.drag) { const n = s.drag; n.fx = null; n.fy = null; if (!s.moved) onSelect?.(n.id); } s.drag = null; s.pan = null; }}
      onMouseLeave={() => { const s = st.current; s.hover = null; s.drag = null; s.pan = null; }}
    />
  );
}
