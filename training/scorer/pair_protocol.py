"""페어 채점 프로토콜 — 프롬프트·파서 (torch 무의존).

로컬 GPU 백엔드(local_llm)와 API 백엔드(zero_shot_eval --backend friendli)가
'같은 프로토콜'을 강제로 공유한다 (프롬프트 드리프트 방지). 라벨 생성·제로샷
평가·비교 실험 전부 이 한 곳의 프롬프트를 쓴다.
"""
import json
import re

PAIR_SYSTEM = (
    "너는 B2B 매칭 애널리스트다. 두 기업이 '사업 파트너로서 얼마나 "
    "관련(보완) 있는가'를 0~10으로 매긴다. 유사도가 아니라 보완성 기준 — "
    "한쪽의 산출물/역량이 다른 쪽의 결핍/수요를 메우면 높다. 동종 경쟁사는 낮다.\n"
    "0~2=무관/경쟁, 3~5=약한 접점, 6~7=뚜렷한 보완, 8~10=강한 보완.\n"
    '반드시 JSON 하나로만 답하라: {"score": <0~10 정수>, "reason": "<한 문장>"}')


def pair_user(a_name, a_text, b_name, b_text) -> str:
    return (f"[기업 A: {a_name}]\n{a_text[:1200]}\n\n"
            f"[기업 B: {b_name}]\n{b_text[:1200]}\n\nJSON으로 답하라.")


def parse_score(raw) -> "dict | None":
    """모델 출력 → {score, reason}. JSON 실패 시 숫자 폴백, 그래도 없으면 None."""
    try:
        s = raw.find("{"); e = raw.rfind("}")
        d = json.loads(raw[s:e + 1])
        return {"score": max(0, min(10, int(d["score"]))),
                "reason": str(d.get("reason", ""))[:200]}
    except Exception:                              # noqa: BLE001
        m = re.search(r"\b([0-9]|10)\b", raw)
        return {"score": int(m.group(1)), "reason": raw[:120]} if m else None
