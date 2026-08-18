#!/usr/bin/env python3
"""로그인 코드를 **메일 없이** 발급한다 (QA·운영용).

배경: Supabase 내장 메일러는 팀 멤버에게만, 그것도 시간당 2통만 보낸다
(공식 문서: "Send messages only to pre-authorized addresses" / 2 emails
per hour, 커스터마이즈 불가). QA가 로그인을 반복 검증하면 두 번째 시도에서
막히고, 그 사이 실제 사용자도 못 들어온다 — 한도가 프로젝트 전체 공유다.

admin generate_link는 링크와 함께 email_otp를 **응답으로** 돌려주고 메일을
보내지 않는다. 따라서 한도를 전혀 쓰지 않는다. 이 스크립트는 그 코드를
꺼내 출력할 뿐이다.

우회로가 아니다: 발급된 코드는 Supabase가 검증하는 정상 OTP이고, 서버의
SAAS_ALLOWED_USERS 허용목록도 그대로 적용된다. 바뀌는 것은 코드가 사용자
메일함 대신 관리자 터미널로 나온다는 점뿐이다.

    python scripts/issue_login_code.py tools@renamecorp.com

서비스 롤 키를 쓴다 — 이 키는 RLS를 우회하므로 로컬 .env 밖으로 내보내지
말 것. CI나 공유 화면에서 실행하지 않는다.
"""
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    email = sys.argv[1].strip()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:                                  # .env는 선택 — 환경변수가 있으면 그것을 쓴다
        from dotenv import load_dotenv
        load_dotenv(os.path.join(root, ".env"))
    except ImportError:
        pass

    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY 가 없습니다 (.env 확인)",
              file=sys.stderr)
        return 1

    req = urllib.request.Request(
        f"{url}/auth/v1/admin/generate_link",
        data=json.dumps({"type": "magiclink", "email": email}).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        print(f"발급 실패 ({e.code}): {body}", file=sys.stderr)
        # 존재하지 않는 사용자는 여기서 걸린다 — 조용히 만들지 않는다.
        return 1

    otp = data.get("email_otp")
    if not otp:
        print("응답에 email_otp가 없습니다 — Supabase 응답 형식을 확인하세요",
              file=sys.stderr)
        return 1

    print(f"\n  {email}\n  로그인 코드:  {otp}\n")
    print("  메일은 발송되지 않았고 한도도 쓰지 않았습니다.")
    print("  1시간 뒤 만료되며, 새로 발급하면 이전 코드는 무효가 됩니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
