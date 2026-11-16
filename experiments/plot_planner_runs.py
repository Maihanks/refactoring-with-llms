"""Figures and significance tests for repeated planner runs.

Input is the JSON written by ``experiments.aggregate_planner_runs``.

    uv run --with scipy python -m experiments.plot_planner_runs \
        --runs docs/figures/planner-comparison-repeated/runs.json \
        --baseline-ck docs/figures/planner-comparison/results.json \
        --out-dir docs/figures/planner-comparison-repeated
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ORDER = ("seqrefactor", "greedy", "topo", "bfs")
NAMES = {"seqrefactor": "SEQ-Refactor", "greedy": "DAPS", "topo": "TopS", "bfs": "BeFS"}
COLORS = {"seqrefactor": "#2a6fdb", "greedy": "#e07b28", "topo": "#2e9e5b", "bfs": "#c8414b"}
CK_ROWS = ("cbo", "wmc", "rfc", "lcom", "loc", "totalMethodsQty")

TESTED = {
    "total_introduced": "Smells introduced",
    "remaining_end": "Smells left at end",
    "edits_to_clear_original": "Verifications to clear originals",
    "cost_usd": "LLM cost (USD)",
    "turns": "LLM turns",
    "wall_min": "Wall time (min)",
    "pass_rate": "Verification pass rate",
    "verifications": "Verifications",
    "cooccurring_methods": "Methods with ≥2 introduced smells",
}


def _by_planner(runs: list[dict]) -> dict[str, list[dict]]:
    return {p: [r for r in runs if r["planner"] == p] for p in ORDER if any(r["planner"] == p for r in runs)}


def significance(runs: list[dict]) -> dict:
    """Kruskal–Wallis across planners, then pairwise Mann–Whitney U with Holm correction."""
    from scipy import stats

    groups = _by_planner(runs)
    out: dict[str, dict] = {}
    for metric in TESTED:
        vals = {p: [r[metric] for r in rs if r.get(metric) is not None] for p, rs in groups.items()}
        vals = {p: v for p, v in vals.items() if v}
        entry: dict = {"label": TESTED[metric]}
        flat = [x for v in vals.values() for x in v]
        if len(vals) >= 2 and len(set(flat)) > 1 and all(len(v) >= 2 for v in vals.values()):
            h, p = stats.kruskal(*vals.values())
            entry["kruskal"] = {"H": float(h), "p": float(p)}
            pairs = []
            for a, b in itertools.combinations(vals, 2):
                if len(set(vals[a] + vals[b])) == 1:
                    pairs.append({"a": a, "b": b, "U": None, "p": 1.0})
                    continue
                u, pv = stats.mannwhitneyu(vals[a], vals[b], alternative="two-sided")
                # Cliff's delta effect size
                gt = sum(x > y for x in vals[a] for y in vals[b])
                lt = sum(x < y for x in vals[a] for y in vals[b])
                delta = (gt - lt) / (len(vals[a]) * len(vals[b]))
                pairs.append({"a": a, "b": b, "U": float(u), "p": float(pv), "cliffs_delta": delta})
            # Holm–Bonferroni
            ranked = sorted(range(len(pairs)), key=lambda i: pairs[i]["p"])
            m = len(pairs)
            running = 0.0
            for rank, i in enumerate(ranked):
                adj = min(1.0, (m - rank) * pairs[i]["p"])
                running = max(running, adj)
                pairs[i]["p_holm"] = running
            entry["pairwise"] = pairs
        else:
            entry["kruskal"] = None
        out[metric] = entry
    return out


def _bar(ax, groups, metric, title, scale=1.0, fmt="{:.1f}"):
    xs = np.arange(len(groups))
    for i, (p, rs) in enumerate(groups.items()):
        v = np.array([r[metric] for r in rs if r.get(metric) is not None], dtype=float) * scale
        if not len(v):
            continue
        sd = v.std(ddof=1) if len(v) > 1 else 0
        ax.bar(i, v.mean(), color=COLORS[p], alpha=0.75, yerr=sd, capsize=4)
        ax.scatter(np.full(len(v), i) + np.linspace(-0.12, 0.12, len(v)), v, color="black", s=12, zorder=3)
        ax.text(i, v.mean(), fmt.format(v.mean()), ha="center", va="bottom", fontsize=8,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1))
    ax.set_xticks(xs, [NAMES[p] for p in groups])
    ax.set_title(title, fontsize=10)
    ax.grid(axis="y", alpha=0.3)


def plot(runs: list[dict], baseline_ck: dict | None, out: Path) -> None:
    groups = _by_planner(runs)
    out.mkdir(parents=True, exist_ok=True)

    # Fig 1: open smells after each verification (mean ± SD across runs)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for p, rs in groups.items():
        series = [[t["remaining_original"] + t["active_introduced"] for t in r["timeline"]] for r in rs]
        length = max(len(s) for s in series)
        mat = np.full((len(series), length), np.nan)
        for i, s in enumerate(series):
            mat[i, : len(s)] = s
            mat[i, len(s):] = s[-1] if s else np.nan  # a finished run stays at its final count
        mean, sd = np.nanmean(mat, 0), np.nanstd(mat, 0)
        x = np.arange(1, length + 1)
        ax.plot(x, mean, color=COLORS[p], label=f"{NAMES[p]} (n={len(rs)})")
        ax.fill_between(x, np.maximum(mean - sd, 0), mean + sd, color=COLORS[p], alpha=0.12)
    ax.set_xlabel("Verification #")
    ax.set_ylabel("Open smells (original + introduced)")
    ax.set_title("Open smells after each verification — mean ± SD across runs")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "fig1_open_smells_trajectory.png", dpi=150)
    plt.close(fig)

    # Fig 2: smells introduced / remaining / verifications to clear
    fig, axs = plt.subplots(1, 3, figsize=(12, 3.8))
    _bar(axs[0], groups, "total_introduced", "Smells introduced (distinct type+owner)")
    _bar(axs[1], groups, "remaining_end", "Smells left at end")
    _bar(axs[2], groups, "edits_to_clear_original", "Verifications until originals cleared")
    fig.suptitle("Smell outcomes — bar = mean, whisker = SD, dots = runs", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "fig2_smells_resolved_introduced.png", dpi=150)
    plt.close(fig)

    # Fig 3: execution cost
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.8))
    _bar(axs[0], groups, "wall_min", "Wall time (min)")
    _bar(axs[1], groups, "cost_usd", "LLM cost (US cents)", scale=100)
    _bar(axs[2], groups, "turns", "LLM turns", fmt="{:.0f}")
    _bar(axs[3], groups, "pass_rate", "Verification pass rate (%)", scale=100)
    fig.tight_layout()
    fig.savefig(out / "fig3_execution_cost.png", dpi=150)
    plt.close(fig)

    # Fig 4: CK delta on target classes
    if baseline_ck:
        base = baseline_ck["target_sum"]
        fig, ax = plt.subplots(figsize=(10, 4))
        w = 0.8 / len(groups)
        for j, (p, rs) in enumerate(groups.items()):
            deltas = np.array([[100 * (r["ck"]["target_sum"][m] - base[m]) / base[m] for m in CK_ROWS]
                               for r in rs if r.get("ck")])
            if not len(deltas):
                continue
            x = np.arange(len(CK_ROWS)) + (j - (len(groups) - 1) / 2) * w
            sd = deltas.std(0, ddof=1) if len(deltas) > 1 else None
            ax.bar(x, deltas.mean(0), w, yerr=sd, capsize=3, color=COLORS[p], alpha=0.8, label=NAMES[p])
        ax.set_xticks(np.arange(len(CK_ROWS)), ["CBO", "WMC", "RFC", "LCOM", "LOC", "Methods"])
        ax.axhline(0, color="black", lw=0.8)
        ax.set_ylabel("Δ% vs baseline (sum of 3 target classes)")
        ax.set_title("CK change on refactored classes — mean ± SD across runs")
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / "fig4_ck_target_delta.png", dpi=150)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--baseline-ck", type=Path, help="results.json with ck.baseline from the single-run report")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    data = json.loads(a.runs.read_text())
    baseline = json.loads(a.baseline_ck.read_text())["ck"]["baseline"] if a.baseline_ck else None
    plot(data["runs"], baseline, a.out_dir)
    sig = significance(data["runs"])
    (a.out_dir / "significance.json").write_text(json.dumps(sig, indent=1))
    for m, e in sig.items():
        k = e.get("kruskal")
        print(f"{e['label']:38s}", f"H={k['H']:.2f} p={k['p']:.4f}" if k else "n/a")


if __name__ == "__main__":
    main()
