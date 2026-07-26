"""HoPE (Hyperbolic Rotary Positional Encoding) 구현 + RoPE 대비 수식 검증.

논문: Dai et al., "HoPE: Hyperbolic Rotary Positional Encoding for Stable
Long-Range Dependency Modeling in LLMs" (arXiv:2509.05218v2).

핵심 주장: RoPE는 삼각함수 주기성 탓에 거리에 따라 어텐션이 **진동**해 장거리
의존을 불안정하게 만든다. HoPE는 Lorentz boost(쌍곡회전)에 감쇠계수를 곱해
어텐션이 거리에 대해 **단조 감쇠**하게 만든다.

학습 없이 검증 가능한 것만 실측한다(중심 주장은 전부 순수 수치):
  C1 군 성질      B(θ,a)B(θ,b) = B(θ,a+b)                    (§8.2.2)
  C2 직교성 차이  RoPE는 노름 보존, HoPE는 변화              (식 26 vs 27)
  C3 모듈러스 경계 B/A ≤ e^θ → 감쇠계수 e^{-θ}의 근거          (식 30·31)
  C4 점근 감쇠    e^{-dθ'}cosh(dθ) ∝ e^{-d(θ'-θ)}, θ'>θ 필요  (식 12)
  C5 단조성       |어텐션|이 거리에 단조 감쇠하는가            (Fig.4/5)
  C6 위치 판별력  ∃k s.t. argmax_s⟨f_q(m),f_k(m+s)⟩ = r       (정리 3.2.2)

수치 안정성(논문에 없는 구현 이슈): e^{-dθ'}·cosh(dθ)를 곧이곧대로 계산하면
cosh가 폭주해 inf가 된다(float64도 dθ>710에서 overflow). 항등식으로 지수를
미리 합쳐 안정화한다:
    e^{-dθ'}cosh(dθ) = ½(e^{d(θ-θ')} + e^{-d(θ+θ')})
    e^{-dθ'}sinh(dθ) = ½(e^{d(θ-θ')} - e^{-d(θ+θ')})
θ' > θ이면 두 지수 모두 음수라 0으로 안전 수렴한다. θ' ≤ max θ_i 이면 발산하는데,
이는 논문 §3.2.1이 요구하는 제약(θ' > max_i θ_i)을 어긴 경우로 C4에서 실측한다.
"""
import numpy as np


# ── 기본 행렬 (식 5·6·23·25) ──────────────────────────────────────────

def rope_matrix(phi: float) -> np.ndarray:
    """RoPE 회전 ρ(φ) — 직교행렬 (식 23)."""
    return np.array([[np.cos(phi), -np.sin(phi)],
                     [np.sin(phi), np.cos(phi)]])


def hope_matrix(phi: float) -> np.ndarray:
    """HoPE 쌍곡회전 B(θ,m) — Lorentz boost 생성자 (식 5·25). 비직교."""
    return np.array([[np.cosh(phi), np.sinh(phi)],
                     [np.sinh(phi), np.cosh(phi)]])


def hope_matrix_key(phi: float) -> np.ndarray:
    """키용 B'(θ,m) (식 6). B'(θ,m) = B(θ,-m)."""
    return np.array([[np.cosh(phi), -np.sinh(phi)],
                     [-np.sinh(phi), np.cosh(phi)]])


def rope_freqs(dim: int, base: float = 10000.0) -> np.ndarray:
    """RoPE 주파수 θ_i = base^{-2i/d} (§8.2.1의 G). θ_0=1이 최대."""
    i = np.arange(0, dim // 2, dtype=np.float64)
    return base ** (-2.0 * i / dim)


# ── 어텐션 점수 (식 9·24) ────────────────────────────────────────────

def rope_score(q: np.ndarray, k: np.ndarray, dist: np.ndarray,
               base: float = 10000.0) -> np.ndarray:
    """RoPE: ⟨R_m q, R_n k⟩ = qᵀρ(dθ)k (식 24). 2D 청크별 합."""
    theta = rope_freqs(q.shape[-1], base)
    q1, q2 = q[0::2], q[1::2]
    k1, k2 = k[0::2], k[1::2]
    phi = dist[:, None] * theta[None, :]
    dot = (q1 * k1 + q2 * k2)[None, :]
    cross = (q2 * k1 - q1 * k2)[None, :]
    return (dot * np.cos(phi) + cross * np.sin(phi)).sum(axis=1)


def hope_score(q: np.ndarray, k: np.ndarray, dist: np.ndarray,
               base: float = 10000.0, theta_prime: float | None = None
               ) -> np.ndarray:
    """HoPE: e^{-dθ'}·qᵀB(θ,d)k (식 9~11). 2D 청크 기여는
        (q1k1+q2k2)cosh(dθ) + (q1k2+q2k1)sinh(dθ)
    이고 전체에 감쇠 e^{-dθ'}가 곱해진다. docstring 항등식으로 안정 계산.

    theta_prime: 논문 §3.2.1은 θ' > max_i θ_i 를 요구한다(위반 시 발산). 기본은
    max(θ)=1.0의 1.1배.
    """
    theta = rope_freqs(q.shape[-1], base)
    tp = float(theta.max() * 1.1) if theta_prime is None else float(theta_prime)
    q1, q2 = q[0::2], q[1::2]
    k1, k2 = k[0::2], k[1::2]
    d = dist[:, None]
    with np.errstate(over="ignore"):   # θ'≤maxθ 위반 케이스는 C4에서 의도적 관찰
        plus = np.exp(d * (theta[None, :] - tp))
        minus = np.exp(-d * (theta[None, :] + tp))
    damped_cosh = 0.5 * (plus + minus)
    damped_sinh = 0.5 * (plus - minus)
    dot = (q1 * k1 + q2 * k2)[None, :]
    cross = (q1 * k2 + q2 * k1)[None, :]
    return (dot * damped_cosh + cross * damped_sinh).sum(axis=1)


def alibi_score(q: np.ndarray, k: np.ndarray, dist: np.ndarray,
                slope: float = 0.05) -> np.ndarray:
    """ALiBi 기준선 — 위치 무관 qᵀk 에 선형 편향 -slope·d."""
    return float(q @ k) - slope * dist


# ── 지표 ─────────────────────────────────────────────────────────────

def magnitude_monotonicity(scores: np.ndarray) -> float:
    """|어텐션|이 단조 감쇠하는 스텝 비율.

    부호가 아니라 **크기**로 잰다 — 논문 주장(Fig.4/5)은 거리가 멀수록 어텐션
    영향력이 줄어든다는 것이라, 음수 점수가 0으로 수렴하는 것도 '감쇠'다.
    부호 기준으로 재면 음수 표본에서 감쇠가 '증가'로 잘못 잡힌다(실측 함정).
    """
    m = np.abs(scores)
    finite = np.isfinite(m)
    if finite.sum() < 2:
        return float("nan")
    return float((np.diff(m[finite]) <= 1e-12).mean())


def sign_flips(scores: np.ndarray) -> int:
    """부호 반전 횟수 — RoPE 진동의 직접 증거."""
    s = scores[np.isfinite(scores)]
    return int((np.diff(np.sign(s)) != 0).sum())


# ── 검증 C1~C6 ───────────────────────────────────────────────────────

def check_group_property() -> None:
    """C1: B(θ,a)B(θ,b) = B(θ,a+b) — 쌍곡회전이 군을 이룬다(§8.2.2)."""
    a, b = 0.7, 1.3
    lhs = hope_matrix(a) @ hope_matrix(b)
    err = np.abs(lhs - hope_matrix(a + b)).max()
    # 키 행렬이 B(θ,-m)인지도 확인 → qᵀB(m)ᵀB'(n)k = qᵀB(m-n)k (식 9의 근거)
    err2 = np.abs(hope_matrix_key(a) - hope_matrix(-a)).max()
    comp = hope_matrix(a).T @ hope_matrix_key(b)
    err3 = np.abs(comp - hope_matrix(a - b)).max()
    print(f"  C1 군 성질 B(a)B(b)=B(a+b)      오차 {err:.2e}  {'✓' if err < 1e-9 else '✗'}")
    print(f"     B'(m)=B(-m)                  오차 {err2:.2e}  {'✓' if err2 < 1e-9 else '✗'}")
    print(f"     B(m)ᵀB'(n)=B(m-n) (식 9 근거) 오차 {err3:.2e}  {'✓' if err3 < 1e-9 else '✗'}")


def check_orthogonality() -> None:
    """C2/C3: RoPE는 노름 보존(식 26), HoPE는 변화(식 27)하고 B/A ≤ e^θ (식 31)."""
    rng = np.random.default_rng(1)
    theta = 0.8
    rope_ratios, hope_ratios = [], []
    for _ in range(2000):
        v = rng.standard_normal(2)
        a = np.linalg.norm(v)
        rope_ratios.append(np.linalg.norm(rope_matrix(theta) @ v) / a)
        hope_ratios.append(np.linalg.norm(hope_matrix(theta) @ v) / a)
    r, h = np.array(rope_ratios), np.array(hope_ratios)
    bound = np.exp(theta)
    print(f"  C2 RoPE 노름비 B/A            [{r.min():.4f}, {r.max():.4f}] "
          f"{'✓ 보존(직교)' if abs(r.max()-1) < 1e-9 else '✗'}")
    print(f"     HoPE 노름비 B/A            [{h.min():.4f}, {h.max():.4f}] "
          f"{'✓ 변화(비직교)' if h.max() > 1.0 else '✗'}")
    print(f"  C3 모듈러스 경계 B/A ≤ e^θ={bound:.4f}  최대 {h.max():.4f}  "
          f"{'✓ 성립' if h.max() <= bound + 1e-9 else '✗ 위반'}")
    print(f"     → 감쇠계수 e^{{-θ}}의 근거(식 32): 최대 증폭을 정확히 상쇄")


def check_asymptotic_decay() -> None:
    """C4: e^{-dθ'}cosh(dθ) ∝ e^{-d(θ'-θ)} (식 12). θ'>θ 필요 — 위반 시 발산."""
    theta, d = 1.0, np.array([50.0, 100.0, 200.0])
    print("  C4 점근 감쇠 (θ=1.0 단일 차원)")
    for tp in (0.5, 1.0, 1.5):
        with np.errstate(over="ignore"):
            val = 0.5 * (np.exp(d * (theta - tp)) + np.exp(-d * (theta + tp)))
        trend = ("발산(제약 위반)" if tp < theta else
                 "무감쇠(경계)" if tp == theta else "지수 감쇠")
        pretty = " ".join(f"{v:.2e}" for v in val)
        print(f"     θ'={tp:<4} d=50/100/200 → {pretty}   {trend}")
    print("     → 논문 §3.2.1의 θ' > max_i θ_i 제약이 실측으로 필수임을 확인")


def check_monotonicity(dim: int = 128, max_dist: int = 4096,
                       trials: int = 200) -> None:
    """C5: |어텐션|의 거리 단조 감쇠 — 논문 Fig.5(가우시안 q,k)에 대응."""
    rng = np.random.default_rng(0)
    dist = np.arange(0, max_dist, dtype=np.float64)
    acc = {"RoPE": [], "HoPE": [], "ALiBi": []}
    flips = {"RoPE": [], "HoPE": [], "ALiBi": []}
    for _ in range(trials):
        q, k = rng.standard_normal(dim), rng.standard_normal(dim)
        for name, s in (("RoPE", rope_score(q, k, dist)),
                        ("HoPE", hope_score(q, k, dist)),
                        ("ALiBi", alibi_score(q, k, dist))):
            acc[name].append(magnitude_monotonicity(s))
            flips[name].append(sign_flips(s))
    print(f"  C5 |어텐션| 단조 감쇠율 (dim={dim}, 거리 0~{max_dist}, {trials}표본)")
    for name in ("RoPE", "HoPE", "ALiBi"):
        v = np.array(acc[name])
        print(f"     {name:6s} {v.mean():.3f} ± {v.std():.3f} · "
              f"부호반전 평균 {np.mean(flips[name]):6.1f}회")
    print("     → RoPE의 낮은 단조성·다수 부호반전 = 논문이 지적한 '진동'")


def check_positional_discrimination(dim: int = 2) -> None:
    """C6: 정리 3.2.2 — 임의 목표거리 r에 대해 argmax_s⟨f_q(m),f_k(m+s)⟩ = r 인 k가 존재.

    구성을 수식으로 직접 푼다(부록 8.1은 'k를 키우면 된다'로 스케치만 한다).

    RoPE: score(d) = qᵀρ(dθ)k. k = ρ(-rθ)q 로 두면 qᵀρ((d-r)θ)q 라 d=r에서 최대.
      (k = ρ(+rθ)q 로 두면 qᵀρ((d+r)θ)q 가 되어 argmax가 d=-r — 흔한 부호 함정)

    HoPE: 단일 2D 청크에서 감쇠까지 전개하면 두 지수의 합이다.
        score(d) = a·e^{-αd} + b·e^{-βd},  α=θ'-θ, β=θ'+θ,  β>α>0
        a = (A+C)/2, b = (A-C)/2, A = q1k1+q2k2, C = q1k2+q2k1
      score'(d)=0 ⟺ e^{(β-α)d} = -bβ/(aα) 이므로 **a와 b의 부호가 반대**일 때만
      내부 극값이 생긴다. d=r에 극대를 두려면 a=1, b = -(α/β)·e^{(β-α)r}.
      그다음 [[q1,q2],[q2,q1]]k = [A,C]ᵀ 를 풀어 k를 얻는다(det=q1²-q2²≠0 필요).
      → 단조 감쇠(C5)와 위치 판별이 양립하는지가 쟁점이라 직접 구성해 확인한다.
    """
    rng = np.random.default_rng(2)
    dist = np.arange(0, 64, dtype=np.float64)
    targets = [1, 3, 8, 20, 40]
    theta = float(rope_freqs(dim).max())          # dim=2면 단일 주파수 θ=1.0
    tp = theta * 1.1
    alpha, beta = tp - theta, tp + theta
    hits_rope = hits_hope = 0
    rows = []
    for r in targets:
        q = rng.standard_normal(dim)
        while abs(q[0] ** 2 - q[1] ** 2) < 1e-6:  # det≠0 보장
            q = rng.standard_normal(dim)
        # RoPE — 부호 수정된 구성. |k|=|q| 로 r과 무관하게 O(1)
        k_rope = np.concatenate([rope_matrix(-r * t) @ q[2 * i:2 * i + 2]
                                 for i, t in enumerate(rope_freqs(dim))])
        ok_r = int(np.argmax(rope_score(q, k_rope, dist))) == r
        hits_rope += ok_r
        # HoPE — 두 지수 합의 내부 극대를 d=r에 놓는 구성
        a = 1.0
        b = -(alpha / beta) * np.exp((beta - alpha) * r)
        A, C = a + b, a - b
        M = np.array([[q[0], q[1]], [q[1], q[0]]])
        k_hope = np.linalg.solve(M, np.array([A, C]))
        ok_h = int(np.argmax(hope_score(q, k_hope, dist, theta_prime=tp))) == r
        hits_hope += ok_h
        rows.append((r, np.linalg.norm(k_rope), np.linalg.norm(k_hope), ok_r, ok_h))
    print(f"  C6 위치 판별력 — 정리 3.2.2 구성적 검증 (dim={dim}, 목표거리 {targets})")
    print(f"     RoPE argmax=r 적중 {hits_rope}/{len(targets)} · "
          f"HoPE {hits_hope}/{len(targets)}")
    print("     거리별 '피크를 그 위치에 두는 데 필요한 키 노름':")
    for r, nr, nh, ok_r, ok_h in rows:
        print(f"       r={r:<3} RoPE |k|={nr:8.3f} {'✓' if ok_r else '✗'}   "
              f"HoPE |k|={nh:.3e} {'✓' if ok_h else '✗'}")
    print("     → HoPE는 |k| ∝ e^{2θr} 로 폭증(r=40에 4e33). 정리는 참이나 float64")
    print("       유효자리(~16)를 넘겨 파국적 상쇄가 나고, 실제 학습 키(노름 O(1~10))")
    print("       로는 도달 불가. 즉 '단조 감쇠'와 '먼 특정 위치 지목'은 상충한다.")


def main() -> None:
    print("=" * 70)
    print("HoPE 논문 수식 실측 검증 — 학습 불필요(순수 수치)")
    print("arXiv:2509.05218v2 · 식 5·6·9~12·23~32, 정리 3.2.2")
    print("=" * 70)
    check_group_property()
    print()
    check_orthogonality()
    print()
    check_asymptotic_decay()
    print()
    check_monotonicity()
    print()
    check_positional_discrimination()
    print("\nHOPE_VERIFY_DONE")


if __name__ == "__main__":
    main()
