"""학습 스코어러 HTTP 서빙 (GPU 서버 전용) — 엔진 retrieve의 랭킹 백엔드.

모델(베이스+어댑터+특수 17행)을 한 번 로드해 상주시키고, 배치 채점 엔드포인트를
연다. 엔진(맥)은 SSH 터널로 접근한다(방화벽이 22번만 허용).

  POST /score        {"a_text": …, "b_text": …}            → {"score": 2.8, …}
  POST /score-batch  {"pairs": [{"a_text":…, "b_text":…}]} → {"scores": [{…}]}
  GET  /health       → {"ok": true, "run_dir": …}

실행:
  pip install fastapi uvicorn
  python -m training.scorer.serve --base-model <EXAONE> --run-dir runs/<name> --port 8500
"""
import argparse

from fastapi import FastAPI
from pydantic import BaseModel


class ScoreIn(BaseModel):
    a_text: str
    b_text: str


class BatchIn(BaseModel):
    pairs: list[ScoreIn]


def build_app(base_model: str, run_dir: str, device: str = "cuda") -> FastAPI:
    from .infer import RelatednessScorer
    scorer = RelatednessScorer(base_model, run_dir, device=device)
    app = FastAPI(title="relatedness-scorer")

    @app.get("/health")
    def health():
        return {"ok": True, "run_dir": run_dir, "base_model": base_model}

    @app.post("/score")
    def score(inp: ScoreIn):
        return scorer.score(inp.a_text, inp.b_text)

    @app.post("/score-batch")
    def score_batch(inp: BatchIn):
        # 순차 처리 — 랭킹 용도(수십 건)엔 충분. 대량이면 배치 forward가 후속 과제.
        return {"scores": [scorer.score(p.a_text, p.b_text) for p in inp.pairs]}

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="관련도 스코어러 서빙")
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--port", type=int, default=8500)
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본 로컬 바인드 — 외부 노출은 SSH 터널로만")
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(build_app(a.base_model, a.run_dir), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
