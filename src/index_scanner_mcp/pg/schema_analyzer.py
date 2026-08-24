"""Schema analyzer for PostgreSQL table definitions.

Detects schema design anti-patterns and constraint gaps by inspecting
CREATE TABLE statements parsed from SQL migration files.
"""

from __future__ import annotations

import re

from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import (
    Action,
    Severity,
    TableDefinition,
    Violation,
    ViolationCategory,
)
from index_scanner_mcp.pg.sql_parser import SQLParser


# Business-critical column names that should never be nullable
_BUSINESS_CRITICAL_COLUMNS = frozenset(
    {"status", "type", "category", "state", "role", "kind", "priority", "level"}
)


class SchemaAnalyzer:
    """Analyze PostgreSQL schema definitions for design anti-patterns."""

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config
        self._parser = SQLParser()

    def analyze_file(self, filepath: str) -> list[Violation]:
        """Read a SQL file and return all schema violations found.

        Args:
            filepath: Path to a SQL file containing CREATE TABLE statements.

        Returns:
            A list of Violation objects for detected schema issues.
        """
        statements = self._parser.parse_file(filepath)
        tables = self._parser.extract_tables(statements)

        violations: list[Violation] = []
        for table in tables:
            violations.extend(self._check_no_primary_key(table))
            violations.extend(self._check_composite_pk_width(table))
            violations.extend(self._check_missing_foreign_keys(table, tables))
            violations.extend(self._check_data_types(table))
            violations.extend(self._check_nullable_business_columns(table))
            violations.extend(self._check_not_null_without_default(table))

        # Circular FK check needs all tables in the file
        violations.extend(self._check_circular_references(tables))

        # Filter out disabled rules and apply severity overrides
        violations = self._apply_config(violations)
        return violations

    # ─── Internal Check Methods ──────────────────────────────────────────

    def _check_no_primary_key(self, table: TableDefinition) -> list[Violation]:
        """Detect tables without a PRIMARY KEY (Requirement 3.1)."""
        if table.primary_key:
            return []

        return [
            Violation(
                rule_id="schema.no_primary_key",
                category=ViolationCategory.SCHEMA,
                severity=Severity.CRITICAL,
                action=Action.BLOCK_PR,
                file_path=table.file_path,
                line_number=table.line_number,
                description=(
                    f"Table '{table.name}' has no PRIMARY KEY defined. "
                    "Every table should have a primary key for data integrity "
                    "and efficient row identification."
                ),
                remediation=(
                    f"Add a PRIMARY KEY constraint to table '{table.name}'. "
                    "Example: ALTER TABLE {name} ADD PRIMARY KEY (id);".format(
                        name=table.name
                    )
                ),
            )
        ]

    def _check_composite_pk_width(self, table: TableDefinition) -> list[Violation]:
        """Detect composite PKs exceeding configured max columns (Requirement 3.2)."""
        max_cols = self._config.thresholds.composite_pk_max_columns
        if len(table.primary_key) <= max_cols:
            return []

        return [
            Violation(
                rule_id="schema.composite_pk_too_wide",
                category=ViolationCategory.SCHEMA,
                severity=Severity.HIGH,
                action=Action.WARN,
                file_path=table.file_path,
                line_number=table.line_number,
                description=(
                    f"Table '{table.name}' has a composite PRIMARY KEY with "
                    f"{len(table.primary_key)} columns ({', '.join(table.primary_key)}), "
                    f"exceeding the configured maximum of {max_cols}."
                ),
                remediation=(
                    "Consider using a surrogate key (e.g., BIGINT GENERATED ALWAYS AS IDENTITY) "
                    "and adding a UNIQUE constraint on the business columns instead."
                ),
            )
        ]

    def _check_missing_foreign_keys(
        self, table: TableDefinition, all_tables: list[TableDefinition]
    ) -> list[Violation]:
        """Detect columns that likely reference other tables without FK constraints (Requirement 3.3).

        Heuristic: columns ending in '_id' that don't have a corresponding
        foreign key constraint referencing another table in the same file.
        """
        violations: list[Violation] = []

        # Build set of columns already covered by foreign keys
        fk_source_cols: set[str] = set()
        for fk in table.foreign_keys:
            for col in fk.source_columns:
                fk_source_cols.add(col.lower())

        # Build set of known table names for reference checking
        known_tables: set[str] = {t.name.lower() for t in all_tables}

        for col in table.columns:
            col_lower = col.name.lower()
            # Skip if already covered by a foreign key
            if col_lower in fk_source_cols:
                continue
            # Skip primary key columns
            if col.is_primary_key:
                continue
            # Heuristic: column ends with '_id' and the prefix matches a known table
            if col_lower.endswith("_id"):
                prefix = col_lower[:-3]  # strip '_id'
                # Check if a table with that name exists
                if prefix in known_tables and prefix != table.name.lower():
                    violations.append(
                        Violation(
                            rule_id="schema.missing_foreign_key",
                            category=ViolationCategory.SCHEMA,
                            severity=Severity.HIGH,
                            action=Action.WARN,
                            file_path=table.file_path,
                            line_number=table.line_number,
                            description=(
                                f"Column '{col.name}' in table '{table.name}' "
                                f"appears to reference table '{prefix}' but has no "
                                "FOREIGN KEY constraint defined."
                            ),
                            remediation=(
                                f"Add a FOREIGN KEY constraint: ALTER TABLE {table.name} "
                                f"ADD CONSTRAINT fk_{table.name}_{prefix} "
                                f"FOREIGN KEY ({col.name}) REFERENCES {prefix}(id);"
                            ),
                        )
                    )

        return violations

    def _check_circular_references(
        self, tables: list[TableDefinition]
    ) -> list[Violation]:
        """Detect circular FK references between tables (Requirement 3.4)."""
        # Build adjacency list from FK relationships
        graph: dict[str, set[str]] = {}
        table_lookup: dict[str, TableDefinition] = {}

        for table in tables:
            table_lower = table.name.lower()
            table_lookup[table_lower] = table
            if table_lower not in graph:
                graph[table_lower] = set()
            for fk in table.foreign_keys:
                target_lower = fk.target_table.lower()
                graph[table_lower].add(target_lower)

        # Detect cycles using DFS
        violations: list[Violation] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        reported_cycles: set[frozenset[str]] = set()

        def _dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    _dfs(neighbor, path)
                elif neighbor in rec_stack:
                    # Found a cycle - extract it
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:]
                    cycle_key = frozenset(cycle)
                    if cycle_key not in reported_cycles:
                        reported_cycles.add(cycle_key)
                        # Use the first table in the cycle for file/line
                        ref_table = table_lookup.get(cycle[0])
                        if ref_table:
                            violations.append(
                                Violation(
                                    rule_id="schema.circular_foreign_key",
                                    category=ViolationCategory.SCHEMA,
                                    severity=Severity.CRITICAL,
                                    action=Action.BLOCK_PR,
                                    file_path=ref_table.file_path,
                                    line_number=ref_table.line_number,
                                    description=(
                                        "Circular FOREIGN KEY reference detected between tables: "
                                        f"{' → '.join(cycle)} → {neighbor}. "
                                        "This can cause insertion/deletion ordering issues."
                                    ),
                                    remediation=(
                                        "Break the circular dependency by removing one FK constraint "
                                        "and enforcing the relationship at the application level, "
                                        "or use deferred constraints."
                                    ),
                                )
                            )

            path.pop()
            rec_stack.discard(node)

        for node in graph:
            if node not in visited:
                _dfs(node, [])

        return violations

    def _check_data_types(self, table: TableDefinition) -> list[Violation]:
        """Detect problematic data type choices (Requirements 3.9–3.14)."""
        violations: list[Violation] = []

        for col in table.columns:
            dtype_upper = col.data_type.upper()

            # 3.11: JSON instead of JSONB
            if dtype_upper == "JSON":
                violations.append(
                    Violation(
                        rule_id="schema.json_instead_of_jsonb",
                        category=ViolationCategory.SCHEMA,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=table.file_path,
                        line_number=table.line_number,
                        description=(
                            f"Column '{col.name}' in table '{table.name}' uses JSON "
                            "data type. JSONB is preferred for better indexing and "
                            "query performance."
                        ),
                        remediation=(
                            f"Change column type to JSONB: ALTER TABLE {table.name} "
                            f"ALTER COLUMN {col.name} SET DATA TYPE JSONB;"
                        ),
                    )
                )

            # 3.13: TIMESTAMP WITHOUT TIME ZONE
            if "TIMESTAMP" in dtype_upper and "WITHOUT TIME ZONE" in dtype_upper:
                violations.append(
                    Violation(
                        rule_id="schema.timestamp_without_timezone",
                        category=ViolationCategory.SCHEMA,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=table.file_path,
                        line_number=table.line_number,
                        description=(
                            f"Column '{col.name}' in table '{table.name}' uses "
                            "TIMESTAMP WITHOUT TIME ZONE. This can cause timezone-related "
                            "bugs in distributed systems."
                        ),
                        remediation=(
                            f"Use TIMESTAMPTZ instead: ALTER TABLE {table.name} "
                            f"ALTER COLUMN {col.name} SET DATA TYPE TIMESTAMPTZ;"
                        ),
                    )
                )

            # 3.14: SERIAL instead of IDENTITY
            if dtype_upper in ("SERIAL", "BIGSERIAL", "SMALLSERIAL"):
                violations.append(
                    Violation(
                        rule_id="schema.serial_instead_of_identity",
                        category=ViolationCategory.SCHEMA,
                        severity=Severity.MEDIUM,
                        action=Action.WARN,
                        file_path=table.file_path,
                        line_number=table.line_number,
                        description=(
                            f"Column '{col.name}' in table '{table.name}' uses "
                            f"{col.data_type} type. The modern GENERATED ALWAYS AS IDENTITY "
                            "syntax is preferred for better standards compliance."
                        ),
                        remediation=(
                            f"Replace {col.data_type} with: {col.name} "
                            "INTEGER GENERATED ALWAYS AS IDENTITY"
                        ),
                    )
                )

            # 3.10: NUMERIC without precision/scale
            if re.match(r"^NUMERIC$", dtype_upper) or re.match(
                r"^DECIMAL$", dtype_upper
            ):
                violations.append(
                    Violation(
                        rule_id="schema.numeric_without_precision",
                        category=ViolationCategory.SCHEMA,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=table.file_path,
                        line_number=table.line_number,
                        description=(
                            f"Column '{col.name}' in table '{table.name}' uses "
                            f"{col.data_type} without precision and scale. This allows "
                            "arbitrary precision which can lead to unexpected storage behavior."
                        ),
                        remediation=(
                            f"Specify precision and scale: {col.name} NUMERIC(10,2) "
                            "(adjust to your needs)."
                        ),
                    )
                )

            # 3.9: VARCHAR exceeding configured max length
            varchar_match = re.match(
                r"^(?:VARCHAR|CHARACTER\s+VARYING)\s*\(\s*(\d+)\s*\)$",
                dtype_upper,
            )
            if varchar_match:
                length = int(varchar_match.group(1))
                max_length = self._config.thresholds.varchar_max_length
                if length > max_length:
                    violations.append(
                        Violation(
                            rule_id="schema.varchar_exceeds_max_length",
                            category=ViolationCategory.SCHEMA,
                            severity=Severity.MEDIUM,
                            action=Action.WARN,
                            file_path=table.file_path,
                            line_number=table.line_number,
                            description=(
                                f"Column '{col.name}' in table '{table.name}' defines "
                                f"VARCHAR({length}) which exceeds the configured maximum "
                                f"of {max_length}. Consider using TEXT type instead."
                            ),
                            remediation=(
                                f"Use TEXT type with a CHECK constraint for length validation: "
                                f"ALTER TABLE {table.name} ALTER COLUMN {col.name} "
                                f"SET DATA TYPE TEXT;"
                            ),
                        )
                    )

        return violations

    def _check_nullable_business_columns(
        self, table: TableDefinition
    ) -> list[Violation]:
        """Detect business-critical columns that are nullable (Requirement 3.7)."""
        violations: list[Violation] = []

        for col in table.columns:
            if col.nullable and col.name.lower() in _BUSINESS_CRITICAL_COLUMNS:
                violations.append(
                    Violation(
                        rule_id="schema.nullable_business_column",
                        category=ViolationCategory.SCHEMA,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=table.file_path,
                        line_number=table.line_number,
                        description=(
                            f"Column '{col.name}' in table '{table.name}' is nullable "
                            "but appears to be a business-critical column. Nullable "
                            "business-critical columns can lead to ambiguous application state."
                        ),
                        remediation=(
                            f"Add NOT NULL constraint: ALTER TABLE {table.name} "
                            f"ALTER COLUMN {col.name} SET NOT NULL;"
                        ),
                    )
                )

        return violations

    def _check_not_null_without_default(
        self, table: TableDefinition
    ) -> list[Violation]:
        """Detect NOT NULL columns without a DEFAULT value (Requirement 3.6)."""
        violations: list[Violation] = []

        for col in table.columns:
            # Skip PK columns - they typically don't need defaults (auto-generated)
            if col.is_primary_key:
                continue
            # Skip if nullable (this rule only applies to NOT NULL columns)
            if col.nullable:
                continue
            # Skip if has default
            if col.has_default:
                continue
            # Skip identity/serial columns (auto-generated values)
            dtype_upper = col.data_type.upper()
            if dtype_upper in ("SERIAL", "BIGSERIAL", "SMALLSERIAL"):
                continue
            if "GENERATED" in dtype_upper:
                continue

            violations.append(
                Violation(
                    rule_id="schema.not_null_without_default",
                    category=ViolationCategory.SCHEMA,
                    severity=Severity.MEDIUM,
                    action=Action.WARN,
                    file_path=table.file_path,
                    line_number=table.line_number,
                    description=(
                        f"Column '{col.name}' in table '{table.name}' is NOT NULL "
                        "but has no DEFAULT value. This will cause INSERT failures "
                        "if the column is not explicitly provided."
                    ),
                    remediation=(
                        f"Add a DEFAULT value: ALTER TABLE {table.name} "
                        f"ALTER COLUMN {col.name} SET DEFAULT <value>;"
                    ),
                )
            )

        return violations

    # ─── Configuration Helpers ───────────────────────────────────────────

    def _apply_config(self, violations: list[Violation]) -> list[Violation]:
        """Filter disabled rules and apply severity overrides."""
        result: list[Violation] = []
        disabled = set(self._config.disabled_rules)

        for v in violations:
            if v.rule_id in disabled:
                continue
            # Apply severity override if configured
            if v.rule_id in self._config.severity_overrides:
                override = self._config.severity_overrides[v.rule_id]
                v = Violation(
                    rule_id=v.rule_id,
                    category=v.category,
                    severity=Severity(override),
                    action=v.action,
                    file_path=v.file_path,
                    line_number=v.line_number,
                    description=v.description,
                    remediation=v.remediation,
                    auto_fix_sql=v.auto_fix_sql,
                    explain_output=v.explain_output,
                )
            result.append(v)

        return result
