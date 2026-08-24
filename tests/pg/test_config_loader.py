"""Unit tests for the PostgreSQL Guardrails ConfigLoader."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from index_scanner_mcp.pg.config_loader import (
    AuroraConnectionConfig,
    ConfigLoader,
    GuardrailConfig,
    ThresholdConfig,
)


class TestThresholdConfig:
    """Tests for ThresholdConfig dataclass."""

    def test_defaults(self):
        config = ThresholdConfig()
        assert config.large_table_row_count == 100_000
        assert config.offset_limit == 10_000
        assert config.index_size_limit_mb == 1024
        assert config.varchar_max_length == 4000
        assert config.composite_pk_max_columns == 3

    def test_custom_values(self):
        config = ThresholdConfig(
            large_table_row_count=500_000,
            offset_limit=5000,
            index_size_limit_mb=2048,
            varchar_max_length=8000,
            composite_pk_max_columns=5,
        )
        assert config.large_table_row_count == 500_000
        assert config.offset_limit == 5000
        assert config.index_size_limit_mb == 2048
        assert config.varchar_max_length == 8000
        assert config.composite_pk_max_columns == 5

    def test_negative_large_table_row_count(self):
        with pytest.raises(ValueError, match="large_table_row_count"):
            ThresholdConfig(large_table_row_count=-1)

    def test_negative_offset_limit(self):
        with pytest.raises(ValueError, match="offset_limit"):
            ThresholdConfig(offset_limit=-1)

    def test_negative_index_size_limit(self):
        with pytest.raises(ValueError, match="index_size_limit_mb"):
            ThresholdConfig(index_size_limit_mb=-1)

    def test_negative_varchar_max_length(self):
        with pytest.raises(ValueError, match="varchar_max_length"):
            ThresholdConfig(varchar_max_length=-1)

    def test_zero_composite_pk_max_columns(self):
        with pytest.raises(ValueError, match="composite_pk_max_columns"):
            ThresholdConfig(composite_pk_max_columns=0)


class TestAuroraConnectionConfig:
    """Tests for AuroraConnectionConfig dataclass."""

    def test_minimal(self):
        config = AuroraConnectionConfig(host="db.example.com")
        assert config.host == "db.example.com"
        assert config.port == 5432
        assert config.database == ""
        assert config.username == ""
        assert config.password_env_var == "AURORA_PG_PASSWORD"
        assert config.ssl_mode == "require"

    def test_full_config(self):
        config = AuroraConnectionConfig(
            host="aurora.example.com",
            port=5433,
            database="mydb",
            username="admin",
            password_env_var="MY_PG_PASS",
            ssl_mode="verify-full",
        )
        assert config.host == "aurora.example.com"
        assert config.port == 5433
        assert config.database == "mydb"
        assert config.username == "admin"
        assert config.password_env_var == "MY_PG_PASS"
        assert config.ssl_mode == "verify-full"

    def test_empty_host_raises(self):
        with pytest.raises(ValueError, match="host"):
            AuroraConnectionConfig(host="")

    def test_invalid_port_too_high(self):
        with pytest.raises(ValueError, match="port"):
            AuroraConnectionConfig(host="h", port=70000)

    def test_invalid_port_too_low(self):
        with pytest.raises(ValueError, match="port"):
            AuroraConnectionConfig(host="h", port=0)

    def test_invalid_ssl_mode(self):
        with pytest.raises(ValueError, match="ssl_mode"):
            AuroraConnectionConfig(host="h", ssl_mode="invalid")

    def test_get_password_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_PG_PASS", "secret123")
        config = AuroraConnectionConfig(
            host="h", password_env_var="TEST_PG_PASS"
        )
        assert config.get_password() == "secret123"

    def test_get_password_missing_env(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        config = AuroraConnectionConfig(
            host="h", password_env_var="NONEXISTENT_VAR"
        )
        assert config.get_password() is None


class TestGuardrailConfig:
    """Tests for GuardrailConfig dataclass."""

    def test_defaults(self):
        config = GuardrailConfig()
        assert config.database_type == "postgresql"
        assert config.severity_overrides == {}
        assert config.disabled_rules == []
        assert isinstance(config.thresholds, ThresholdConfig)
        assert config.index_naming_pattern is None
        assert config.rollback_directory is None
        assert config.rollback_naming_convention is None
        assert config.aurora_connection is None

    def test_invalid_database_type(self):
        with pytest.raises(ValueError, match="database_type"):
            GuardrailConfig(database_type="mysql")

    def test_valid_database_types(self):
        pg = GuardrailConfig(database_type="postgresql")
        mongo = GuardrailConfig(database_type="mongodb")
        assert pg.database_type == "postgresql"
        assert mongo.database_type == "mongodb"

    def test_valid_severity_overrides(self):
        config = GuardrailConfig(
            severity_overrides={
                "MISSING_PK": "High",
                "SERIAL_USAGE": "Medium",
                "DROP_TABLE": "Critical",
            }
        )
        assert config.severity_overrides["MISSING_PK"] == "High"

    def test_invalid_severity_override_value(self):
        with pytest.raises(ValueError, match="Invalid severity override"):
            GuardrailConfig(severity_overrides={"RULE1": "Low"})

    def test_valid_disabled_rules(self):
        config = GuardrailConfig(disabled_rules=["SERIAL_USAGE", "VARCHAR_LENGTH"])
        assert len(config.disabled_rules) == 2

    def test_empty_string_in_disabled_rules(self):
        with pytest.raises(ValueError, match="non-empty string"):
            GuardrailConfig(disabled_rules=["RULE1", ""])


class TestConfigLoader:
    """Tests for ConfigLoader class."""

    def test_load_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        loader = ConfigLoader()
        config = loader.load()
        assert config.database_type == "postgresql"
        assert config.thresholds.large_table_row_count == 100_000

    def test_load_explicit_nonexistent_path(self):
        loader = ConfigLoader()
        config = loader.load("/nonexistent/path/.guardrails.yml")
        assert config.database_type == "postgresql"

    def test_load_empty_yaml(self, tmp_path):
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text("")
        loader = ConfigLoader()
        config = loader.load(str(config_file))
        assert config.database_type == "postgresql"

    def test_load_full_config(self, tmp_path):
        yaml_content = """\
database_type: postgresql
severity_overrides:
  MISSING_PK: High
disabled_rules:
  - SERIAL_USAGE
  - VARCHAR_LENGTH
thresholds:
  large_table_row_count: 500000
  offset_limit: 5000
  index_size_limit_mb: 2048
  varchar_max_length: 8000
  composite_pk_max_columns: 4
index_naming_pattern: "^idx_[a-z]+_[a-z_]+$"
rollback_directory: "migrations/rollback"
rollback_naming_convention: "R__*.sql"
aurora_connection:
  host: aurora-cluster.example.com
  port: 5432
  database: mydb
  username: scanner
  password_env_var: AURORA_PG_PASSWORD
  ssl_mode: require
"""
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text(yaml_content)

        loader = ConfigLoader()
        config = loader.load(str(config_file))

        assert config.database_type == "postgresql"
        assert config.severity_overrides == {"MISSING_PK": "High"}
        assert config.disabled_rules == ["SERIAL_USAGE", "VARCHAR_LENGTH"]
        assert config.thresholds.large_table_row_count == 500_000
        assert config.thresholds.offset_limit == 5000
        assert config.thresholds.index_size_limit_mb == 2048
        assert config.thresholds.varchar_max_length == 8000
        assert config.thresholds.composite_pk_max_columns == 4
        assert config.index_naming_pattern == "^idx_[a-z]+_[a-z_]+$"
        assert config.rollback_directory == "migrations/rollback"
        assert config.rollback_naming_convention == "R__*.sql"
        assert config.aurora_connection is not None
        assert config.aurora_connection.host == "aurora-cluster.example.com"
        assert config.aurora_connection.port == 5432
        assert config.aurora_connection.database == "mydb"
        assert config.aurora_connection.username == "scanner"
        assert config.aurora_connection.ssl_mode == "require"

    def test_load_partial_config_thresholds_only(self, tmp_path):
        yaml_content = """\
thresholds:
  large_table_row_count: 200000
"""
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text(yaml_content)

        loader = ConfigLoader()
        config = loader.load(str(config_file))

        assert config.database_type == "postgresql"
        assert config.thresholds.large_table_row_count == 200_000
        assert config.thresholds.offset_limit == 10_000  # default
        assert config.aurora_connection is None

    def test_load_from_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        yaml_content = "database_type: mongodb\n"
        (tmp_path / ".guardrails.yml").write_text(yaml_content)

        loader = ConfigLoader()
        config = loader.load()
        assert config.database_type == "mongodb"

    def test_invalid_severity_in_yaml(self, tmp_path):
        yaml_content = """\
severity_overrides:
  RULE1: InvalidLevel
"""
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text(yaml_content)

        loader = ConfigLoader()
        with pytest.raises(ValueError, match="Invalid severity override"):
            loader.load(str(config_file))

    def test_invalid_thresholds_type(self, tmp_path):
        yaml_content = "thresholds: not_a_dict\n"
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text(yaml_content)

        loader = ConfigLoader()
        with pytest.raises(ValueError, match="thresholds must be a mapping"):
            loader.load(str(config_file))

    def test_aurora_without_host(self, tmp_path):
        yaml_content = """\
aurora_connection:
  port: 5432
"""
        config_file = tmp_path / ".guardrails.yml"
        config_file.write_text(yaml_content)

        loader = ConfigLoader()
        with pytest.raises(ValueError, match="host is required"):
            loader.load(str(config_file))
