"""JSON report generator for PostgreSQL Guardrails.

Produces a machine-readable JSON summary of guardrail analysis results
for integration with other tools and CI/CD pipelines.
"""

from __future__ import annotations

import json
from typing import Any

from .models import GuardrailResult, Violation


class JSONReportGenerator:
    """Generates a machine-readable JSON report from guardrail analysis results."""

    def generate(self, result: GuardrailResult) -> str:
        """Produce a JSON string summarizing guardrail analysis results.

        Args:
            result: The aggregated guardrail analysis result.

        Returns:
            A formatted JSON string (indented with 2 spaces).
        """
        report: dict[str, Any] = {
            "project_path": result.project_path,
            "gate_decision": self._build_gate_decision(result),
            "summary": self._build_summary(result),
            "violations": [
                self._build_violation(v) for v in result.violations
            ],
            "errors": result.errors,
        }

        return json.dumps(report, indent=2)

    def _build_gate_decision(self, result: GuardrailResult) -> dict[str, Any] | None:
        """Build the gate_decision section of the JSON report."""
        if result.gate_decision is None:
            return None

        return {
            "passed": result.gate_decision.passed,
            "total_violations": result.gate_decision.total_violations,
            "critical_count": result.gate_decision.critical_count,
            "high_count": result.gate_decision.high_count,
            "medium_count": result.gate_decision.medium_count,
        }

    def _build_summary(self, result: GuardrailResult) -> dict[str, Any]:
        """Build the summary section of the JSON report."""
        return {
            "files_scanned": result.files_scanned,
            "migration_files_scanned": result.migration_files_scanned,
            "java_files_scanned": result.java_files_scanned,
            "runtime_checks_performed": result.runtime_checks_performed,
        }

    def _build_violation(self, violation: Violation) -> dict[str, Any]:
        """Build a single violation entry for the JSON report."""
        entry: dict[str, Any] = {
            "rule_id": violation.rule_id,
            "category": violation.category.value,
            "severity": violation.severity.value,
            "action": violation.action.value,
            "file_path": violation.file_path,
            "line_number": violation.line_number,
            "description": violation.description,
            "remediation": violation.remediation,
            "auto_fix_sql": violation.auto_fix_sql,
        }
        return entry
