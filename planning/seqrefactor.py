"""SEQ-Refactor priority-queue planner adapted to Havriil's smell state model.

This implements the SEQ ordering policy (prerequisite-safe, impact-forward)
using the ORGANIC findings and element identities already produced here. The
rule pairs and default impact model follow the SEQ-Refactor project:
https://github.com/Maihanks/SEQ-Refactor
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import networkx as nx

from planning.ast.models import AstIndex
from planning.rules import PlanningSmell, State
from smell.smell import Smell

EdgePolarity = Literal["prerequisite", "positive", "negative"]

ORGANIC_TO_SEQ_CATEGORY = {
    "Class Data Should Be Private": "ClassDataShouldBePrivate",
    "Complex Class": "ComplexClass",
    "Feature Envy": "FeatureEnvy",
    "God Class": "GodClass",
    "Lazy Class": "LazyClass",
    "Long Method": "LongMethod",
    "Long Parameter List": "LongParameterList",
    "Message Chains": "MessageChains",
    "Refused Bequest": "RefusedBequest",
    "Speculative Generality": "SpeculativeGenerality",
    "Spaghetti Code": "SpaghettiCode",
    "Dispersed Coupling": "DispersedCoupling",
    "Intensive Coupling": "IntensiveCoupling",
    "Brain Class": "BrainClass",
    "Shotgun Surgery": "ShotgunSurgery",
    "Brain Method": "BrainMethod",
    "Data Class": "DataClass",
}

PRECEDENCE_RULES = {
    ("GodClass", "FeatureEnvy"),
    ("LargeClass", "LongMethod"),
    ("DuplicatedCode", "LongMethod"),
    ("DivergentChange", "ShotgunSurgery"),
    ("MessageChains", "MiddleMan"),
}

SIGNED_RULES = {
    ("LongMethod", "FeatureEnvy"): ("positive", 0.55),
    ("BigSwitch", "DuplicatedCode"): ("positive", 0.50),
    ("DivergentChange", "LargeClass"): ("positive", 0.60),
    ("FeatureEnvy", "ShotgunSurgery"): ("negative", 0.35),
    ("GodClass", "DivergentChange"): ("negative", 0.30),
    ("LargeClass", "MessageChains"): ("negative", 0.40),
}


@dataclass(frozen=True, slots=True)
class SeqEdge:
    source: str
    target: str
    polarity: EdgePolarity
    probability: float = 1.0


@dataclass(frozen=True, slots=True)
class SeqOrdering:
    agenda: tuple[str, ...]
    escalations: tuple[tuple[str, ...], ...]
    impacts: dict[str, float]
    edges: tuple[SeqEdge, ...]


def _seq_element_id(element_id: str) -> str:
    """Normalize Spoon's ``Class#method()`` element IDs to SEQ dotted IDs."""
    return element_id.replace("#", ".").split("(", maxsplit=1)[0]


def _contains(outer: str, inner: str) -> bool:
    return outer != inner and inner.startswith(outer + ".")


def dependency_edges(state: State) -> tuple[SeqEdge, ...]:
    """Build SEQ's containment-scoped prerequisite and signed edges."""
    edges: list[SeqEdge] = []
    elements = {smell.id: _seq_element_id(smell.element_id) for smell in state}
    for source in state:
        for target in state:
            if source.id == target.id or not _contains(
                elements[source.id], elements[target.id]
            ):
                continue
            pair = (
                ORGANIC_TO_SEQ_CATEGORY.get(source.type),
                ORGANIC_TO_SEQ_CATEGORY.get(target.type),
            )
            if pair in PRECEDENCE_RULES:
                edges.append(SeqEdge(source.id, target.id, "prerequisite"))
                continue
            signed = SIGNED_RULES.get(pair)
            if signed is not None:
                polarity, probability = signed
                edges.append(SeqEdge(source.id, target.id, polarity, probability))
            else:
                edges.append(SeqEdge(source.id, target.id, "prerequisite"))
    return tuple(edges)


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-12:
        return dict.fromkeys(values, 0.0)
    return {key: (value - low) / (high - low) for key, value in values.items()}


def impact_scores(state: State, edges: Sequence[SeqEdge]) -> dict[str, float]:
    """SEQ default impact: normalized localization breadth, severity, and degree."""
    coupling = _normalize({smell.id: 1.0 for smell in state})
    complexity = _normalize({smell.id: float(smell.severity) for smell in state})
    degree = {smell.id: 0.0 for smell in state}
    for edge in edges:
        degree[edge.source] = degree.get(edge.source, 0.0) + 1.0
        degree[edge.target] = degree.get(edge.target, 0.0) + 1.0
    cooccurrence = _normalize(degree)
    return {
        smell.id: 0.4 * coupling[smell.id]
        + 0.4 * complexity[smell.id]
        + 0.2 * cooccurrence[smell.id]
        for smell in state
    }


def order_state(state: State) -> SeqOrdering:
    """Return SEQ's deterministic agenda and SCC escalation set for a state."""
    edges = dependency_edges(state)
    impacts = impact_scores(state, edges)
    graph = nx.DiGraph()
    graph.add_nodes_from(smell.id for smell in state)
    graph.add_edges_from(
        (edge.source, edge.target)
        for edge in edges
        if edge.polarity == "prerequisite"
    )
    signed_mass = {smell.id: 0.0 for smell in state}
    for edge in edges:
        if edge.polarity in {"positive", "negative"}:
            signed_mass[edge.source] += edge.probability

    indegree = {node: graph.in_degree(node) for node in graph}
    ready = [
        (-impacts[node], -signed_mass[node], node)
        for node in graph
        if indegree[node] == 0
    ]
    heapq.heapify(ready)
    agenda: list[str] = []
    while ready:
        _, _, source = heapq.heappop(ready)
        agenda.append(source)
        for target in graph.successors(source):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, (-impacts[target], -signed_mass[target], target))

    escalations: list[tuple[str, ...]] = []
    if len(agenda) < graph.number_of_nodes():
        emitted = set(agenda)
        residual = graph.subgraph(node for node in graph if node not in emitted)
        escalations = [
            tuple(sorted(component))
            for component in nx.strongly_connected_components(residual)
            if len(component) > 1
        ]
        condensation = nx.condensation(residual)
        agenda.extend(
            next(iter(condensation.nodes[component]["members"]))
            for component in nx.topological_sort(condensation)
            if len(condensation.nodes[component]["members"]) == 1
        )

    return SeqOrdering(tuple(agenda), tuple(escalations), impacts, edges)


@dataclass(frozen=True, slots=True)
class SeqRefactorPlanner:
    """PlannerProtocol adapter; the harness re-plans after each verified edit."""

    ast_index: AstIndex | None = None

    def plan_next_smell(self, smells: Sequence[Smell], repo_path: Path) -> Smell | None:
        if not smells:
            return None
        from planning.ast.runner import GradleRunner
        from planning.state import state_from_smells
        from repository.repo import Repo

        index = self.ast_index
        if index is None:
            index = GradleRunner(Repo(repo_path)).inspect()
        ordering = order_state(state_from_smells(smells, index, repo_path))
        escalated = {item for component in ordering.escalations for item in component}
        selected_id = next((item for item in ordering.agenda if item not in escalated), None)
        if selected_id is None:
            return None
        return next((smell for smell in smells if smell.id == selected_id), None)


__all__ = [
    "ORGANIC_TO_SEQ_CATEGORY",
    "PRECEDENCE_RULES",
    "SIGNED_RULES",
    "SeqEdge",
    "SeqOrdering",
    "SeqRefactorPlanner",
    "dependency_edges",
    "impact_scores",
    "order_state",
]