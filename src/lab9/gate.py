"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    base_cols = _columns(base, graded)
    problems = [f"baseline: {p}" for p in _report_problems(base, base_cols)]
    # The PR is checked on every graded column the spec asks for that it or the baseline carries:
    # a column the baseline has but the PR dropped is a crashed or removed grader.
    problems += _report_problems(pr, sorted(set(_columns(pr, graded)) | set(base_cols)))
    # Same (item, seed) coverage as the baseline: dropped, added or cherry-picked trials.
    pk, bk = set(_keys(pr)), set(_keys(base))
    if pk != bk:
        missing, extra = sorted(bk - pk), sorted(pk - bk)
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing "
                        f"{_fmt(missing)}, {len(extra)} extra {_fmt(extra)}")
    for key in ("items", "seeds", "eval_set"):
        got, want = (pr.get("manifest") or {}).get(key), (base.get("manifest") or {}).get(key)
        if got != want:
            problems.append(f"manifest {key} {got!r} != baseline {want!r}")
    # Slice relabelling: an item must carry the same slice tags as in the baseline.
    ps, bsl = _slices(pr), _slices(base)
    relabelled = sorted(i for i in set(ps) & set(bsl) if ps[i] != bsl[i])
    if relabelled:
        problems.append(f"slice tags differ from the baseline for {_fmt(relabelled)}")
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def _fmt(xs: list, n: int = 5) -> str:
    return "[" + ", ".join(map(str, xs[:n])) + (", ...]" if len(xs) > n else "]")


def _keys(rep: dict) -> list[tuple]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def _slices(rep: dict) -> dict[str, tuple[str, ...]]:
    out: dict[str, set[str]] = {}
    for r in rep.get("rows") or []:
        out.setdefault(r.get("item_id"), set()).update(r.get("slices") or [])
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _columns(rep: dict, graded: list[str]) -> list[str]:
    """Graded metric columns this report carries (in at least one row). A column that only some
    rows carry is still a column: the rows without it are reported as missing values."""
    rows = rep.get("rows") or []
    return [m for m in graded if any(m in r for r in rows)]


def _bad_number(v) -> bool:
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def _report_problems(rep: dict, graded: list[str]) -> list[str]:
    """Problems inside one report, independent of the other report."""
    problems: list[str] = []
    rows = rep.get("rows") or []
    man = rep.get("manifest") or {}
    metrics = rep.get("metrics") or {}
    for k, v in metrics.items():
        if isinstance(v, bool) or (v is not None and not isinstance(v, (int, float))) or _bad_number(v):
            problems.append(f"metric {k} = {v!r} is not a finite number")
    seeds = man.get("seeds") or []
    want = (man.get("items") or 0) * len(seeds)
    if not rows or len(rows) != want:
        problems.append(f"{len(rows)} rows, manifest says {man.get('items')} items x {len(seeds)} seeds = {want}")
    keys = _keys(rep)
    seen: set = set()
    dups = sorted({k for k in keys if k in seen or seen.add(k)})
    if dups:
        problems.append(f"duplicated (item, seed) rows {_fmt(dups)}")
    if rows and seeds and {k[1] for k in keys} != set(seeds):
        problems.append(f"row seeds {sorted({k[1] for k in keys})} != manifest seeds {sorted(seeds)}")
    if rep.get("evaluator_failures"):
        problems.append(f"{len(rep['evaluator_failures'])} evaluator failure(s), first: {rep['evaluator_failures'][0]}")
    nulls = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
             if r.get(m) is None or _bad_number(r.get(m))]
    if nulls:
        problems.append(f"{len(nulls)} graded value(s) missing/None/NaN, first {nulls[0]}: "
                        "a crashed grader must not drop out of the mean")
    # The headline numbers must be what the rows say: a hand-edited metric is not evidence.
    if rows and not nulls:
        ks = sorted({int(k.rsplit("_", 1)[1]) for k in metrics
                     if k.startswith(("pass_at_", "pass_hat_")) and k.rsplit("_", 1)[1].isdigit()})
        has_cost = all("cost" in r for r in rows)
        ids = [m for m in graded if all(m in r for r in rows)]
        try:
            again = aggregate([r if has_cost else {**r, "cost": 0.0} for r in rows], ids, k_values=ks)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
            problems.append(f"metrics cannot be recomputed from rows ({type(e).__name__}: {e})")
        else:
            derived = {"pass_5"} if "pass_rate" in ids else set()
            for k, v in again.items():
                if k not in metrics or k == "cost_per_success" and not has_cost:
                    continue
                if k not in ids and k not in derived and not k.startswith(("pass_at_", "pass_hat_", "cost_")):
                    continue
                got = metrics[k]
                same = (got is None and v is None) or (
                    got is not None and v is not None and isinstance(got, (int, float))
                    and abs(float(got) - float(v)) <= 1e-9)   # aggregate rounds; honest = exact
                if not same:
                    problems.append(f"metric {k} = {got!r} but its rows give {v!r}")
    return problems


_LOOSER = {"max": lambda new, old: new > old, "min": lambda new, old: new < old,
           "max_increase": lambda new, old: new > old}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not _bad_number(v)


def _cmp_threshold(where: str, new: dict, old: dict) -> list[str]:
    out = []
    for k, looser in _LOOSER.items():
        if k in old:
            if k not in new:
                out.append(f"RATCHET {where}: threshold {k} removed (was {old[k]})")
            elif not _num(new[k]):
                out.append(f"RATCHET {where}: threshold {k} = {new[k]!r} is not a number")
            elif _num(old[k]) and looser(new[k], old[k]):
                out.append(f"RATCHET {where}: {k} loosened {old[k]} -> {new[k]}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        out.append(f"RATCHET {where}: no_significant_drop dropped")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval set => thresholds may only get stricter. A NEW eval set starts over.
    "Same" is decided by content (eval_set + labels files), not by the self-declared version label:
    bumping eval_set_version on the same files is a loosened spec with a new name."""
    same_files = spec.get("eval_set") == base_spec.get("eval_set") and spec.get("labels") == base_spec.get("labels")
    if spec.get("eval_set_version") != base_spec.get("eval_set_version") and not same_files:
        return []
    out: list[str] = []
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        out.append(f"RATCHET eval_set_version renamed {base_spec.get('eval_set_version')!r} -> "
                   f"{spec.get('eval_set_version')!r} on the same eval set and labels")
    new_m = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        n = new_m.get(m["id"])
        if n is None:
            out.append(f"RATCHET metric {m['id']} removed from the spec")
            continue
        if m.get("blocking") and not n.get("blocking"):
            out.append(f"RATCHET metric {m['id']} no longer blocking")
        out += _cmp_threshold(f"metric {m['id']}", n.get("threshold") or {}, m.get("threshold") or {})
    key = lambda sl: (sl.get("name"), sl.get("metric"))
    new_s = {key(sl): sl for sl in spec.get("slices") or []}
    for sl in base_spec.get("slices") or []:
        n = new_s.get(key(sl))
        where = f"slice {sl.get('name')}/{sl.get('metric')}"
        if n is None:
            out.append(f"RATCHET {where} removed from the spec")
            continue
        if sl.get("blocking") and not n.get("blocking"):
            out.append(f"RATCHET {where} no longer blocking")
        out += _cmp_threshold(where, n, sl)
    for k in ("seeds", "eval_set", "labels"):
        if spec.get(k) != base_spec.get(k):
            out.append(f"RATCHET {k} changed on the same eval-set version")
    nb, ob = spec.get("bootstrap") or {}, base_spec.get("bootstrap") or {}
    if nb.get("confidence", 0.95) != ob.get("confidence", 0.95):
        out.append("RATCHET bootstrap confidence changed (a wider CI blocks less)")
    if nb.get("rng", 2027) != ob.get("rng", 2027):
        out.append("RATCHET bootstrap rng changed (re-rolling the resamples until the CI touches 0)")
    if nb.get("n_resamples", 1000) < ob.get("n_resamples", 1000):
        out.append("RATCHET bootstrap n_resamples lowered")
    if nb.get("method", "percentile") != ob.get("method", "percentile"):
        out.append("RATCHET bootstrap method changed")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note = "ok", ""
        if not a:
            # The baseline covers this slice but the PR does not: its evidence went missing (block).
            # Neither covers it (e.g. a slice for a future pack): nothing to compare (warn only).
            verdict, value = ("missing" if b else "empty"), None
        else:
            value = round(sum(a.values()) / len(a), 4)
            if "min" in sl and value < sl["min"]:
                verdict = "fail"
            if "max" in sl and value > sl["max"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict not in ("ok", "empty") and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING", "empty": "warn (no items)"}.get(
            verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<34} {value!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
