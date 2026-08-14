/** rename Lead SaaS 프론트 (이슈 #6-F).
 *
 * rewrites 프록시 — 브라우저는 동일 출처 /api/* 만 부르고, Next가 Cloud Run
 * 엔진으로 중계한다. CORS 설정이 필요 없고 엔진 URL이 클라이언트에 노출되지
 * 않는다 (스펙 Architecture 확정).
 *
 * SaaS 경로만 연다. 이전에는 /api 이하 전체를 엔진 루트로 넘기는 캐치올이라
 * 인증이 없는 product 라우터(22개)와 v1 엔드포인트(7개)가 공개 URL로 그대로
 * 열렸다 — 누구나 API 크레딧을 태우고 저장된 프로필을 덤프할 수 있었다
 * (감사 확정 blocker). 프록시는 신뢰 경계이므로 여는 경로를 열거하는 쪽이 맞다.
 */
const ENGINE = process.env.ENGINE_URL || "http://localhost:8423";

export default {
  async rewrites() {
    return [
      { source: "/api/saas/:path*", destination: `${ENGINE}/saas/:path*` },
    ];
  },
};
