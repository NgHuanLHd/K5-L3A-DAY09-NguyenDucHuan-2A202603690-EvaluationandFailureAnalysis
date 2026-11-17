"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Sequence

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def _norm(label: str) -> str:
    v = str(label).strip().upper()
    if v not in ("PASS", "FAIL"):
        raise ValueError(f"label must be PASS or FAIL, got {label!r}")
    return v


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    if len(human) != len(judge):
        raise ValueError(f"length mismatch: {len(human)} human vs {len(judge)} judge labels")
    out = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for h, j in zip(human, judge):
        h_pos, j_pos = _norm(h) == POS, _norm(j) == POS
        out["tp" if h_pos and j_pos else "fp" if j_pos else "fn" if h_pos else "tn"] += 1
    return out


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    c = confusion(human, judge)
    precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
    recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    c = confusion(human, judge)
    n = sum(c.values())
    if n == 0:
        raise ValueError("kappa undefined: no labels")
    p_o = (c["tp"] + c["tn"]) / n
    h_pos, j_pos = (c["tp"] + c["fn"]) / n, (c["tp"] + c["fp"]) / n
    p_e = h_pos * j_pos + (1 - h_pos) * (1 - j_pos)
    if math.isclose(p_e, 1.0):
        raise ValueError("kappa undefined: chance agreement is 1 (constant labels)")
    return (p_o - p_e) / (1 - p_e)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    denom = sensitivity + specificity - 1
    if math.isclose(denom, 0.0, abs_tol=1e-12):
        # Judge no better than chance (e.g. a constant judge): its output carries no information
        # about the true rate, so no correction is possible. Report the observed rate (clipped)
        # instead of crashing calibration; kappa/P/R already make such a judge FAIL the floors.
        return min(1.0, max(0.0, float(observed_rate)))
    return min(1.0, max(0.0, (observed_rate + specificity - 1) / denom))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    import numpy as np
    from scipy.stats import bootstrap

    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.shape != y.shape or x.ndim != 1 or x.size == 0:
        raise ValueError("paired_bootstrap needs two non-empty sequences of the same length")
    if np.array_equal(x, y):
        return (0.0, 0.0, 0.0)
    d = float(x.mean() - y.mean())
    if np.all(x - y == (x - y)[0]):            # every pair differs by the same amount: no spread
        return (d, d, d)
    res = bootstrap((x, y), lambda u, v, axis=-1: np.mean(u, axis=axis) - np.mean(v, axis=axis),
                    paired=True, vectorized=True, method="percentile", n_resamples=n_resamples,
                    confidence_level=confidence, rng=np.random.default_rng(rng))
    return (d, float(res.confidence_interval.low), float(res.confidence_interval.high))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    n, c, k = _check_nck(n, c, k)
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    n, c, k = _check_nck(n, c, k)
    return math.comb(c, k) / math.comb(n, k)


def _as_count(name: str, v) -> int:
    """Accept ints, numpy ints and integral floats (5.0); reject bools, 5.5, NaN, strings."""
    if isinstance(v, bool):
        raise ValueError(f"{name} must be an integer count, got {v!r}")
    try:
        return operator.index(v)
    except TypeError:
        pass
    if isinstance(v, float) and v.is_integer():
        return int(v)
    raise ValueError(f"{name} must be an integer count, got {v!r}")


def _check_nck(n, c, k) -> tuple[int, int, int]:
    n, c, k = _as_count("n", n), _as_count("c", c), _as_count("k", k)
    if n < 1 or k < 1 or c < 0 or c > n or k > n:
        raise ValueError(f"invalid (n, c, k) = ({n}, {c}, {k}): need 1 <= k <= n and 0 <= c <= n")
    return n, c, k
