/* Supabase 클라이언트 + 인증 헤더 (배포용).
 *
 * 두 모드를 명시적으로 가른다 — 조용한 대체 없음:
 * - NEXT_PUBLIC_SUPABASE_URL/ANON_KEY 가 있으면 실제 인증(magic link).
 * - 없으면 로컬 dev 모드로 X-Dev-User 헤더를 쓴다. 프로덕션 빌드에 env가
 *   빠지면 인증이 사라지는 게 아니라 로그인 화면 자체가 안 뜨므로,
 *   isConfigured를 화면이 직접 읽어 상태를 드러낸다.
 *
 * anon 키는 공개 키다(브라우저에 나가는 것이 정상). 실제 권한은 서버의
 * SAAS_ALLOWED_USERS 허용 목록과 Postgres RLS가 정한다.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const isConfigured = Boolean(URL && ANON);

export const supabase: SupabaseClient | null = isConfigured
  ? createClient(URL, ANON, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null;

/** 로컬 dev 폴백 사용자 — Supabase 미설정일 때만 쓰인다. */
export const DEV_USER = "boram";

/** 요청 헤더. 토큰이 필요하나 없으면 던진다 — 인증 없이 조용히 나가면
 *  서버가 401을 주고 사용자는 이유를 모른다. */
export async function authHeaders(): Promise<Record<string, string>> {
  const base = { "Content-Type": "application/json" };
  if (!supabase) return { ...base, "X-Dev-User": DEV_USER };
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("로그인이 필요해요");
  return { ...base, Authorization: `Bearer ${token}` };
}
