// 개발 환경 기본값 — Electron 앱(main.js CFG)과 같다. 실제 로그인을 붙이면 계정은 하나만 남는다.
export const CFG = {
  engine: 'http://localhost:8423',
  web: 'http://localhost:3200',
  user: 'boram',
  tenant: 'test',
  devUsers: ['boram', 'alice', 'bob', 'carol', 'acme_ceo'],
};
