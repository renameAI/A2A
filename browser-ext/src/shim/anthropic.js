// browser/ 모듈(harness.js·worktypes.js)이 `require('@anthropic-ai/sdk')`로 만드는 클라이언트 — 확장 안에서는
// 브라우저 요청이라 dangerouslyAllowBrowser를 켠다(sw/agent.js와 같은 한계: 개발용 키가 이 기기에 있다).
import Real from '@anthropic-ai/sdk/index.mjs';

class Anthropic extends Real {
  constructor(opts = {}) { super({ dangerouslyAllowBrowser: true, ...opts }); }
}
Object.assign(Anthropic, Real);
Anthropic.default = Anthropic;
Anthropic.Anthropic = Anthropic;
export default Anthropic;
export { Anthropic };
