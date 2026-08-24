"""Unit tests for PostgresGuardrailEngine Aurora runtime validation wiring.

Covers the two cases specified by task 16.2:
- No Aurora config → runtime_checks_performed=False, no runtime violations added
- Aurora config provided but connection fails → runtime_checks_performed=False,
  no violations added and no exception propagated to the caller
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from index_scanner_mcp.pg.aurora_connector import AuroraConnectionError
from index_scanner_mcp.pg.config_loader import AuroraConnectionConfig, GuardrailConfig
from index_scanner_mcp.pg.engine import PostgresGuardrailEngine
from index_scanner_mcp.pg.models import (
    Action,
    GuardrailResult,
    Severity,
    UnusedIndex,
    ViolationCategory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(aurora_connection: AuroraConnectionConfig | None = None) -> GuardrailConfig:
    """Return a minimal GuardrailConfig, optionally with Aurora config."""
    return GuardrailConfig(aurora_connection=aurora_connection)


def _make_aurora_config() -> AuroraConnectionConfig:
    """Return a minimal AuroraConnectionConfig pointing at a fake host."""
    return AuroraConnectionConfig(
        host="fake-aurora.cluster.us-east-1.rds.amazonaws.com",
        database="testdb",
        username="scanner",
    )


# ---------------------------------------------------------------------------
# _run_runtime_validations unit tests
# ---------------------------------------------------------------------------

class TestRunRuntimeValidationsNoConfig:
    """When no aurora_connection is configured, runtime checks are skipped."""

    def test_returns_empty_violations_and_false(self):
        engine = PostgresGuardrailEngine(_make_config(aurora_connection=None))
        violations, performed = engine._run_runtime_validations([])
        assert violations == []
        assert performed is False

    def test_does_not_attempt_import_of_aurora_connector(self):
        """No import of AuroraConnector should happen when config is absent."""
        engine = PostgresGuardrailEngine(_make_config(aurora_connection=None))
        with patch("index_scanner_mcp.pg.aurora_connector.AuroraConnector") as mock_cls:
            engine._run_runtime_validations([])
            mock_cls.assert_not_called()


class TestRunRuntimeValidationsConnectionFailure:
    """When Aurora config is provided but the connection fails, checks are skipped gracefully."""

    def _engine_with_aurora(self) -> PostgresGuardrailEngine:
        return PostgresGuardrailEngine(_make_config(_make_aurora_config()))

    def test_connection_error_returns_empty_and_false(self):
        engine = self._engine_with_aurora()
        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector"
        ) as MockConnector:
            instance = MockConnector.return_value
            # session() raises AuroraConnectionError on __enter__
            instance.session.return_value.__enter__.side_effect = AuroraConnectionError(
                "Connection timed out"
            )
            instance.session.return_value.__exit__ = MagicMock(return_value=False)

            violations, performed = engine._run_runtime_validations([])

        assert violations == []
        assert performed is False

    def test_connection_error_does_not_propagate(self):
        """AuroraConnectionError must not bubble up — engine should swallow it."""
        engine = self._engine_with_aurora()
        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector"
        ) as MockConnector:
            instance = MockConnector.return_value
            instance.session.return_value.__enter__.side_effect = AuroraConnectionError(
                "Authentication failed"
            )
            instance.session.return_value.__exit__ = MagicMock(return_value=False)

            # Must not raise
            try:
                engine._run_runtime_validations([])
            except AuroraConnectionError:
                pytest.fail(
                    "_run_runtime_validations should not propagate AuroraConnectionError"
                )

    def test_import_error_returns_empty_and_false(self):
        """When psycopg2 is not installed, the ImportError is handled gracefully.

        We simulate this by making the 'from ... import AuroraConnector' raise ImportError.
        """
        engine = self._engine_with_aurora()
        # Patch the import statement inside engine._run_runtime_validations by
        # temporarily replacing the aurora_connector module in sys.modules with a
        # module object that raises ImportError on attribute access.
        import sys
        import types

        broken_module = types.ModuleType("index_scanner_mcp.pg.aurora_connector")
        broken_module.__spec__ = None  # satisfy import machinery
        # Accessing AuroraConnector or AuroraConnectionError raises ImportError
        def _raise(*a, **kw):
            raise ImportError("No module named 'psycopg2'")

        # We need the import to fail, which we do by replacing the module
        real_module = sys.modules.get("index_scanner_mcp.pg.aurora_connector")
        sys.modules["index_scanner_mcp.pg.aurora_connector"] = None  # type: ignore[assignment]
        try:
            violations, performed = engine._run_runtime_validations([])
        finally:
            if real_module is not None:
                sys.modules["index_scanner_mcp.pg.aurora_connector"] = real_module

        assert violations == []
        assert performed is False


class TestRunRuntimeValidationsSuccess:
    """When the connection succeeds, unused-index violations are produced correctly."""

    def _engine_with_aurora(self) -> PostgresGuardrailEngine:
        return PostgresGuardrailEngine(_make_config(_make_aurora_config()))

    def _mock_connector(self, unused_indexes):
        """Return a context manager mock for AuroraConnector.session()."""
        mock_conn = MagicMock()
        mock_conn.get_unused_indexes.return_value = unused_indexes

        mock_instance = MagicMock()
        mock_instance.session.return_value.__enter__.return_value = mock_conn
        mock_instance.session.return_value.__exit__.return_value = False
        return mock_instance

    def test_unused_indexes_become_violations(self):
        engine = self._engine_with_aurora()

        unused = UnusedIndex(
            index_name="idx_orders_status",
            table_name="orders",
            index_size="8192 bytes",
            idx_scan=0,
        )

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=self._mock_connector([unused]),
        ):
            violations, performed = engine._run_runtime_validations([])

        assert performed is True
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_id == "PG_IDX_014"
        assert v.category == ViolationCategory.INDEX
        assert v.severity == Severity.MEDIUM
        assert v.action == Action.WARN
        assert v.file_path == "runtime:pg_stat_user_indexes"
        assert v.line_number == 0
        assert "idx_orders_status" in v.description
        assert "orders" in v.description
        assert "8192 bytes" in v.description
        assert "dropping" in v.remediation.lower()

    def test_no_unused_indexes_returns_empty_and_true(self):
        engine = self._engine_with_aurora()

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=self._mock_connector([]),
        ):
            violations, performed = engine._run_runtime_validations([])

        # Connection succeeded → performed=True even if no violations found
        assert performed is True
        assert violations == []

    def test_multiple_unused_indexes_produce_multiple_violations(self):
        engine = self._engine_with_aurora()

        unused_list = [
            UnusedIndex("idx_a", "table_a", "4 kB"),
            UnusedIndex("idx_b", "table_b", "8 kB"),
            UnusedIndex("idx_c", "table_c", "16 kB"),
        ]

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=self._mock_connector(unused_list),
        ):
            violations, performed = engine._run_runtime_validations([])

        assert performed is True
        assert len(violations) == 3
        rule_ids = {v.rule_id for v in violations}
        assert rule_ids == {"PG_IDX_014"}


# ---------------------------------------------------------------------------
# run_analysis integration: runtime_checks_performed propagation
# ---------------------------------------------------------------------------

class TestRunAnalysisRuntimeChecksPerformed:
    """Verify that run_analysis propagates runtime_checks_performed correctly."""

    def _minimal_project(self, tmp_path):
        """Create a minimal project directory (empty, no SQL/Java files)."""
        return str(tmp_path)

    def _mock_connector_instance(self, unused_indexes):
        """Return a pre-built AuroraConnector mock instance."""
        mock_conn = MagicMock()
        mock_conn.get_unused_indexes.return_value = unused_indexes

        mock_instance = MagicMock()
        mock_instance.session.return_value.__enter__.return_value = mock_conn
        mock_instance.session.return_value.__exit__.return_value = False
        return mock_instance

    def test_no_aurora_config_runtime_checks_false(self, tmp_path):
        engine = PostgresGuardrailEngine(_make_config(aurora_connection=None))
        result = engine.run_analysis(self._minimal_project(tmp_path))
        assert result.runtime_checks_performed is False

    def test_aurora_config_connection_fails_runtime_checks_false(self, tmp_path):
        engine = PostgresGuardrailEngine(_make_config(_make_aurora_config()))

        failing_instance = MagicMock()
        failing_instance.session.return_value.__enter__.side_effect = AuroraConnectionError(
            "Simulated failure"
        )
        failing_instance.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=failing_instance,
        ):
            result = engine.run_analysis(self._minimal_project(tmp_path))

        assert result.runtime_checks_performed is False
        # No runtime violations should have leaked into the result
        runtime_violations = [
            v for v in result.violations if v.file_path == "runtime:pg_stat_user_indexes"
        ]
        assert runtime_violations == []

    def test_aurora_config_connection_fails_no_exception_in_run_analysis(self, tmp_path):
        """run_analysis must complete normally even when Aurora is unreachable."""
        engine = PostgresGuardrailEngine(_make_config(_make_aurora_config()))

        failing_instance = MagicMock()
        failing_instance.session.return_value.__enter__.side_effect = AuroraConnectionError(
            "Simulated timeout"
        )
        failing_instance.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=failing_instance,
        ):
            try:
                result = engine.run_analysis(self._minimal_project(tmp_path))
            except Exception as exc:
                pytest.fail(
                    f"run_analysis raised an unexpected exception: {exc}"
                )

        assert isinstance(result, GuardrailResult)

    def test_aurora_config_success_runtime_checks_true(self, tmp_path):
        engine = PostgresGuardrailEngine(_make_config(_make_aurora_config()))

        with patch(
            "index_scanner_mcp.pg.aurora_connector.AuroraConnector",
            return_value=self._mock_connector_instance(
                [UnusedIndex("idx_test", "t", "1 kB")]
            ),
        ):
            result = engine.run_analysis(self._minimal_project(tmp_path))

        assert result.runtime_checks_performed is True
        runtime_violations = [
            v for v in result.violations if v.file_path == "runtime:pg_stat_user_indexes"
        ]
        assert len(runtime_violations) == 1
        assert runtime_violations[0].rule_id == "PG_IDX_014"
