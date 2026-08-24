"""PostgreSQL Guardrail Engine - orchestrates all analyzers and produces results.

This module provides the PostgresGuardrailEngine class which is the main
entry point for running a complete guardrail analysis on a project directory.
It discovers files, invokes all analyzers, computes the gate decision, and
generates HTML and JSON reports.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from index_scanner_mcp.pg.application_code_scanner import ApplicationCodeScanner
from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.gate_decision import GateDecisionEvaluator
from index_scanner_mcp.pg.html_report_generator import HTMLReportGenerator
from index_scanner_mcp.pg.index_analyzer import IndexAnalyzer
from index_scanner_mcp.pg.json_report_generator import JSONReportGenerator
from index_scanner_mcp.pg.migration_scanner import MigrationScanner
from index_scanner_mcp.pg.models import Action, GuardrailResult, Severity, Violation, ViolationCategory
from index_scanner_mcp.pg.performance_scanner import PerformanceScanner
from index_scanner_mcp.pg.schema_analyzer import SchemaAnalyzer

logger = logging.getLogger(__name__)


class PostgresGuardrailEngine:
    """Orchestrate all PostgreSQL guardrail analyzers and produce a combined result.

    Discovers SQL migration files and Java source files in the project,
    runs each analyzer, aggregates violations, computes the gate decision,
    and generates HTML/JSON reports.
    """

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config
        self._migration_scanner = MigrationScanner(config)
        self._schema_analyzer = SchemaAnalyzer(config)
        self._index_analyzer = IndexAnalyzer(config)
        self._performance_scanner = PerformanceScanner(config)
        self._application_code_scanner = ApplicationCodeScanner(config)
        self._gate_evaluator = GateDecisionEvaluator()
        self._html_report_generator = HTMLReportGenerator()
        self._json_report_generator = JSONReportGenerator()

    def run_analysis(self, project_path: str) -> GuardrailResult:
        """Orchestrate all analyzers and produce a combined result.

        Args:
            project_path: Path to the project directory to analyze.

        Returns:
            A GuardrailResult containing all violations, gate decision,
            scan counts, and any errors encountered.
        """
        # Validate project path
        path = Path(project_path)
        if not path.exists():
            return GuardrailResult(
                project_path=project_path,
                errors=[f"Project path does not exist: {project_path}"],
            )

        if not path.is_dir():
            return GuardrailResult(
                project_path=project_path,
                errors=[f"Project path is not a directory: {project_path}"],
            )

        # Discover files
        migration_files = self._discover_migration_files(project_path)
        java_files = self._discover_java_files(project_path)

        all_violations: list[Violation] = []
        errors: list[str] = []

        # Run MigrationScanner on each migration file
        for filepath in migration_files:
            try:
                violations = self._migration_scanner.scan_file(filepath)
                all_violations.extend(violations)
            except Exception as e:
                errors.append(
                    f"MigrationScanner error on '{filepath}': {e}"
                )

        # Run SchemaAnalyzer on each migration file
        for filepath in migration_files:
            try:
                violations = self._schema_analyzer.analyze_file(filepath)
                all_violations.extend(violations)
            except Exception as e:
                errors.append(
                    f"SchemaAnalyzer error on '{filepath}': {e}"
                )

        # Run IndexAnalyzer on each migration file
        for filepath in migration_files:
            try:
                violations = self._index_analyzer.analyze_file(filepath)
                all_violations.extend(violations)
            except Exception as e:
                errors.append(
                    f"IndexAnalyzer error on '{filepath}': {e}"
                )

        # Run PerformanceScanner on each migration file
        for filepath in migration_files:
            try:
                violations = self._performance_scanner.scan_file(filepath)
                all_violations.extend(violations)
            except Exception as e:
                errors.append(
                    f"PerformanceScanner error on '{filepath}': {e}"
                )

        # Run ApplicationCodeScanner on each Java file
        for filepath in java_files:
            try:
                violations = self._application_code_scanner.scan_file(filepath)
                all_violations.extend(violations)
            except Exception as e:
                errors.append(
                    f"ApplicationCodeScanner error on '{filepath}': {e}"
                )

        # Filter out disabled rules from aggregated violations
        all_violations = [
            v for v in all_violations
            if v.rule_id not in self._config.disabled_rules
        ]

        # Apply severity overrides
        all_violations = self._apply_severity_overrides(all_violations)

        # Run optional runtime validations against Aurora (if configured)
        runtime_violations, runtime_checks_performed = self._run_runtime_validations(all_violations)
        all_violations.extend(runtime_violations)

        # Compute gate decision
        gate_decision = self._gate_evaluator.evaluate(all_violations)

        # Build result
        result = GuardrailResult(
            project_path=project_path,
            violations=all_violations,
            gate_decision=gate_decision,
            files_scanned=len(migration_files) + len(java_files),
            migration_files_scanned=len(migration_files),
            java_files_scanned=len(java_files),
            runtime_checks_performed=runtime_checks_performed,
            errors=errors,
        )

        return result

    def generate_html_report(self, result: GuardrailResult) -> str:
        """Generate an HTML report from the analysis result.

        Args:
            result: The GuardrailResult from run_analysis.

        Returns:
            A complete HTML document string.
        """
        return self._html_report_generator.generate(result)

    def generate_json_report(self, result: GuardrailResult) -> str:
        """Generate a JSON report from the analysis result.

        Args:
            result: The GuardrailResult from run_analysis.

        Returns:
            A formatted JSON string.
        """
        return self._json_report_generator.generate(result)

    def _discover_migration_files(self, project_path: str) -> list[str]:
        """Discover all SQL migration files in the project directory.

        Recursively finds all .sql files in the project directory tree.

        Args:
            project_path: Root directory to search.

        Returns:
            A sorted list of absolute file paths to SQL files.
        """
        path = Path(project_path)
        sql_files: list[str] = []

        try:
            for sql_file in sorted(path.rglob("*.sql")):
                if sql_file.is_file():
                    sql_files.append(str(sql_file))
        except (OSError, PermissionError) as e:
            # Gracefully handle unreadable directories
            pass

        return sql_files

    def _discover_java_files(self, project_path: str) -> list[str]:
        """Discover all Java source files in the project directory.

        Recursively finds all .java files in the project directory tree.

        Args:
            project_path: Root directory to search.

        Returns:
            A sorted list of absolute file paths to Java files.
        """
        path = Path(project_path)
        java_files: list[str] = []

        try:
            for java_file in sorted(path.rglob("*.java")):
                if java_file.is_file():
                    java_files.append(str(java_file))
        except (OSError, PermissionError) as e:
            # Gracefully handle unreadable directories
            pass

        return java_files

    def _run_runtime_validations(
        self, violations: list[Violation]
    ) -> tuple[list[Violation], bool]:
        """Run optional runtime validations against Aurora PostgreSQL.

        If ``aurora_connection`` is set in the configuration, connects to the
        Aurora instance, queries ``pg_stat_user_indexes`` for unused indexes,
        and converts each result to a ``Violation``.

        Connection failures are handled gracefully:
        - ``AuroraConnectionError``: logged as a warning; returns empty list
          with ``runtime_checks_performed=False``.
        - ``ImportError`` (psycopg2 not installed): logged as a warning;
          returns empty list.

        Args:
            violations: Current list of violations (for context, not modified).

        Returns:
            A tuple of (list of runtime violations, runtime_checks_performed flag).
            The flag is ``True`` only when the connection succeeded and checks ran.
        """
        if self._config.aurora_connection is None:
            return [], False

        try:
            from index_scanner_mcp.pg.aurora_connector import (
                AuroraConnectionError,
                AuroraConnector,
            )
        except ImportError:
            logger.warning(
                "psycopg2 is not installed; skipping Aurora runtime validation. "
                "Install it with: pip install psycopg2-binary"
            )
            return [], False

        connector = AuroraConnector(self._config.aurora_connection)
        runtime_violations: list[Violation] = []

        try:
            with connector.session() as conn:
                unused_indexes = conn.get_unused_indexes()
                for unused in unused_indexes:
                    runtime_violations.append(
                        Violation(
                            rule_id="PG_IDX_014",
                            category=ViolationCategory.INDEX,
                            severity=Severity.MEDIUM,
                            action=Action.WARN,
                            file_path="runtime:pg_stat_user_indexes",
                            line_number=0,
                            description=(
                                f"Index '{unused.index_name}' on table "
                                f"'{unused.table_name}' has never been used "
                                f"(idx_scan=0, size={unused.index_size})"
                            ),
                            remediation=(
                                "Consider dropping this index to reduce write overhead. "
                                "Verify it is truly unused before dropping."
                            ),
                        )
                    )
        except AuroraConnectionError as exc:
            logger.warning(
                "Aurora connection failed; skipping runtime validation. "
                "Static analysis results are unaffected. Error: %s",
                exc,
            )
            return [], False

        return runtime_violations, True

    def _apply_severity_overrides(
        self, violations: list[Violation]
    ) -> list[Violation]:
        """Apply configured severity overrides to violations.

        Args:
            violations: List of violations to process.

        Returns:
            The same violations with severity levels adjusted per config.
        """
        from index_scanner_mcp.pg.models import Severity

        overrides = self._config.severity_overrides
        if not overrides:
            return violations

        result: list[Violation] = []
        for v in violations:
            if v.rule_id in overrides:
                override_value = overrides[v.rule_id]
                try:
                    new_severity = Severity(override_value)
                    # Create a new violation with the overridden severity
                    v = Violation(
                        rule_id=v.rule_id,
                        category=v.category,
                        severity=new_severity,
                        action=v.action,
                        file_path=v.file_path,
                        line_number=v.line_number,
                        description=v.description,
                        remediation=v.remediation,
                        auto_fix_sql=v.auto_fix_sql,
                        explain_output=v.explain_output,
                    )
                except ValueError:
                    # Invalid severity value in config, keep original
                    pass
            result.append(v)

        return result
