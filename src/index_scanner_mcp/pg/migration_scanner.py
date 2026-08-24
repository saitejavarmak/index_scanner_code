"""Migration scanner for PostgreSQL guardrails.

Scans SQL migration files for risky DDL operations, destructive statements,
blocked maintenance operations, missing rollback scripts, and non-concurrent
index creation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import (
    Action,
    Severity,
    SQLStatement,
    Violation,
    ViolationCategory,
)
from index_scanner_mcp.pg.sql_parser import SQLParser


class MigrationScanner:
    """Scan SQL migration files for risky or blocked operations.

    Detects destructive DDL (DROP TABLE/COLUMN/DATABASE, TRUNCATE),
    blocked maintenance operations (VACUUM FULL, CLUSTER, REINDEX SYSTEM),
    dangerous schema modifications (ALTER TYPE, ALTER TABLE SET DATA TYPE),
    non-concurrent index creation, missing rollback scripts, and multiple
    DDL statements in a single migration.
    """

    # DDL statement types that count toward multiple-DDL detection
    _DDL_TYPES = {
        "CREATE TABLE",
        "CREATE INDEX",
        "ALTER TABLE",
        "ALTER TABLE RENAME",
        "ALTER COLUMN TYPE",
        "ALTER TYPE",
        "DROP TABLE",
        "DROP COLUMN",
        "DROP DATABASE",
        "ADD COLUMN",
        "TRUNCATE",
    }

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config
        self._parser = SQLParser()

    def scan_file(self, filepath: str) -> list[Violation]:
        """Scan a single SQL migration file for violations.

        Args:
            filepath: Path to a SQL migration file.

        Returns:
            List of detected violations.
        """
        statements = self._parser.parse_file(filepath)
        violations: list[Violation] = []

        violations.extend(self._check_destructive_operations(statements, filepath))
        violations.extend(self._check_blocked_operations(statements, filepath))
        violations.extend(self._check_non_concurrent_index(statements, filepath))
        violations.extend(self._check_multiple_ddl(statements, filepath))
        violations.extend(self._check_rollback_exists(filepath))

        return violations

    def scan_directory(self, directory: str) -> list[Violation]:
        """Scan all SQL migration files in a directory for violations.

        Args:
            directory: Path to a directory containing SQL migration files.

        Returns:
            List of detected violations across all files.
        """
        violations: list[Violation] = []
        dir_path = Path(directory)

        if not dir_path.is_dir():
            return violations

        for sql_file in sorted(dir_path.rglob("*.sql")):
            if sql_file.is_file():
                violations.extend(self.scan_file(str(sql_file)))

        return violations

    # ─── Internal: Destructive Operations ────────────────────────────────

    def _check_destructive_operations(
        self, statements: list[SQLStatement], filepath: str
    ) -> list[Violation]:
        """Detect DROP TABLE, DROP COLUMN, DROP DATABASE, and TRUNCATE."""
        violations: list[Violation] = []

        for stmt in statements:
            # DROP TABLE
            if stmt.statement_type == "DROP TABLE":
                violation = self._make_violation(
                    rule_id="PG-MIG-001",
                    category=ViolationCategory.MIGRATION,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        f"DROP TABLE detected"
                        f"{' on ' + stmt.table_name if stmt.table_name else ''}. "
                        "This is a destructive operation that permanently removes data."
                    ),
                    remediation=(
                        "Consider renaming the table with a deprecation suffix instead "
                        "of dropping it. If removal is required, ensure a rollback "
                        "script and data backup exist."
                    ),
                )
                if violation:
                    violations.append(violation)

            # DROP COLUMN
            elif stmt.statement_type == "DROP COLUMN":
                violation = self._make_violation(
                    rule_id="PG-MIG-002",
                    category=ViolationCategory.MIGRATION,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        f"DROP COLUMN detected"
                        f"{' on table ' + stmt.table_name if stmt.table_name else ''}. "
                        "This permanently removes column data."
                    ),
                    remediation=(
                        "Consider marking the column as deprecated or nullable first. "
                        "If removal is required, ensure a rollback script and data "
                        "backup exist."
                    ),
                )
                if violation:
                    violations.append(violation)

            # DROP DATABASE
            elif stmt.statement_type == "DROP DATABASE":
                violation = self._make_violation(
                    rule_id="PG-MIG-003",
                    category=ViolationCategory.MIGRATION,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "DROP DATABASE detected. This is an extremely destructive "
                        "operation that removes an entire database."
                    ),
                    remediation=(
                        "DROP DATABASE should never appear in migration scripts. "
                        "Remove this statement and manage database lifecycle through "
                        "infrastructure tooling."
                    ),
                )
                if violation:
                    violations.append(violation)

            # TRUNCATE
            elif stmt.statement_type == "TRUNCATE":
                violation = self._make_violation(
                    rule_id="PG-MIG-004",
                    category=ViolationCategory.MIGRATION,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        f"TRUNCATE detected"
                        f"{' on table ' + stmt.table_name if stmt.table_name else ''}. "
                        "This removes all rows without logging individual deletions."
                    ),
                    remediation=(
                        "Use DELETE with a WHERE clause for targeted data removal, "
                        "or ensure TRUNCATE is intentional with a rollback script."
                    ),
                )
                if violation:
                    violations.append(violation)

        return violations

    # ─── Internal: Blocked Operations ────────────────────────────────────

    def _check_blocked_operations(
        self, statements: list[SQLStatement], filepath: str
    ) -> list[Violation]:
        """Detect VACUUM FULL, CLUSTER, REINDEX SYSTEM, ALTER TYPE, ALTER TABLE SET DATA TYPE, REINDEX, ANALYZE."""
        violations: list[Violation] = []

        for stmt in statements:
            raw_upper = stmt.raw_sql.upper()

            # VACUUM FULL
            if stmt.statement_type == "VACUUM" and "FULL" in raw_upper:
                violation = self._make_violation(
                    rule_id="PG-BLK-001",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "VACUUM FULL detected. This acquires an ACCESS EXCLUSIVE "
                        "lock and rewrites the entire table, causing extended downtime."
                    ),
                    remediation=(
                        "Use regular VACUUM or pg_repack for online table "
                        "compaction without locking."
                    ),
                )
                if violation:
                    violations.append(violation)

            # CLUSTER
            elif stmt.statement_type == "CLUSTER":
                violation = self._make_violation(
                    rule_id="PG-BLK-002",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "CLUSTER detected. This acquires an ACCESS EXCLUSIVE lock "
                        "and physically reorders the table, causing extended downtime."
                    ),
                    remediation=(
                        "Avoid CLUSTER in migrations. Use pg_repack for online "
                        "table reordering if needed."
                    ),
                )
                if violation:
                    violations.append(violation)

            # REINDEX SYSTEM (Critical) vs regular REINDEX (High)
            elif stmt.statement_type == "REINDEX":
                if "SYSTEM" in raw_upper:
                    violation = self._make_violation(
                        rule_id="PG-BLK-003",
                        category=ViolationCategory.BLOCKED_OPERATIONS,
                        default_severity=Severity.CRITICAL,
                        default_action=Action.BLOCK_PR,
                        file_path=filepath,
                        line_number=stmt.line_number,
                        description=(
                            "REINDEX SYSTEM detected. This rebuilds all system "
                            "catalog indexes and requires exclusive access."
                        ),
                        remediation=(
                            "REINDEX SYSTEM should only be run during maintenance "
                            "windows by DBAs. Remove from migration scripts."
                        ),
                    )
                else:
                    violation = self._make_violation(
                        rule_id="PG-BLK-006",
                        category=ViolationCategory.BLOCKED_OPERATIONS,
                        default_severity=Severity.HIGH,
                        default_action=Action.WARN,
                        file_path=filepath,
                        line_number=stmt.line_number,
                        description=(
                            "REINDEX detected. This locks the table/index during "
                            "rebuild and can impact availability."
                        ),
                        remediation=(
                            "Use REINDEX CONCURRENTLY (PostgreSQL 12+) to avoid "
                            "locking, or schedule during maintenance windows."
                        ),
                    )
                if violation:
                    violations.append(violation)

            # ALTER TYPE (enum modification)
            elif stmt.statement_type == "ALTER TYPE":
                violation = self._make_violation(
                    rule_id="PG-BLK-004",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "ALTER TYPE (enum modification) detected. Modifying enum "
                        "types can cause issues with running transactions and "
                        "cached query plans."
                    ),
                    remediation=(
                        "For adding enum values, use ALTER TYPE ... ADD VALUE in a "
                        "separate transaction. For removing or renaming values, "
                        "create a new type and migrate."
                    ),
                )
                if violation:
                    violations.append(violation)

            # ALTER TABLE SET DATA TYPE (table rewrite)
            elif stmt.statement_type == "ALTER COLUMN TYPE":
                violation = self._make_violation(
                    rule_id="PG-BLK-005",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.CRITICAL,
                    default_action=Action.BLOCK_PR,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        f"ALTER TABLE SET DATA TYPE detected"
                        f"{' on table ' + stmt.table_name if stmt.table_name else ''}. "
                        "Changing a column type may require a full table rewrite "
                        "with an ACCESS EXCLUSIVE lock."
                    ),
                    remediation=(
                        "Add a new column with the desired type, backfill data, "
                        "then swap columns. This avoids the exclusive lock on "
                        "large tables."
                    ),
                )
                if violation:
                    violations.append(violation)

            # ANALYZE (Medium, Warn)
            elif stmt.statement_type == "ANALYZE":
                violation = self._make_violation(
                    rule_id="PG-BLK-007",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.MEDIUM,
                    default_action=Action.WARN,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "ANALYZE detected in migration. While not destructive, "
                        "ANALYZE can be resource-intensive on large tables and is "
                        "typically handled by autovacuum."
                    ),
                    remediation=(
                        "Let autovacuum handle statistics collection. If manual "
                        "ANALYZE is needed, run it during maintenance windows."
                    ),
                )
                if violation:
                    violations.append(violation)

        return violations

    # ─── Internal: Rollback Existence ────────────────────────────────────

    def _check_rollback_exists(self, filepath: str) -> list[Violation]:
        """Check that a corresponding rollback script exists for the migration."""
        rule_id = "PG-MIG-005"

        if self._is_rule_disabled(rule_id):
            return []

        migration_path = Path(filepath)
        migration_name = migration_path.stem  # e.g., "V001__create_users"

        # Determine rollback directory
        rollback_dir: Path
        if self._config.rollback_directory:
            rollback_dir = Path(self._config.rollback_directory)
            if not rollback_dir.is_absolute():
                # Relative to the migration file's directory
                rollback_dir = migration_path.parent / rollback_dir
        else:
            rollback_dir = migration_path.parent

        # Determine naming convention
        naming_convention = self._config.rollback_naming_convention

        # Search for rollback file
        if self._find_rollback_file(migration_name, rollback_dir, naming_convention):
            return []

        # Also check the same directory if rollback_dir is different
        if rollback_dir != migration_path.parent:
            if self._find_rollback_file(
                migration_name, migration_path.parent, naming_convention
            ):
                return []

        severity = self._get_effective_severity(rule_id, Severity.CRITICAL)
        action = Action.BLOCK_PR if severity == Severity.CRITICAL else Action.WARN

        return [
            Violation(
                rule_id=rule_id,
                category=ViolationCategory.MIGRATION,
                severity=severity,
                action=action,
                file_path=filepath,
                line_number=0,
                description=(
                    f"No rollback script found for migration '{migration_path.name}'. "
                    "Every migration must have a corresponding rollback script."
                ),
                remediation=(
                    f"Create a rollback script (e.g., R__{migration_name}.sql or "
                    f"undo_{migration_name}.sql) in "
                    f"'{rollback_dir}' that reverses the changes in this migration."
                ),
            )
        ]

    def _find_rollback_file(
        self,
        migration_name: str,
        directory: Path,
        naming_convention: str | None,
    ) -> bool:
        """Check if a rollback file matching the migration exists in directory."""
        if not directory.is_dir():
            return False

        if naming_convention:
            # Use the naming convention pattern to build expected filename
            # Convention placeholders: {migration_name}
            expected_name = naming_convention.replace(
                "{migration_name}", migration_name
            )
            rollback_path = directory / expected_name
            return rollback_path.exists()

        # Default: check common rollback naming patterns
        common_patterns = [
            f"R__{migration_name}.sql",
            f"undo_{migration_name}.sql",
            f"rollback_{migration_name}.sql",
            f"{migration_name}_rollback.sql",
            f"{migration_name}.rollback.sql",
        ]

        for pattern in common_patterns:
            if (directory / pattern).exists():
                return True

        return False

    # ─── Internal: Multiple DDL ──────────────────────────────────────────

    def _check_multiple_ddl(
        self, statements: list[SQLStatement], filepath: str
    ) -> list[Violation]:
        """Detect multiple DDL statements in a single migration file."""
        rule_id = "PG-MIG-006"

        if self._is_rule_disabled(rule_id):
            return []

        ddl_statements = [
            stmt for stmt in statements if stmt.statement_type in self._DDL_TYPES
        ]

        if len(ddl_statements) <= 1:
            return []

        severity = self._get_effective_severity(rule_id, Severity.MEDIUM)
        action = Action.BLOCK_PR if severity == Severity.CRITICAL else Action.WARN

        return [
            Violation(
                rule_id=rule_id,
                category=ViolationCategory.MIGRATION,
                severity=severity,
                action=action,
                file_path=filepath,
                line_number=ddl_statements[0].line_number,
                description=(
                    f"Migration file contains {len(ddl_statements)} DDL statements. "
                    "Each migration should contain a single logical change for "
                    "easier rollback and review."
                ),
                remediation=(
                    "Split this migration into separate files, one DDL statement "
                    "per migration, for atomic changes and simpler rollbacks."
                ),
            )
        ]

    # ─── Internal: Non-Concurrent Index ──────────────────────────────────

    def _check_non_concurrent_index(
        self, statements: list[SQLStatement], filepath: str
    ) -> list[Violation]:
        """Detect CREATE INDEX without CONCURRENTLY keyword."""
        violations: list[Violation] = []

        for stmt in statements:
            if stmt.statement_type != "CREATE INDEX":
                continue

            raw_upper = stmt.raw_sql.upper()
            if "CONCURRENTLY" not in raw_upper:
                violation = self._make_violation(
                    rule_id="PG-MIG-007",
                    category=ViolationCategory.BLOCKED_OPERATIONS,
                    default_severity=Severity.HIGH,
                    default_action=Action.WARN,
                    file_path=filepath,
                    line_number=stmt.line_number,
                    description=(
                        "CREATE INDEX without CONCURRENTLY detected. This acquires "
                        "a SHARE lock on the table, blocking writes until the index "
                        "is built."
                    ),
                    remediation=(
                        "Use CREATE INDEX CONCURRENTLY to build the index without "
                        "blocking concurrent writes. Note: CONCURRENTLY cannot run "
                        "inside a transaction block."
                    ),
                    auto_fix_sql=self._suggest_concurrent_index(stmt.raw_sql),
                )
                if violation:
                    violations.append(violation)

        return violations

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _make_violation(
        self,
        rule_id: str,
        category: ViolationCategory,
        default_severity: Severity,
        default_action: Action,
        file_path: str,
        line_number: int,
        description: str,
        remediation: str,
        auto_fix_sql: str | None = None,
    ) -> Violation | None:
        """Create a Violation respecting disabled rules and severity overrides."""
        if self._is_rule_disabled(rule_id):
            return None

        severity = self._get_effective_severity(rule_id, default_severity)
        # Adjust action based on effective severity
        if severity == Severity.CRITICAL:
            action = Action.BLOCK_PR
        elif default_action == Action.BLOCK_PR and severity != Severity.CRITICAL:
            action = Action.WARN
        else:
            action = default_action

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

    def _is_rule_disabled(self, rule_id: str) -> bool:
        """Check if a rule is disabled in the configuration."""
        return rule_id in self._config.disabled_rules

    def _get_effective_severity(
        self, rule_id: str, default: Severity
    ) -> Severity:
        """Get the effective severity for a rule, considering overrides."""
        override = self._config.severity_overrides.get(rule_id)
        if override:
            try:
                return Severity(override)
            except ValueError:
                return default
        return default

    def _suggest_concurrent_index(self, raw_sql: str) -> str:
        """Generate auto-fix SQL adding CONCURRENTLY to CREATE INDEX."""
        # Insert CONCURRENTLY after INDEX keyword
        fixed = re.sub(
            r"(CREATE\s+(?:UNIQUE\s+)?INDEX)\s+",
            r"\1 CONCURRENTLY ",
            raw_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        return fixed
