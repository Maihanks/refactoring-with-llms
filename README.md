# refactoring with llms

> **This is an extended fork of [hpietukhin/refactoring-with-llms](https://github.com/hpietukhin/refactoring-with-llms)** by Havriil Pietukhin.
> It adds [SEQ-Refactor](https://github.com/Maihanks/SEQ-Refactor) as a fourth smell-ordering planner next to the existing DAPS, TopS and BeFS planners, plus planner comparison experiments.

## What this fork adds

- **SEQ-Refactor planner**: integrated as a fourth planner in [`planning/`](planning/).
- **Planner comparison on tap4j**: [`docs/planner-comparison-tap4j.md`](docs/planner-comparison-tap4j.md) (single run) and [`docs/planner-comparison-tap4j-repeated.md`](docs/planner-comparison-tap4j-repeated.md) (5 runs per planner, with significance tests).
- **SEQ-Refactor ordering study**: [`docs/seqrefactor-tap4j-ordering.md`](docs/seqrefactor-tap4j-ordering.md).
- **Experiment scripts**: repeated-run runner, aggregation and plotting in [`experiments/`](experiments/); see [`docs/reproducing.md`](docs/reproducing.md).

## Citing

See [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository" button). If you use this work, please also credit the original repository by Havriil Pietukhin.

## Usage

Run an experiment case:

```bash
export OPENROUTER_API_KEY="sk-or-..."
uv run python -m experiments.main --case-id "Tap4j:4413ab35b400"
```

Run tests:

```bash
uv run pytest
```
