/** 대용량 PDF 업로드 전용 프록시 (이슈 #6-F).
 *
 * next.config.mjs의 rewrites는 body를 버퍼링해서 큰 multipart에서 500이 난다
 * (실측: 4KB 200 / 35MB 500). 이 Route Handler는 요청 본문을 그대로
 * 엔진으로 스트리밍해 메모리에 올리지 않는다.
 *
 * 다른 API는 rewrites 프록시를 그대로 쓴다 — 여기만 예외.
 */
const ENGINE = process.env.ENGINE_URL || "http://localhost:8423";

export const runtime = "nodejs";
export const maxDuration = 300;

export async function POST(req: Request) {
  const upstream = await fetch(`${ENGINE}/saas/upload`, {
    method: "POST",
    headers: {
      // content-type은 multipart 경계(boundary)를 담고 있어 그대로 넘겨야 한다
      "content-type": req.headers.get("content-type") ?? "",
      // 인증 헤더를 버리면 엔진이 401을 준다 — 업로드도 워크스페이스에 귀속된다
      ...(req.headers.get("authorization")
        ? { authorization: req.headers.get("authorization")! } : {}),
      ...(req.headers.get("x-dev-user")
        ? { "x-dev-user": req.headers.get("x-dev-user")! } : {}),
    },
    body: req.body,
    // Node fetch에서 스트림 본문을 보낼 때 필수
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type":
      upstream.headers.get("content-type") ?? "application/json" },
  });
}
