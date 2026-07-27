# -*- coding: utf-8 -*-
"""
company_research.py — 회사 리서치 전용 프로그램 (judge 로부터 분리)
==================================================================
역할:
  · 회사명(+힌트)을 받아 Google 검색 그라운딩(Gemini)으로 B2B 프로필을 리서치하고
    company_pool/ 폴더에 회사당 1개 JSON 으로 저장한다.
  · --kosdaq N : KRX 상장법인목록에서 코스닥 회사 N곳을 뽑아 일괄 리서치(병렬).
  · 저장된 풀은 negotiation_sim.py(judge)가 두 회사를 골라 협상을 돌리는 입력이 된다.

사용:
  단일:   python company_research.py --company "회사명" [--hint "설명"]
  일괄:   python company_research.py --kosdaq 200 [--workers 4]
  목록:   python company_research.py --list
  재수집: --force (기존 파일 덮어쓰기; 기본은 건너뜀=이어받기 가능)

저장 형식 (company_pool/<회사명>.json):
  {company, hint, sector, product, public_profile, provenance, source, researched_at}
  provenance: grounded(검색 확인) | insufficient(자료 빈약 — judge 사용 시 주의)
==================================================================
"""

import re
import json
import random
import argparse
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import negotiation_sim as ns   # chat(그라운딩)·make_client·모델·비용계측 재사용

BASE_DIR = Path(__file__).resolve().parent
POOL_DIR = BASE_DIR / "company_pool"
PRINT_LOCK = threading.Lock()

RESEARCH_SYS = """\
회사를 Google 검색으로 조사해, B2B 협상 상대로서의 공개 프로필을 6~10줄 평문으로
작성하라. 포함: ①사업 내용·주력 제품/서비스 ②주요 시장·고객 ③최근 동향(신사업·
수주·확장) ④검증된 성과(실적·인증·수상 — 검색으로 확인된 것만) ⑤협업/거래 관점에서
주목할 특징. 과장·추정 금지, 검색으로 확인된 사실 위주.
검색 결과가 거의 없으면 첫 줄에 '자료 없음' 이라고 쓰고 확인된 최소 사실만 적어라."""


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())


# ==================================================================
# 코스닥 상장사 명단 확보 (KRX KIND 상장법인목록 다운로드)
# ==================================================================
def fetch_kosdaq_list(n: int, seed: int = 42) -> List[Dict[str, str]]:
    url = ("http://kind.krx.co.kr/corpgeneral/corpList.do"
           "?method=download&marketType=kosdaqMkt")
    print(f"[명단] KRX KIND 코스닥 상장법인목록 다운로드 중...")
    html = ""
    last_err: Optional[Exception] = None
    for attempt in range(4):                      # 간헐적 연결 끊김 대비 재시도
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            html = r.content.decode("euc-kr", errors="replace")
            if len(html) > 100_000:               # 정상 응답은 수백 KB
                break
        except Exception as e:
            last_err = e
            print(f"  재시도 {attempt + 1}/4 ... ({str(e)[:60]})")
            import time as _t; _t.sleep(3 * (attempt + 1))
    if len(html) <= 100_000:
        raise RuntimeError(f"KRX 명단 다운로드 실패: {last_err}")
    comps: List[Dict[str, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = [re.sub(r"<[^>]+>", "", td).strip()
               for td in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) >= 5 and tds[0] and tds[0] != "회사명":
            # KIND 컬럼: [회사명, 종목코드?, 종목코드, 업종, 주요제품, ...] — 종목코드(숫자) 뒤 두 칸 사용
            code_idx = next((i for i, t in enumerate(tds) if re.fullmatch(r"\d{6}", t)), None)
            if code_idx is None or len(tds) <= code_idx + 2:
                continue
            name = tds[0]
            sector, product = tds[code_idx + 1], tds[code_idx + 2]
            if "스팩" in name:          # SPAC(기업인수목적회사)은 실사업이 없어 제외
                continue
            comps.append({"company": name, "sector": sector, "product": product})
    if len(comps) < n:
        raise RuntimeError(f"KRX 명단 파싱 결과가 부족합니다({len(comps)}건). "
                           f"포맷 변경 여부 확인 필요.")
    print(f"[명단] 코스닥 {len(comps)}개사 확보 → seed={seed} 로 {n}곳 무작위 표본")
    random.seed(seed)
    return random.sample(comps, n)


# ==================================================================
# 리서치 1건
# ==================================================================
def research_one(client, name: str, hint: str) -> Tuple[str, str]:
    """returns (public_profile, provenance)."""
    doc = ns.chat(client, ns.SPEAK_MODEL, RESEARCH_SYS,
                  f"회사명: {name}\n힌트: {hint or '(없음)'}",
                  temperature=0.3, max_tokens=2000, use_search=True)
    doc = (doc or "").strip()
    if not doc or doc[:20].startswith("자료 없음") or "자료 없음" in doc[:30]:
        return (doc or "자료 없음"), "insufficient"
    return doc, "grounded"


def save_entry(name: str, hint: str, profile: str, provenance: str,
               source: str, sector: str = "", product: str = "") -> Path:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    p = POOL_DIR / f"{safe_name(name)}.json"
    p.write_text(json.dumps({
        "company": name, "hint": hint, "sector": sector, "product": product,
        "public_profile": profile, "provenance": provenance, "source": source,
        "researched_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ==================================================================
# 일괄 리서치 (병렬 · 이어받기 가능)
# ==================================================================
def batch_research(companies: List[Dict[str, str]], workers: int, force: bool) -> None:
    client = ns.make_client()
    todo = []
    for c in companies:
        p = POOL_DIR / f"{safe_name(c['company'])}.json"
        if p.exists() and not force:
            continue
        todo.append(c)
    print(f"[일괄] 대상 {len(companies)}곳 중 신규 {len(todo)}곳 리서치 "
          f"(기존 {len(companies) - len(todo)}곳 건너뜀) | workers={workers}")

    done = {"n": 0, "ok": 0, "thin": 0, "fail": 0}

    def work(c):
        hint = f"코스닥 상장사. 업종: {c.get('sector','')}. 주요제품: {c.get('product','')}"
        try:
            profile, prov = research_one(client, c["company"], hint)
            save_entry(c["company"], hint, profile, prov, "kosdaq_krx",
                       c.get("sector", ""), c.get("product", ""))
            return c["company"], prov, None
        except Exception as e:
            return c["company"], "fail", str(e)[:120]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, c) for c in todo]
        for fut in as_completed(futures):
            name, prov, err = fut.result()
            with PRINT_LOCK:
                done["n"] += 1
                if err:
                    done["fail"] += 1
                    print(f"  [{done['n']:>3}/{len(todo)}] ✗ {name}: {err}")
                else:
                    done["ok" if prov == "grounded" else "thin"] += 1
                    if done["n"] % 10 == 0 or done["n"] == len(todo):
                        print(f"  [{done['n']:>3}/{len(todo)}] 진행중... "
                              f"(확인 {done['ok']} / 빈약 {done['thin']} / 실패 {done['fail']})")

    total = len(list(POOL_DIR.glob("*.json")))
    print(f"\n[완료] 신규 {done['n']}건 (grounded {done['ok']} · insufficient {done['thin']}"
          f" · 실패 {done['fail']}) | 풀 전체 {total}개사 → {POOL_DIR}")


def list_pool() -> None:
    files = sorted(POOL_DIR.glob("*.json"))
    print(f"[회사 풀] {POOL_DIR} — {len(files)}개사")
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            print(f"  · {d['company']:20s} [{d.get('provenance','?'):12s}] "
                  f"{d.get('sector','')[:20]}")
        except Exception:
            print(f"  · (파싱 실패) {f.name}")


# ==================================================================
# main
# ==================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="회사 리서치 전용 프로그램 → company_pool/ 저장")
    ap.add_argument("--company", default=None, help="단일 회사 리서치")
    ap.add_argument("--hint", default="", help="검색 힌트")
    ap.add_argument("--kosdaq", type=int, default=None, metavar="N",
                    help="코스닥 상장사 N곳 일괄 리서치 (KRX 명단 기반)")
    ap.add_argument("--workers", type=int, default=4, help="병렬 워커 수 (기본 4)")
    ap.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    ap.add_argument("--list", action="store_true", help="풀 목록 출력")
    ap.add_argument("--seed", type=int, default=42, help="표본 추출 시드")
    args = ap.parse_args()

    if args.list:
        list_pool()
        return
    if args.company:
        client = ns.make_client()
        profile, prov = research_one(client, args.company, args.hint)
        p = save_entry(args.company, args.hint, profile, prov, "manual")
        print(f"[저장] {p}  (provenance={prov})")
        print("-" * 50)
        print(profile[:600])
        ns.report_cost()
        return
    if args.kosdaq:
        companies = fetch_kosdaq_list(args.kosdaq, seed=args.seed)
        batch_research(companies, workers=args.workers, force=args.force)
        ns.report_cost()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
