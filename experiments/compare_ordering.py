"""Compare initial planner agendas on a single pinned manifest case, without an LLM."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

from dataset import Dataset
from detection.organic import OrganicDetector
from planning.ast.models import AstIndex
from planning.ast.relations import AstRelations
from planning.ast.runner import GradleRunner
from planning.greedy import GreedyPlanner
from planning.organic_rules import organic_rules
from planning.planner import PriorityPlanner
from planning.seqrefactor import SeqRefactorPlanner, order_state
from planning.state import state_from_smells
from planning.topo import TopoPlanner, preference_graph
from repository.repo import Repo, worktree_path
from workflows.composite.nodes.detect import filter_smells_to_elements


def pairwise_agreement(first: list[str], second: list[str]) -> dict[str, Any]:
    first_position = {item: index for index, item in enumerate(first)}
    second_position = {item: index for index, item in enumerate(second)}
    common = [item for item in first if item in second_position]
    concordant = 0
    discordant = 0
    for index, left in enumerate(common):
        for right in common[index + 1 :]:
            if (first_position[left] < first_position[right]) == (
                second_position[left] < second_position[right]
            ):
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    differences = [abs(first_position[item] - second_position[item]) for item in common]
    return {
        "items": len(common),
        "first_coverage": len(first),
        "second_coverage": len(second),
        "pairs": total,
        "concordant": concordant,
        "discordant": discordant,
        "kendall_tau": (concordant - discordant) / total if total else None,
        "agreement_fraction": concordant / total if total else None,
        "mean_absolute_rank_displacement": sum(differences) / len(differences)
        if differences
        else None,
    }


def planner_agenda(planner: Any, smells: list, repo_path: Path) -> list[str]:
    remaining = list(smells)
    agenda: list[str] = []
    while remaining:
        selected = planner.plan_next_smell(remaining, repo_path)
        if selected is None:
            break
        agenda.append(selected.id)
        remaining = [smell for smell in remaining if smell.id != selected.id]
    return agenda


def preference_audit(agenda: list[str], graph: Any) -> dict[str, int]:
    positions = {item: index for index, item in enumerate(agenda)}
    condensation = nx.condensation(graph)
    component_by_node = {
        node: component
        for component, data in condensation.nodes(data=True)
        for node in data["members"]
    }
    within_scc = 0
    cross_scc = 0
    cross_scc_edges = 0
    for source, target in graph.edges:
        if positions[source] < positions[target]:
            continue
        if component_by_node[source] == component_by_node[target]:
            within_scc += 1
        else:
            cross_scc += 1
    for source, target in graph.edges:
        cross_scc_edges += int(component_by_node[source] != component_by_node[target])
    return {
        "edges": graph.number_of_edges(),
        "within_scc_violations": within_scc,
        "cross_scc_edges": cross_scc_edges,
        "cross_scc_violations": cross_scc,
        "cyclic_components": sum(
            len(data["members"]) > 1 for _, data in condensation.nodes(data=True)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("dataset/manifest_eval6.jsonl"),
    )
    parser.add_argument("--case-id", default="Tap4j:462ab4c8c308")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--ast-index", type=Path, default=None)
    args = parser.parse_args()

    cases = Dataset.from_manifest(args.manifest).load_cases()
    case = next((item for item in cases if item.case_id == args.case_id), None)
    if case is None:
        raise SystemExit(f"No case_id={args.case_id!r} in {args.manifest}")

    repo = Repo.for_experiment(
        case.repo_url,
        case.start_commit,
        checkout_path=worktree_path(case.repo_url),
    )
    if args.ast_index is None:
        ast_index = GradleRunner(repo).inspect()
    else:
        ast_index = AstIndex.model_validate_json(
            args.ast_index.read_text(encoding="utf-8")
        )

    feedback = OrganicDetector().detect_feedback(repo.path, commit_hash=case.start_commit)
    selected = set(
        filter_smells_to_elements([entry.smell for entry in feedback], case.elements)
    )
    feedback = [entry for entry in feedback if entry.smell in selected]
    smells = [entry.smell for entry in feedback]
    state = state_from_smells(smells, ast_index, repo.path)
    relations = AstRelations.from_index(ast_index)
    havriil_graph = preference_graph(state, organic_rules, relations)

    planners = {
        "daps": GreedyPlanner(ast_index=ast_index),
        "topological": TopoPlanner(ast_index=ast_index),
        "befs": PriorityPlanner(ast_index=ast_index),
        "seqrefactor": SeqRefactorPlanner(ast_index=ast_index),
    }
    agendas = {
        name: planner_agenda(planner, smells, repo.path)
        for name, planner in planners.items()
    }
    labels = {
        entry.smell.id: {
            "type": entry.smell.type,
            "owner": entry.owner,
            "line": entry.smell.location.range.start.line + 1,
        }
        for entry in feedback
    }
    seq_result = order_state(state)
    positions = {
        name: {item: index for index, item in enumerate(agenda)}
        for name, agenda in agendas.items()
    }
    seq_predecessors = [
        edge
        for edge in seq_result.edges
        if edge.polarity == "prerequisite"
    ]
    seq_violations = sum(
        positions["seqrefactor"][edge.source] >= positions["seqrefactor"][edge.target]
        for edge in seq_predecessors
    )
    output = {
        "mode": "fixed-baseline-order-only; no Java edits or LLM calls",
        "case_id": case.case_id,
        "repo_url": case.repo_url,
        "start_commit": case.start_commit,
        "elements": case.elements,
        "finding_count": len(smells),
        "finding_types": dict(Counter(smell.type for smell in smells)),
        "agendas": {
            name: [{"id": item, **labels[item]} for item in agenda]
            for name, agenda in agendas.items()
        },
        "agenda_coverage": {
            name: {
                "ordered": len(agenda),
                "initial_findings": len(smells),
                "remaining_ids": sorted(set(labels) - set(agenda)),
            }
            for name, agenda in agendas.items()
        },
        "pairwise_agreement": {
            f"{first}_vs_{second}": pairwise_agreement(agendas[first], agendas[second])
            for first, second in (
                ("seqrefactor", "daps"),
                ("seqrefactor", "topological"),
                ("seqrefactor", "befs"),
            )
        },
        "native_constraints": {
            "seqrefactor": {
                "dependency_edges": len(seq_result.edges),
                "prerequisite_edges": len(seq_predecessors),
                "prerequisite_violations": seq_violations,
                "escalations": [list(component) for component in seq_result.escalations],
            },
            "havriil_topological": preference_audit(agendas["topological"], havriil_graph),
        },
    }
    output_path = args.output or Path("data/experiments/output") / f"{case.case_id.replace(':', '_')}.ordering.json"
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parents[1] / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "case_id": case.case_id, "findings": len(smells)}, indent=2))


if __name__ == "__main__":
    main()