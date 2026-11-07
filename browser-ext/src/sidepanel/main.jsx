// 사이드 패널 — 다리(window.a2a)를 먼저 세운 뒤 Electron 때의 채팅방 화면을 그대로 띄운다
import '../shared/bridge.js';
import { createRoot } from 'react-dom/client';
import '../ui/index.css';
import SidePanel from '../ui/SidePanel.jsx';

createRoot(document.getElementById('root')).render(<SidePanel />);
