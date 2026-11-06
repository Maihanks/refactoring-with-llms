"""Tests for the integrated SEQ-Refactor ordering planner."""

from __future__ import annotations

from planning.rules import PlanningSmell
from planning.seqrefactor import dependency_edges, order_state


def _smell(
    smell_id: str,
    smell_type: str,
    element_id: str,
    severity: int,
) -> PlanningSmell:
    return PlanningSmell(
        id=smell_id,
        type=smell_type,
        element_id=element_id,
        line=1,
        severity=severity,
    )


def test_hard_prerequisite_precedes_higher_impact_contained_smell() -> None:
    state = (
        _smell("class", "God Class", "org.example.OrderService", 1),
        _smell("envy", "Feature Envy", "org.example.OrderService.checkout", 3),
    )

    result = order_state(state)

    assert result.agenda == ("class", "envy")
    assert [(edge.source, edge.target, edge.polarity) for edge in result.edges] == [
        ("class", "envy", "prerequisite")
    ]
    assert result.escalations == ()


def test_signed_positive_relation_does_not_become_a_hard_constraint() -> None:
    state = (
        _smell("long", "Long Method", "org.example.C.outer", 1),
        _smell("envy", "Feature Envy", "org.example.C.outer.inner", 3),
    )

    result = order_state(state)

    assert result.agenda == ("envy", "long")
    assert [(edge.source, edge.target, edge.polarity) for edge in result.edges] == [
        ("long", "envy", "positive")
    ]


def test_sibling_smells_without_a_rule_are_unconstrained_and_deterministic() -> None:
    state = (
        _smell("b", "Feature Envy", "org.example.C.first", 2),
        _smell("a", "Message Chains", "org.example.C.second", 2),
    )

    result = order_state(state)

    assert result.edges == ()
    assert result.agenda == ("a", "b")