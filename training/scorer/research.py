"""기업 리서치 배치 — interview_agent의 Gemini 검색 그라운딩을 떼어낸 것 (step 1).

디렉터 스펙 1단계: "interview QA agent의 기업 검색·리서치 기능을 따로 떼어내
약 4000개 기업을 조사, DB에 저장". 원본 interview_agent.py의 phase_research에서
리서치 부분만 추출하고, 인터뷰·온톨로지 로직은 뺐다.

⚠️ API 키는 코드에 넣지 않는다 — 환경변수로만 읽는다:
    GOOGLE_API_KEY   (Gemini 검색 그라운딩)
    RESEARCH_MODEL   (기본 gemini-2.5-flash — 서버 사정에 맞게)
원본에 하드코딩돼 있던 키는 노출로 간주해 재발급 대상이다(SEC-02).

⚠️ 실행은 대량 API 호출이라 AXR팀 협의 후. 이 모듈은 '준비된 코드'이고,
main()은 --dry-run이 기본이라 키 없이도 계획을 검증할 수 있다.

저장: SQLite (무인프라 — 리포 관례). companies(name PK, hints, research_text,
country, ts). 재실행 시 이미 조사된 기업은 건너뛴다(멱등·비용 절약).
"""
import argparse
import os
import sqlite3
import time
from pathlib import Path

RESEARCH_SYS = """\
너는 B2B 스타트업 글로벌 진출 컨설턴트다. 대상 기업을 조사하는 리서치 단계다.
반드시 Google 검색을 적극적으로 사용해(한국어·영어 질의를 섞어) 최대한 조사하라.
- IR/회사소개, 홈페이지, 제품/솔루션, 고객/레퍼런스, 수상/특허/투자, 기사/인터뷰,
  해외 진출/파트너/PoC 단서를 찾는다.
- 검색으로 확인한 사실과 추정을 구분한다. 출처가 불확실하면 추정이라고 명시한다.

출력: 아래 구조의 '마크다운 리서치 문서' 하나만(JSON 아님).
  # {회사명} 리서치
  ## 회사 개요 / 솔루션 / 성과·레퍼런스 / 해외 단서
  ## 요약 (관련도 판단용 핵심 3~5줄)
"""


def _db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS companies(
        name TEXT PRIMARY KEY, hints TEXT, research_text TEXT,
        country TEXT, ts TEXT)""")
    return conn


def research_one(company: str, hints: str, client, model: str) -> str:
    """기업 1개 리서치 — Gemini 검색 그라운딩. client는 google-genai Client."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        system_instruction=RESEARCH_SYS, temperature=0.3, max_output_tokens=4000,
        tools=[types.Tool(google_search=types.GoogleSearch())])
    contents = [types.Content(role="user", parts=[types.Part.from_text(
        text=f"대상 기업: {company}\n힌트: {hints}\n\n위 기업을 검색으로 조사해 "
             f"리서치 문서를 작성해줘.")])]
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=cfg)
            return (resp.text or "").strip()
        except Exception as e:                    # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return ""


def run_batch(companies: list, db_path, dry_run: bool = True) -> dict:
    """회사 리스트 → 리서치 → DB. dry_run이면 API 없이 계획만 검증(기본)."""
    conn = _db(db_path)
    done = {r[0] for r in conn.execute("SELECT name FROM companies "
                                       "WHERE research_text IS NOT NULL")}
    todo = [(c["name"], c.get("hints", "")) for c in companies
            if c["name"] not in done]
    tally = {"total": len(companies), "already_done": len(done),
             "to_research": len(todo), "dry_run": dry_run}
    if dry_run:
        print(f"[dry-run] 계획: 총 {tally['total']} · 완료 {tally['already_done']} · "
              f"조사 예정 {tally['to_research']} — 실행하려면 --run + AXR 협의")
        return tally

    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("GOOGLE_API_KEY 환경변수가 없습니다 (키는 코드에 넣지 않음).")
    from google import genai
    client = genai.Client(api_key=key)
    model = os.environ.get("RESEARCH_MODEL", "gemini-2.5-flash")

    ok = 0
    for name, hints in todo:
        try:
            text = research_one(name, hints, client, model)
        except Exception as e:                    # noqa: BLE001
            print(f"  ✗ {name}: {e}")
            continue
        conn.execute("INSERT OR REPLACE INTO companies VALUES(?,?,?,?,?)",
                     (name, hints, text, None,
                      time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
        ok += 1
        if ok % 25 == 0:
            print(f"  … {ok}/{len(todo)} 조사 완료")
    tally["researched"] = ok
    print(f"[완료] {ok}건 조사 → {db_path}")
    return tally


def load_company_list(path) -> list:
    """회사 목록 로드 — JSONL({name,hints}) 또는 한 줄에 회사명."""
    import json
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            d = json.loads(line)
            out.append({"name": d["name"], "hints": d.get("hints", "")})
        else:
            out.append({"name": line, "hints": ""})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="기업 리서치 배치 (Gemini 검색) — 기본 dry-run, 실행은 AXR 협의 후")
    ap.add_argument("--companies", required=True, help="회사 목록 (JSONL 또는 줄단위)")
    ap.add_argument("--db", default="dataset/research.db")
    ap.add_argument("--run", action="store_true",
                    help="실제 API 실행 (없으면 dry-run — 계획만 검증)")
    a = ap.parse_args()
    companies = load_company_list(a.companies)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    run_batch(companies, a.db, dry_run=not a.run)


if __name__ == "__main__":
    main()
