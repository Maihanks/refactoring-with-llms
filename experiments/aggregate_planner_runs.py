"""Extract per-run metrics from Pi-harness run logs and aggregate repeated runs.

Usage:
    uv run python -m experiments.aggregate_planner_runs \
        --runs-dir data/pi/runs/tupilabs_tap4j \
        --case-id "Tap4j:462ab4c8c308" \
        --out docs/figures/planner-comparison-repeated/runs.json

Each run directory must contain ``all.log`` (Eliot JSON lines). The planner is
read from the ``pi_batch:manifest_resume`` event. Per-class CK metrics are read
from the ``class.csv``/``method.csv`` written to the ``ck:metrics`` output dir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

TARGETS = (
    "org.tap4j.consumer.TestTap13Consumer",
    "org.tap4j.model.BailOut",
    "org.tap4j.producer.TestTap13YamlProducer",
)
CK_FIELDS = ("cbo", "fanin", "fanout", "wmc", "rfc", "lcom", "loc", "totalMethodsQty", "maxNestedBlocksQty")


def _events(log: Path):
    with log.open() as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _logical_key(smell_type: str, owner: str) -> str:
    payload = json.dumps({"type": smell_type, "owner": owner}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ck(output_dir: str | None) -> dict | None:
    if not output_dir:
        return None
    d = Path(output_dir)
    cls, meth = d / "class.csv", d / "method.csv"
    if not cls.exists():
        return None
    rows = [r for r in csv.DictReader(cls.open()) if "/build/" not in r["file"]]
    targets = {r["class"]: {f: float(r[f]) for f in CK_FIELDS} for r in rows if r["class"] in TARGETS}
    target_sum = {f: sum(t[f] for t in targets.values()) for f in CK_FIELDS}
    methods = []
    if meth.exists():
        methods = [r for r in csv.DictReader(meth.open()) if r["class"] in TARGETS and "/build/" not in r["file"]]
    mwmc = [float(m["wmc"]) for m in methods]
    mloc = [float(m["loc"]) for m in methods]
    whole = {
        "classes": len(rows),
        "cbo": statistics.fmean(float(r["cbo"]) for r in rows),
        "wmc": statistics.fmean(float(r["wmc"]) for r in rows),
        "lcom": statistics.fmean(float(r["lcom"]) for r in rows),
        "loc": sum(float(r["loc"]) for r in rows),
    }
    return {
        "targets": targets,
        "target_sum": target_sum,
        "target_methods": {
            "count": len(methods),
            "mean_method_wmc": statistics.fmean(mwmc) if mwmc else None,
            "max_method_wmc": max(mwmc, default=None),
            "mean_method_loc": statistics.fmean(mloc) if mloc else None,
            "max_method_loc": max(mloc, default=None),
        },
        "whole_project": whole,
    }


def extract_run(run_dir: Path, case_id: str) -> dict | None:
    log = run_dir / "all.log"
    if not log.exists():
        return None
    planner = None
    start = end = None
    cost = None
    summary = None
    ck_dir = None
    turns = []
    verifies = []
    introduced = {}  # logical (type, owner) -> smell
    churn = set()
    ast = []
    refactor_completions = 0
    max_attempt = 0
    smell_type_by_key = {}
    introduced_keys: set[str] = set()
    logical_keys: list[str] = []
    smells_path = run_dir / "smells.json"
    entries = {}
    if smells_path.exists():
        sj = json.loads(smells_path.read_text())
        entries = sj.get("entries", {})
        introduced_keys = set(sj.get("introduced_identity_keys", []))
        logical_keys = list(sj.get("introduced_logical_keys", []))
        for k, e in entries.items():
            smell_type_by_key[k] = (e.get("smell") or {}).get("type")
    picks = []
    for e in _events(log):
        mt = e.get("message_type")
        if e.get("case_id") not in (None, case_id) and mt != "pi_batch:manifest_resume":
            continue
        if mt == "deep:case_start":
            start = e["timestamp"]
        elif mt == "pi:llm_turn":
            turns.append(e)
            max_attempt = max(max_attempt, e.get("attempt_number") or 0)
        elif mt == "pi:verify":
            verifies.append(e)
        elif mt == "pi:refactoring_complete":
            refactor_completions += 1
            picks.append((e.get("smell_key"), e.get("attempt_number")))
        elif mt == "pi:smells_introduced":
            for s in e.get("smells", []):
                introduced.setdefault((s["type"], s["owner"]), s)
                churn.add(s["identity_key"])
                smell_type_by_key.setdefault(s["identity_key"], s["type"])
        elif mt == "planner:ast_indexed":
            ast.append(e)
        elif mt == "pi:case_cost":
            cost = e
        elif mt == "deep:case_summary":
            summary = e
        elif mt == "ck:metrics":
            ck_dir = e.get("output_dir")
            end = e["timestamp"]
        elif mt == "pi_batch:manifest_resume":
            planner = e.get("planner")
    if summary is None or start is None:
        return None
    end = end or summary["timestamp"]
    original = summary["original_smells"]
    timeline = []
    for v in verifies:
        rem_orig = original - v["fixed_smells"]
        timeline.append(
            {
                "t_min": (v["timestamp"] - start) / 60,
                "passed": v["passed"],
                "remaining_original": rem_orig,
                "active_introduced": v["introduced_smells"],
                "total_introduced": v["total_introduced_smells"],
            }
        )
    # Same definition as the single-run report: index of the first verification
    # (passed or not) after which no original smell remained open.
    edits_to_clear = next((i + 1 for i, t in enumerate(timeline) if t["remaining_original"] == 0), None)
    # Introduced smells: all entries the harness marked as introduced, deduped by
    # logical identity (type, owning member); churn counts location hashes.
    # The harness's logical key is sha256({type, owner}); a baseline smell that
    # disappears and later reappears counts as introduced, as in the harness.
    by_logical = {_logical_key(e["smell"]["type"], e["owner"]): e for e in entries.values()}
    if logical_keys:
        introduced = {}
        for lk in logical_keys:
            en = by_logical.get(lk)
            if en:
                introduced[(en["smell"]["type"], en["owner"])] = en
    churn = introduced_keys or churn
    owners_types = Counter(owner for (_t, owner) in introduced)
    by_class = Counter(owner.rsplit(".", 2)[-2] for (_t, owner) in introduced)
    models = Counter(t["model"] for t in turns)
    remaining_end = summary["remaining_smells"]
    return {
        "run_id": run_dir.name,
        "planner": planner,
        "stop_reason": summary["stop_reason"],
        "original": original,
        "fixed_original": summary["fixed_smells"],
        "total_introduced": summary["total_introduced_smells"],
        "fixed_introduced": summary["fixed_introduced_smells"],
        "remaining_end": remaining_end,
        "cleared": remaining_end == 0,
        "wall_min": (end - start) / 60,
        "cost_usd": (cost or summary)["cost_usd"],
        "tokens_in": (cost or summary)["input_tokens"],
        "tokens_out": (cost or summary)["output_tokens"],
        "cache_read": (cost or summary)["cache_read_tokens"],
        "turns": (cost or summary)["turn_count"],
        "models": dict(models),
        "refactor_completions": refactor_completions,
        "max_attempt": max_attempt,
        "verifications": len(verifies),
        "verify_passed": sum(v["passed"] for v in verifies),
        "verify_test_fail": sum(not v["tests_acceptable"] for v in verifies),
        "verify_smell_still": sum(bool(v["current_smell_present"]) and v["tests_acceptable"] for v in verifies),
        "pass_rate": (sum(v["passed"] for v in verifies) / len(verifies)) if verifies else None,
        "planner_invocations": len(ast),
        "edits_to_clear_original": edits_to_clear,
        "first_pick_type": smell_type_by_key.get(picks[0][0]) if picks else None,
        # First smell chosen by the planner (the first task is sent in detection order).
        "first_planner_pick_type": smell_type_by_key.get(picks[1][0]) if len(picks) > 1 else None,
        "pick_sequence": [smell_type_by_key.get(k) for k, _ in picks],
        "introduced_types": dict(Counter(t for (t, _o) in introduced)),
        "introduced_owners": len(owners_types),
        "cooccurring_methods": sum(1 for c in owners_types.values() if c >= 2),
        "introduced_by_class": dict(by_class),
        "churn_identity_keys": len(churn),
        "timeline": timeline,
        "ck": _ck(ck_dir),
    }


def _stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if not n:
        return {"n": 0}
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs) if n > 1 else 0.0
    # 95% CI with Student t (two-sided)
    t = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}.get(n - 1, 1.96)
    half = t * sd / math.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "mean": mean, "sd": sd, "median": statistics.median(xs), "min": min(xs), "max": max(xs),
            "ci95": [mean - half, mean + half], "values": xs}


METRICS = (
    "total_introduced", "fixed_introduced", "remaining_end", "wall_min", "cost_usd", "turns", "tokens_in",
    "tokens_out", "cache_read", "verifications", "verify_passed", "verify_test_fail", "verify_smell_still",
    "pass_rate", "planner_invocations", "edits_to_clear_original", "introduced_owners", "cooccurring_methods",
    "churn_identity_keys", "fixed_original",
)


def aggregate(runs: list[dict]) -> dict:
    out = {}
    for planner in sorted({r["planner"] for r in runs}):
        rs = [r for r in runs if r["planner"] == planner]
        agg = {m: _stats([r.get(m) for r in rs]) for m in METRICS}
        agg["cleared_count"] = sum(r["cleared"] for r in rs)
        agg["stop_reasons"] = dict(Counter(r["stop_reason"] for r in rs))
        agg["first_pick_types"] = dict(Counter(r["first_pick_type"] for r in rs))
        agg["first_planner_pick_types"] = dict(Counter(r["first_planner_pick_type"] for r in rs))
        agg["run_ids"] = [r["run_id"] for r in rs]
        cks = [r["ck"] for r in rs if r["ck"]]
        agg["ck_target_sum"] = {f: _stats([c["target_sum"][f] for c in cks]) for f in CK_FIELDS}
        agg["ck_target_methods"] = {
            f: _stats([c["target_methods"][f] for c in cks])
            for f in ("count", "mean_method_wmc", "max_method_wmc", "mean_method_loc", "max_method_loc")
        }
        agg["ck_whole"] = {f: _stats([c["whole_project"][f] for c in cks]) for f in ("cbo", "wmc", "lcom", "loc")}
        out[planner] = agg
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", type=Path, default=Path("data/pi/runs/tupilabs_tap4j"))
    ap.add_argument("--case-id", default="Tap4j:462ab4c8c308")
    ap.add_argument("--run-ids", nargs="*", help="Restrict to these run directory names")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    runs = []
    for d in sorted(a.runs_dir.iterdir()):
        if a.run_ids and d.name not in a.run_ids:
            continue
        r = extract_run(d, a.case_id)
        if r and r["planner"]:
            runs.append(r)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"runs": runs, "aggregate": aggregate(runs)}, indent=1))
    for p, g in aggregate(runs).items():
        print(f"{p:12s} n={g['wall_min']['n']} intro={g['total_introduced']['mean']:.1f} "
              f"cost={g['cost_usd']['mean']*100:.2f}c turns={g['turns']['mean']:.0f} "
              f"wall={g['wall_min']['mean']:.1f} pass={g['pass_rate']['mean']:.3f} cleared={g['cleared_count']}")


if __name__ == "__main__":
    main()
