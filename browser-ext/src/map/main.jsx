// 업무 지도 — browser/renderer의 화면(WorkMap·GraphView)을 그대로 띄운다. 데이터는 서비스 워커의 onto:call(sw/knowledge.js).
import '../shared/bridge.js';
import { createRoot } from 'react-dom/client';
import './map.css';
import WorkMap from '../../../browser/renderer/src/WorkMap.jsx';

document.body.dataset.tone = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
createRoot(document.getElementById('root')).render(<WorkMap />);
