// 셸 UI(React + Tailwind) 빌드 — renderer/ → ui/ (Electron이 ui/*.html을 파일로 연다)
//   index.html : 브라우저 셸
//   page.html  : 에이전트 결과 페이지(shadcn/ui 컴포넌트로 블록 명세를 그린다)
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const r = (p) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  root: 'renderer',
  base: './',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': r('./renderer/src/page') } },
  build: {
    outDir: '../ui', emptyOutDir: true, target: 'chrome140',
    rollupOptions: { input: { index: r('./renderer/index.html'), page: r('./renderer/page.html') } },
  },
});
