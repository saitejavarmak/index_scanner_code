"""Unit tests for the JSONReportGenerator class."""

import json

import pytest

from index_scanner_mcp.pg.json_report_generator import JSONReportGenerator
from index_scanner_mcp.pg.models import (
    Action,
    GateDecision,
    GuardrailResult,
    Severity,
    Violation,
    ViolationCategory,
)


@pytest.fixture
def generator() -> JSONReportGenerator:
    return JSONReportGenerator()


def _make_violation(
    rule_id: str = "PG-MIG-001",
    category: ViolationCategory = ViolationCategory.MIGRATION,
    severity: Severity = Severity.CRITICAL,
    action: Action = Action.BLOCK_PR,
    file_path: str = "migrations/001.sql",
    line_number: int = 5,
    description: str = "CREATE TABLE without PRIMARY KEY",
    remediation: str = "Add a PRIMARY KEY constraint",
    auto_fix_sql: str | None = None,
) -> Violation:
    return Violation(
        rule_id=rule_id,
        category=category,
        severity=severity,
        action=action,
        file_path=file_path,
        line_number=line_number,
        description=description,
        remediation=remediation,
        auto_fix_sql=auto_fix_sql,
    )


def _make_gate_decision(
    passed: bool = False,
    total_violations: int = 1,
    critical_count: int = 1,
    high_count: int = 0,
    medium_count: int = 0,
) -> GateDecision:
    return GateDecision(
        passed=passed,
        total_violations=total_violations,
        critical_count=critical_count,
        high_count=high_count,
        medium_count=medium_count,
    )


def _make_result(
    project_path: str = "/my/project",
    violations: list[Violation] | None = None,
    gate_decision: GateDecision | None = None,
    files_scanned: int = 10,
    migration_files_scanned: int = 3,
    java_files_scanned: int = 5,
    runtime_checks_performed: bool = False,
    errors: list[str] | None = None,
) -> GuardrailResult:
    return GuardrailResult(
        project_path=project_path,
        violations=violations or [],
        gate_decision=gate_decision,
        files_scanned=files_scanned,
        migration_files_scanned=migration_files_scanned,
        java_files_scanned=java_files_scanned,
        runtime_checks_performed=runtime_checks_performed,
        errors=errors or [],
    )


class TestJSONStructure:
    """Test the top-level JSON structure."""

    def test_output_is_valid_json(self, generator: JSONReportGenerator) -> None:
        result = _make_result()
        output = generator.generate(result)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_output_is_indented(self, generator: JSONReportGenerator) -> None:
        result = _make_result()
        output = generator.generate(result)
        # Indented JSON has newlines and leading spaces
        assert "\n" in output
        assert "  " in output

    def test_has_required_top_level_keys(self, generator: JSONReportGenerator) -> None:
        result = _make_result()
        parsed = json.loads(generator.generate(result))
        assert "project_path" in parsed
        assert "gate_decision" in parsed
        assert "summary" in parsed
        assert "violations" in parsed
        assert "errors" in parsed

    def test_project_path_matches(self, generator: JSONReportGenerator) -> None:
        result = _make_result(project_path="/some/path")
        parsed = json.loads(generator.generate(result))
        assert parsed["project_path"] == "/some/path"


class TestGateDecisionSection:
    """Test the gate_decision section of the JSON report."""

    def test_gate_decision_none(self, generator: JSONReportGenerator) -> None:
        result = _make_result(gate_decision=None)
        parsed = json.loads(generator.generate(result))
        assert parsed["gate_decision"] is None

    def test_gate_decision_passed(self, generator: JSONReportGenerator) -> None:
        gate = _make_gate_decision(passed=True, total_violations=0, critical_count=0)
        result = _make_result(gate_decision=gate)
        parsed = json.loads(generator.generate(result))
        assert parsed["gate_decision"]["passed"] is True

    def test_gate_decision_failed(self, generator: JSONReportGenerator) -> None:
        gate = _make_gate_decision(passed=False)
        result = _make_result(gate_decision=gate)
        parsed = json.loads(generator.generate(result))
        assert parsed["gate_decision"]["passed"] is False

    def test_gate_decision_counts(self, generator: JSONReportGenerator) -> None:
        gate = _make_gate_decision(
            total_violations=5, critical_count=2, high_count=2, medium_count=1
        )
        result = _make_result(gate_decision=gate)
        parsed = json.loads(generator.generate(result))
        gd = parsed["gate_decision"]
        assert gd["total_violations"] == 5
        assert gd["critical_count"] == 2
        assert gd["high_count"] == 2
        assert gd["medium_count"] == 1


class TestSummarySection:
    """Test the summary section of the JSON report."""

    def test_summary_files_scanned(self, generator: JSONReportGenerator) -> None:
        result = _make_result(files_scanned=25)
        parsed = json.loads(generator.generate(result))
        assert parsed["summary"]["files_scanned"] == 25

    def test_summary_migration_files(self, generator: JSONReportGenerator) -> None:
        result = _make_result(migration_files_scanned=8)
        parsed = json.loads(generator.generate(result))
        assert parsed["summary"]["migration_files_scanned"] == 8

    def test_summary_java_files(self, generator: JSONReportGenerator) -> None:
        result = _make_result(java_files_scanned=12)
        parsed = json.loads(generator.generate(result))
        assert parsed["summary"]["java_files_scanned"] == 12

    def test_summary_runtime_checks(self, generator: JSONReportGenerator) -> None:
        result = _make_result(runtime_checks_performed=True)
        parsed = json.loads(generator.generate(result))
        assert parsed["summary"]["runtime_checks_performed"] is True


class TestViolationsSection:
    """Test the violations list section of the JSON report."""

    def test_empty_violations(self, generator: JSONReportGenerator) -> None:
        result = _make_result(violations=[])
        parsed = json.loads(generator.generate(result))
        assert parsed["violations"] == []

    def test_violation_fields(self, generator: JSONReportGenerator) -> None:
        v = _make_violation(
            rule_id="PG-SCH-003",
            category=ViolationCategory.SCHEMA,
            severity=Severity.HIGH,
            action=Action.WARN,
            file_path="schema/tables.sql",
            line_number=42,
            description="Missing NOT NULL on boolean column",
            remediation="Add NOT NULL DEFAULT false",
        )
        result = _make_result(violations=[v])
        parsed = json.loads(generator.generate(result))
        viol = parsed["violations"][0]
        assert viol["rule_id"] == "PG-SCH-003"
        assert viol["category"] == "Schema"
        assert viol["severity"] == "High"
        assert viol["action"] == "Warn"
        assert viol["file_path"] == "schema/tables.sql"
        assert viol["line_number"] == 42
        assert viol["description"] == "Missing NOT NULL on boolean column"
        assert viol["remediation"] == "Add NOT NULL DEFAULT false"

    def test_violation_auto_fix_sql_present(self, generator: JSONReportGenerator) -> None:
        v = _make_violation(auto_fix_sql="DROP INDEX idx_dup;")
        result = _make_result(violations=[v])
        parsed = json.loads(generator.generate(result))
        assert parsed["violations"][0]["auto_fix_sql"] == "DROP INDEX idx_dup;"

    def test_violation_auto_fix_sql_absent(self, generator: JSONReportGenerator) -> None:
        v = _make_violation(auto_fix_sql=None)
        result = _make_result(violations=[v])
        parsed = json.loads(generator.generate(result))
        assert parsed["violations"][0]["auto_fix_sql"] is None

    def test_enum_values_serialized_as_strings(self, generator: JSONReportGenerator) -> None:
        v = _make_violation(
            category=ViolationCategory.APPLICATION_CODE,
            severity=Severity.MEDIUM,
            action=Action.AUTO_FIX,
        )
        result = _make_result(violations=[v])
        parsed = json.loads(generator.generate(result))
        viol = parsed["violations"][0]
        assert viol["category"] == "Application Code"
        assert viol["severity"] == "Medium"
        assert viol["action"] == "Auto-Fix"

    def test_multiple_violations_preserved(self, generator: JSONReportGenerator) -> None:
        v1 = _make_violation(rule_id="PG-001")
        v2 = _make_violation(rule_id="PG-002", file_path="other.sql", line_number=10)
        result = _make_result(violations=[v1, v2])
        parsed = json.loads(generator.generate(result))
        assert len(parsed["violations"]) == 2
        assert parsed["violations"][0]["rule_id"] == "PG-001"
        assert parsed["violations"][1]["rule_id"] == "PG-002"


class TestErrorsSection:
    """Test the errors list section of the JSON report."""

    def test_empty_errors(self, generator: JSONReportGenerator) -> None:
        result = _make_result(errors=[])
        parsed = json.loads(generator.generate(result))
        assert parsed["errors"] == []

    def test_errors_preserved(self, generator: JSONReportGenerator) -> None:
        result = _make_result(errors=["Connection failed", "Timeout on EXPLAIN"])
        parsed = json.loads(generator.generate(result))
        assert parsed["errors"] == ["Connection failed", "Timeout on EXPLAIN"]
