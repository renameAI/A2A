"""Judge 온톨로지 + 가설 라이브러리 추출 — 협상 시뮬레이션 재료 파싱.

⚠️ 유효 범위 (app/ontology/materials/README_judge_ontology_simulation.md §6과 동일):
이 재료는 학습(SFT) 벽돌이 아니라 온톨로지 설계도다. 판매자 편중(도메인당 1개사) ·
구매측 전부 [구성](AI 생성 시뮬레이션) · 정답 앵커 없음 — 세 이유로 그대로
app/dataset.py·app/augment.py 파이프라인에 넣지 않는다. 여기서 뽑는 것은:
  - 차원별 판정 근거·루브릭 (Judge 프롬프트·온톨로지 설계 입력)
  - exploit/explore 가설 카드 (기획서 7-B, Strategy Loop 가설 시드)
Judge의 판단(JudgeResult) 자체와는 다른 개념이다 — 이건 "협상 국면에서 어떤 패턴이
반복되는가"의 가설이지, 특정 후보 쌍의 판단 출력이 아니다.

정직성: 원문에 명시적으로 있는 구조만 뽑는다. 형식이 어긋나는 섹션(예: 케이스 1-H의
두 렌즈 대조표는 표준 4열 표가 아니다)은 스킵하고 report에 정직하게 집계한다 —
강제로 끼워 맞추지 않는다.
"""
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MATERIALS_DIR = Path(__file__).resolve().parent / "materials"

# README §1.1 종합표 그대로 — 파일명 → (판매자, 도메인). 프롬프트에서 재추론하지 않는다.
FILE_META = {
    "kimu": ("키뮤스튜디오", "아트/디자인"),
    "anpoly": ("에이엔폴리", "소재/화학"),
    "cobot": ("코봇시스템", "로봇/의료기기"),
    "mushn": ("머쉬앤", "식품 원료"),
    "livecare": ("라이브케어", "축산 IoT"),
}

# 기존 Judge 온톨로지 (app/engine/prompts.py의 _DIMENSIONS)와 대조하기 위한 참조 —
# 이 목록과 무관한 값을 발견해도 강제 편입하지 않는다(§8 Open — 팀 결정 사항).
KNOWN_DIMENSIONS = {
    "industry_fit", "purpose_alignment", "resource_complementarity",
    "stage_compatibility", "demonstrability", "substitute_comparison",
    "opportunity_cost", "authenticity_gate",
}

_CASE_HEADER = re.compile(
    r"^# 케이스 (?P<case_id>\S+) — (?P<buyer_label>.+?) · "
    r"(?P<direction>in|out)/(?P<lens>sell|buy)/(?P<decision>\w+)\s*$",
    re.MULTILINE)
_SEALED = re.compile(r"\*\*🔒 봉인\*\*:\s*(.+?)(?=\n\n|\n##|\Z)", re.DOTALL)
_ENDING = re.compile(r"\*\*■ 종료:\s*([^*]+)\*\*")
_DIM_SECTION = re.compile(r"## 차원별 매칭[^\n]*\n\n(?P<table>(?:\|.*\n)+)")
_HYP_BLOCK = re.compile(r"```\n(recommendation_frame:.*?)\n```", re.DOTALL)
_HYP_KEYS = ("recommendation_frame", "lens", "statement", "dimension",
             "evidence_needed")


@dataclass
class DimensionRow:
    dimension_raw: str
    known_dimension: bool
    verdict_raw: str
    verdict_tendency: "str | None"   # 휴리스틱(fit/caution/unfit 텍스트 매칭) — 권위 없음
    rationale: str
    rubric: str


@dataclass
class Case:
    file: str
    case_id: str
    seller: str
    domain: str
    buyer_label: str
    direction: str
    lens: str
    decision: str
    sealed_context: str
    ending: str
    dimensions: list = field(default_factory=list)   # list[DimensionRow]
    provenance: dict = field(default_factory=lambda: {
        "seller": "관찰", "counterpart": "구성"})   # README 전역 고지 — 케이스마다 불변


@dataclass
class HypothesisCard:
    file: str
    case_id: str
    seller: str
    domain: str
    recommendation_frame: str
    lens: str
    statement: str
    dimensions: list        # str 목록 (" / " 분리)
    evidence_needed: str


def _clean_cell(s: str) -> str:
    return s.strip().strip("*").strip()


def _verdict_tendency(raw: str) -> "str | None":
    """휴리스틱 극성 태깅 — 표에 쓰인 자유 텍스트가 너무 다양해(예: 'caution→fit',
    '특수', '무관') 규칙 강제 분류를 하지 않는다. 있으면 참고, 없으면 None."""
    low = raw.lower()
    if "unfit" in low:
        return "unfit"
    if "caution" in low:
        return "caution"
    if "fit" in low:
        return "fit"
    return None


def _parse_dimension_table(table_text: str) -> list[DimensionRow]:
    rows = []
    lines = [ln for ln in table_text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return rows
    header_cells = [_clean_cell(c) for c in lines[0].strip("|").split("|")]
    if len(header_cells) < 2 or header_cells[1] != "판정":
        return rows   # 표준 4열 표가 아님 (예: 1-H 두 렌즈 대조표) — 정직하게 스킵
    for line in lines[2:]:                       # [0]=헤더 [1]=구분선
        if not line.strip().startswith("|"):
            continue
        raw_cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(raw_cells) < 2 or not raw_cells[0]:
            continue
        cells = [raw_cells[0]] + [_clean_cell(c) for c in raw_cells[1:]]
        # 렌즈 역할 표기 " *(buy)*" 등을 먼저 제거(공백+이탤릭 감싸기가 신호) —
        # 그래야 "리스크(행정·IP)"처럼 라벨에 실제로 붙은 괄호는 안 건드린다.
        # raw_cells[0](미가공)에서 뽑는다 — _clean_cell의 무조건 strip("*")이
        # 끝쪽 "*"만 먼저 지워버리면 이 정규식이 못 맞아떨어진다.
        dim_raw = re.sub(r"\s*\*\([^)]*\)\*", "", raw_cells[0])
        dim_raw = dim_raw.replace("*", "").strip()
        rows.append(DimensionRow(
            dimension_raw=dim_raw,
            known_dimension=dim_raw in KNOWN_DIMENSIONS,
            verdict_raw=cells[1] if len(cells) > 1 else "",
            verdict_tendency=_verdict_tendency(cells[1] if len(cells) > 1 else ""),
            rationale=cells[2] if len(cells) > 2 else "",
            rubric=cells[3] if len(cells) > 3 else "",
        ))
    return rows


def _parse_hypothesis_block(block: str) -> "dict | None":
    values: dict[str, str] = {}
    current = None
    for line in block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m and m.group(1) in _HYP_KEYS:
            current = m.group(1)
            values[current] = m.group(2).strip()
        elif current:
            values[current] = (values[current] + " " + line.strip()).strip()
    if not all(k in values and values[k] for k in _HYP_KEYS):
        return None
    return values


def parse_file(path: Path) -> tuple[list[Case], list[HypothesisCard], dict]:
    """단일 케이스 파일 → (케이스 목록, 가설카드 목록, 정직 집계).

    집계는 dataset.py의 skip 집계와 같은 원칙 — 놓친 것을 세되 숨기지 않는다.
    """
    text = path.read_text(encoding="utf-8")
    prefix = path.stem.split("_")[0]
    seller, domain = FILE_META.get(prefix, (prefix, "미상"))

    headers = list(_CASE_HEADER.finditer(text))
    stat = {"file": path.name, "case_headers": len(headers),
           "cases_parsed": 0, "sealed_missing": 0, "ending_missing": 0,
           "dimension_table_nonstandard": 0, "dimension_rows_total": 0,
           "unknown_dimension_axes": [], "hypothesis_cards": 0}

    cases: list[Case] = []
    hyps: list[HypothesisCard] = []
    for i, m in enumerate(headers):
        block_start = m.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[block_start:block_end]

        sealed_m = _SEALED.search(block)
        ending_m = _ENDING.search(block)
        if not sealed_m:
            stat["sealed_missing"] += 1
        if not ending_m:
            stat["ending_missing"] += 1

        dim_m = _DIM_SECTION.search(block)
        dims: list[DimensionRow] = []
        if dim_m:
            dims = _parse_dimension_table(dim_m.group("table"))
            if not dims:
                stat["dimension_table_nonstandard"] += 1
        else:
            stat["dimension_table_nonstandard"] += 1
        stat["dimension_rows_total"] += len(dims)
        for d in dims:
            if not d.known_dimension and d.dimension_raw not in stat["unknown_dimension_axes"]:
                stat["unknown_dimension_axes"].append(d.dimension_raw)

        case = Case(
            file=path.name, case_id=m.group("case_id"), seller=seller,
            domain=domain, buyer_label=m.group("buyer_label"),
            direction=m.group("direction"), lens=m.group("lens"),
            decision=m.group("decision"),
            sealed_context=sealed_m.group(1).strip() if sealed_m else "",
            ending=ending_m.group(1).strip() if ending_m else "",
            dimensions=dims,
        )
        cases.append(case)
        stat["cases_parsed"] += 1

        hyp_m = _HYP_BLOCK.search(block)
        if hyp_m:
            values = _parse_hypothesis_block(hyp_m.group(1))
            if values:
                hyps.append(HypothesisCard(
                    file=path.name, case_id=case.case_id, seller=seller,
                    domain=domain,
                    recommendation_frame=values["recommendation_frame"],
                    lens=values["lens"], statement=values["statement"],
                    dimensions=[d.strip() for d in
                               values["dimension"].split("/")],
                    evidence_needed=values["evidence_needed"]))
                stat["hypothesis_cards"] += 1

    return cases, hyps, stat


def build(materials_dir: "str | Path" = MATERIALS_DIR,
          out_dir: "str | Path" = "dataset/ontology") -> dict:
    src = Path(materials_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_cases: list[Case] = []
    all_hyps: list[HypothesisCard] = []
    file_stats = []
    for path in sorted(src.glob("*_judge_ontology_material.md")):
        cases, hyps, stat = parse_file(path)
        all_cases += cases
        all_hyps += hyps
        file_stats.append(stat)

    with open(out / "cases.jsonl", "w", encoding="utf-8") as f:
        for c in all_cases:
            d = asdict(c)
            d["dimensions"] = [asdict(dr) for dr in c.dimensions]
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(out / "hypotheses.jsonl", "w", encoding="utf-8") as f:
        for h in all_hyps:
            f.write(json.dumps(asdict(h), ensure_ascii=False) + "\n")

    unknown_axes = sorted({a for s in file_stats
                           for a in s["unknown_dimension_axes"]})
    summary = {
        "files": len(file_stats),
        "cases_total": len(all_cases),
        "hypotheses_total": len(all_hyps),
        "dimension_rows_total": sum(s["dimension_rows_total"] for s in file_stats),
        "dimension_table_nonstandard": sum(
            s["dimension_table_nonstandard"] for s in file_stats),
        "sealed_missing": sum(s["sealed_missing"] for s in file_stats),
        "candidate_new_axes": unknown_axes,   # §8 Open — 팀 결정 필요, 자동 편입 안 함
        "by_file": file_stats,
    }
    (out / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Judge 온톨로지·가설 라이브러리 추출 (SFT 벽돌 아님 — 설계 재료)")
    ap.add_argument("--materials-dir", default=str(MATERIALS_DIR))
    ap.add_argument("--out-dir", default="dataset/ontology")
    args = ap.parse_args()
    summary = build(args.materials_dir, args.out_dir)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_file"},
                     ensure_ascii=False, indent=2))
    print(f"\n✅ {args.out_dir}/ 에 cases({summary['cases_total']})·"
          f"hypotheses({summary['hypotheses_total']})·report 생성")
    if summary["candidate_new_axes"]:
        print(f"\n📋 새 축 후보(팀 결정 필요, 자동 편입 안 함): "
              f"{', '.join(summary['candidate_new_axes'])}")


if __name__ == "__main__":
    main()
