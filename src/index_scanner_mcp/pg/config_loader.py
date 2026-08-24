"""Configuration loader for the PostgreSQL Guardrails system.

Reads .guardrails.yml from the project root and provides GuardrailConfig
with sensible defaults when no configuration file exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Valid severity values for override validation
_VALID_SEVERITIES = {"Critical", "High", "Medium"}


@dataclass
class ThresholdConfig:
    """Configurable thresholds for guardrail rule evaluation."""

    large_table_row_count: int = 100_000
    offset_limit: int = 10_000
    index_size_limit_mb: int = 1024
    varchar_max_length: int = 4000
    composite_pk_max_columns: int = 3

    def __post_init__(self) -> None:
        if self.large_table_row_count < 0:
            raise ValueError(
                "ThresholdConfig.large_table_row_count must be non-negative"
            )
        if self.offset_limit < 0:
            raise ValueError("ThresholdConfig.offset_limit must be non-negative")
        if self.index_size_limit_mb < 0:
            raise ValueError(
                "ThresholdConfig.index_size_limit_mb must be non-negative"
            )
        if self.varchar_max_length < 0:
            raise ValueError(
                "ThresholdConfig.varchar_max_length must be non-negative"
            )
        if self.composite_pk_max_columns < 1:
            raise ValueError(
                "ThresholdConfig.composite_pk_max_columns must be at least 1"
            )


@dataclass
class AuroraConnectionConfig:
    """Connection configuration for AWS Aurora PostgreSQL runtime validation."""

    host: str
    port: int = 5432
    database: str = ""
    username: str = ""
    password_env_var: str = "AURORA_PG_PASSWORD"
    ssl_mode: str = "require"

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("AuroraConnectionConfig.host must be non-empty")
        if self.port < 1 or self.port > 65535:
            raise ValueError(
                "AuroraConnectionConfig.port must be between 1 and 65535"
            )
        valid_ssl_modes = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
        if self.ssl_mode not in valid_ssl_modes:
            raise ValueError(
                f"AuroraConnectionConfig.ssl_mode must be one of {valid_ssl_modes}, "
                f"got '{self.ssl_mode}'"
            )

    def get_password(self) -> str | None:
        """Read the password from the environment variable at runtime."""
        return os.environ.get(self.password_env_var)


@dataclass
class GuardrailConfig:
    """Top-level configuration for the PostgreSQL Guardrails system."""

    database_type: str = "postgresql"
    severity_overrides: dict[str, str] = field(default_factory=dict)
    disabled_rules: list[str] = field(default_factory=list)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    index_naming_pattern: str | None = None
    rollback_directory: str | None = None
    rollback_naming_convention: str | None = None
    aurora_connection: AuroraConnectionConfig | None = None

    def __post_init__(self) -> None:
        valid_db_types = {"postgresql", "mongodb"}
        if self.database_type not in valid_db_types:
            raise ValueError(
                f"GuardrailConfig.database_type must be one of {valid_db_types}, "
                f"got '{self.database_type}'"
            )
        # Validate severity overrides
        for rule_id, severity in self.severity_overrides.items():
            if severity not in _VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity override for rule '{rule_id}': "
                    f"'{severity}' is not one of {_VALID_SEVERITIES}"
                )
        # Validate disabled_rules is a list of strings
        for rule in self.disabled_rules:
            if not isinstance(rule, str) or not rule:
                raise ValueError(
                    "Each entry in disabled_rules must be a non-empty string"
                )


class ConfigLoader:
    """Loads guardrail configuration from a YAML file or applies defaults."""

    DEFAULT_CONFIG_FILENAME = ".guardrails.yml"

    def load(self, config_path: str | None = None) -> GuardrailConfig:
        """Load configuration from a YAML file.

        Args:
            config_path: Explicit path to a configuration file. If None,
                looks for .guardrails.yml in the current working directory.

        Returns:
            A validated GuardrailConfig instance.
        """
        if config_path is None:
            # Look in the current working directory
            candidate = Path.cwd() / self.DEFAULT_CONFIG_FILENAME
            if not candidate.exists():
                return self._apply_defaults()
            config_path = str(candidate)
        else:
            path = Path(config_path)
            if not path.exists():
                return self._apply_defaults()

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            # Empty YAML file
            return self._apply_defaults()

        return self._parse_config(raw)

    def _apply_defaults(self) -> GuardrailConfig:
        """Return a GuardrailConfig with all default values."""
        return GuardrailConfig()

    def _parse_config(self, raw: dict) -> GuardrailConfig:
        """Parse a raw YAML dictionary into a validated GuardrailConfig.

        Args:
            raw: Dictionary parsed from YAML.

        Returns:
            A validated GuardrailConfig.

        Raises:
            ValueError: If the configuration contains invalid values.
        """
        # Parse thresholds
        thresholds = self._parse_thresholds(raw.get("thresholds"))

        # Parse aurora_connection
        aurora_connection = self._parse_aurora_connection(
            raw.get("aurora_connection")
        )

        # Parse severity_overrides
        severity_overrides = raw.get("severity_overrides", {})
        if not isinstance(severity_overrides, dict):
            raise ValueError("severity_overrides must be a mapping")

        # Parse disabled_rules
        disabled_rules = raw.get("disabled_rules", [])
        if not isinstance(disabled_rules, list):
            raise ValueError("disabled_rules must be a list")

        return GuardrailConfig(
            database_type=raw.get("database_type", "postgresql"),
            severity_overrides=severity_overrides,
            disabled_rules=disabled_rules,
            thresholds=thresholds,
            index_naming_pattern=raw.get("index_naming_pattern"),
            rollback_directory=raw.get("rollback_directory"),
            rollback_naming_convention=raw.get("rollback_naming_convention"),
            aurora_connection=aurora_connection,
        )

    def _parse_thresholds(self, raw: dict | None) -> ThresholdConfig:
        """Parse the thresholds section from config."""
        if raw is None:
            return ThresholdConfig()

        if not isinstance(raw, dict):
            raise ValueError("thresholds must be a mapping")

        return ThresholdConfig(
            large_table_row_count=raw.get("large_table_row_count", 100_000),
            offset_limit=raw.get("offset_limit", 10_000),
            index_size_limit_mb=raw.get("index_size_limit_mb", 1024),
            varchar_max_length=raw.get("varchar_max_length", 4000),
            composite_pk_max_columns=raw.get("composite_pk_max_columns", 3),
        )

    def _parse_aurora_connection(
        self, raw: dict | None
    ) -> AuroraConnectionConfig | None:
        """Parse the aurora_connection section from config."""
        if raw is None:
            return None

        if not isinstance(raw, dict):
            raise ValueError("aurora_connection must be a mapping")

        host = raw.get("host", "")
        if not host:
            raise ValueError("aurora_connection.host is required")

        return AuroraConnectionConfig(
            host=host,
            port=raw.get("port", 5432),
            database=raw.get("database", ""),
            username=raw.get("username", ""),
            password_env_var=raw.get("password_env_var", "AURORA_PG_PASSWORD"),
            ssl_mode=raw.get("ssl_mode", "require"),
        )
