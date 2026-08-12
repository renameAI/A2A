/** rename Lead SaaS 프론트 (이슈 #6-F).
 *
 * rewrites 프록시 — 브라우저는 동일 출처 /api/* 만 부르고, Next가 Cloud Run
 * 엔진으로 중계한다. CORS 설정이 필요 없고 엔진 URL이 클라이언트에 노출되지
 * 않는다 (스펙 Architecture 확정).
 */
const ENGINE = process.env.ENGINE_URL || "http://localhost:8423";

export default {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${ENGINE}/:path*` },
    ];
  },
};
