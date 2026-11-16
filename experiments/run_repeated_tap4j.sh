#!/usr/bin/env bash
# Repeated four-planner comparison on Tap4j:462ab4c8c308 (Pi harness).
#
# Runs ROUNDS rounds; each round runs every planner once, in an order rotated
# per round so no planner always runs first or last. Runs are sequential so
# wall-time measurements are not distorted by contention.
#
# Each finished run is appended to $INDEX as: round<TAB>planner<TAB>run_id<TAB>exit
# Re-running the script skips (round, planner) pairs already in $INDEX.
#
# Requires OPENROUTER_API_KEY, e.g. in the gitignored mise.local.toml.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ROUNDS="${ROUNDS:-5}"
CASE_ID="Tap4j:462ab4c8c308"
RUNS_DIR="data/pi/runs/tupilabs_tap4j"
INDEX="${INDEX:-data/pi/repeated/tap4j_runs.tsv}"
PLANNERS=(greedy topo bfs seqrefactor)

export PATH="$HOME/.sdkman/candidates/gradle/8.14.3/bin:$HOME/.local/bin:$PATH"
export JDK_JAVA_OPTIONS="--add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.lang=ALL-UNNAMED"

mkdir -p "$(dirname "$INDEX")" "$RUNS_DIR"
touch "$INDEX"

for ((round = 1; round <= ROUNDS; round++)); do
  for ((i = 0; i < ${#PLANNERS[@]}; i++)); do
    planner="${PLANNERS[$(((i + round - 1) % ${#PLANNERS[@]}))]}"
    if awk -F'\t' -v r="$round" -v p="$planner" '$1==r && $2==p && $4==0 {f=1} END {exit !f}' "$INDEX"; then
      echo "[skip] round $round $planner (already done)"
      continue
    fi
    before="$(ls "$RUNS_DIR")"
    echo "[$(date -Is)] round $round/$ROUNDS planner=$planner"
    node --experimental-strip-types experiments/pi_batch.ts dataset/manifest_eval6.jsonl \
      --case-id "$CASE_ID" --planner "$planner" --concurrency 1 --quiet --timeout-ms 5400000
    code=$?
    run_id="$(comm -13 <(echo "$before") <(ls "$RUNS_DIR") | tail -1)"
    printf '%s\t%s\t%s\t%s\n' "$round" "$planner" "${run_id:-none}" "$code" >> "$INDEX"
    echo "[$(date -Is)] done round $round $planner run_id=${run_id:-none} exit=$code"
  done
done
