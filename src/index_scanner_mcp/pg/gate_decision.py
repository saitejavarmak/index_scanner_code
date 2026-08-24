"""Gate decision evaluator for the PostgreSQL Guardrails system."""

from __future__ import annotations

from .models import Action, GateDecision, Severity, Violation


class GateDecisionEvaluator:
    """Determines pass/fail based on violation severities and actions.

    If any violation has action "Block PR", the gate decision is passed=False.
    If no violations have action "Block PR", the gate decision is passed=True.
    """

    def evaluate(self, violations: list[Violation]) -> GateDecision:
        """Determine pass/fail based on violation severities and actions.

        Args:
            violations: List of violations from all analyzers.

        Returns:
            GateDecision with pass/fail status, severity counts, and blocking violations.
        """
        total = len(violations)
        critical_count = sum(
            1 for v in violations if v.severity == Severity.CRITICAL
        )
        high_count = sum(
            1 for v in violations if v.severity == Severity.HIGH
        )
        medium_count = sum(
            1 for v in violations if v.severity == Severity.MEDIUM
        )

        blocking_violations = [
            v for v in violations if v.action == Action.BLOCK_PR
        ]

        passed = len(blocking_violations) == 0

        return GateDecision(
            passed=passed,
            total_violations=total,
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            blocking_violations=blocking_violations,
        )
