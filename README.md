# refactoring with llms
## This is an extended fork of hpietukhin/refactoring-with-llms. It adds the SEQ-Refactor (https://github.com/Maihanks/SEQ-Refactor/) as a fourth planner to the existing planners. The planner comparison experiments in docs/ and experiments/."


Run an experiment case:

```bash
export OPENROUTER_API_KEY="sk-or-..."
uv run python -m experiments.main --case-id "Tap4j:4413ab35b400"
```

Run tests:

```bash
uv run pytest
```
