"""Index Analyzer for the PostgreSQL Guardrails system.

Evaluates index definitions in SQL files to detect missing indexes on foreign keys,
duplicate indexes, overlapping/prefix indexes, overly wide composite indexes,
wrong column ordering, and naming convention violations.
"""

from __future__ import annotations

import re

from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import (
    Action,
    ForeignKey,
    PgIndex,
    Severity,
    TableDefinition,
    Violation,
    ViolationCategory,
)
from index_scanner_mcp.pg.sql_parser import SQLParser


class IndexAnalyzer:
    """Analyze index definitions in SQL files for guardrail violations."""

    # Rule IDs for each check
    RULE_FK_WITHOUT_INDEX = "IDX001"
    RULE_DUPLICATE_INDEX = "IDX002"
    RULE_OVERLAPPING_INDEX = "IDX003"
    RULE_COMPOSITE_WIDTH = "IDX004"
    RULE_COLUMN_ORDER = "IDX005"
    RULE_NAMING_CONVENTION = "IDX006"

    # Default max columns for composite index width check
    _MAX_COMPOSITE_COLUMNS = 5

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config
        self._parser = SQLParser()

    def analyze_file(self, filepath: str) -> list[Violation]:
        """Analyze index definitions in a SQL file and return violations.

        Parses the SQL file, extracts table definitions and standalone indexes,
        then runs all index checks.

        Args:
            filepath: Path to a SQL file.

        Returns:
            A list of Violation objects for any index issues detected.
        """
        statements = self._parser.parse_file(filepath)
        tables = self._parser.extract_tables(statements)

        # Collect all indexes: from table definitions + standalone CREATE INDEX
        all_indexes: list[PgIndex] = []
        for table in tables:
            all_indexes.extend(table.indexes)

        for stmt in statements:
            if stmt.statement_type == "CREATE INDEX":
                for idx in stmt.indexes:
                    # Avoid duplicates if already captured in a table definition
                    if not any(
                        existing.name == idx.name for existing in all_indexes
                    ):
                        all_indexes.append(idx)

        # Collect all foreign keys from table definitions
        all_foreign_keys: list[tuple[ForeignKey, TableDefinition]] = []
        for table in tables:
            for fk in table.foreign_keys:
                all_foreign_keys.append((fk, table))

        # Run checks, respecting disabled rules
        violations: list[Violation] = []

        if self.RULE_FK_WITHOUT_INDEX not in self._config.disabled_rules:
            violations.extend(
                self._check_fk_without_index(
                    all_foreign_keys, all_indexes, filepath
                )
            )

        if self.RULE_DUPLICATE_INDEX not in self._config.disabled_rules:
            violations.extend(
                self._check_duplicate_indexes(all_indexes, filepath)
            )

        if self.RULE_OVERLAPPING_INDEX not in self._config.disabled_rules:
            violations.extend(
                self._check_overlapping_indexes(all_indexes, filepath)
            )

        if self.RULE_COMPOSITE_WIDTH not in self._config.disabled_rules:
            violations.extend(
                self._check_composite_width(all_indexes, filepath)
            )

        if self.RULE_COLUMN_ORDER not in self._config.disabled_rules:
            violations.extend(
                self._check_column_order(all_indexes, tables, filepath)
            )

        if self.RULE_NAMING_CONVENTION not in self._config.disabled_rules:
            violations.extend(
                self._check_naming_convention(all_indexes, filepath)
            )

        # Apply severity overrides
        for violation in violations:
            if violation.rule_id in self._config.severity_overrides:
                override = self._config.severity_overrides[violation.rule_id]
                violation.severity = Severity(override)

        return violations

    # ─── Internal Checks ─────────────────────────────────────────────────

    def _check_fk_without_index(
        self,
        foreign_keys: list[tuple[ForeignKey, TableDefinition]],
        all_indexes: list[PgIndex],
        filepath: str,
    ) -> list[Violation]:
        """Detect foreign key columns that have no corresponding index.

        A FK is considered covered if there exists an index on the same table
        whose leading columns match the FK source columns.
        """
        violations: list[Violation] = []

        for fk, table in foreign_keys:
            fk_cols_lower = [c.lower() for c in fk.source_columns]
            source_table_lower = fk.source_table.lower()

            # Check if any index covers the FK columns (as a prefix)
            covered = False
            for idx in all_indexes:
                if idx.table_name.lower() != source_table_lower:
                    continue
                idx_cols_lower = [c.lower() for c in idx.columns]
                # FK is covered if index columns start with the FK columns
                if idx_cols_lower[: len(fk_cols_lower)] == fk_cols_lower:
                    covered = True
                    break

            # Also check if the FK columns happen to be the primary key
            if not covered:
                pk_cols_lower = [c.lower() for c in table.primary_key]
                if pk_cols_lower[: len(fk_cols_lower)] == fk_cols_lower:
                    covered = True

            if not covered:
                fk_name = fk.constraint_name or f"FK({', '.join(fk.source_columns)})"
                violations.append(
                    Violation(
                        rule_id=self.RULE_FK_WITHOUT_INDEX,
                        category=ViolationCategory.INDEX,
                        severity=Severity.CRITICAL,
                        action=Action.BLOCK_PR,
                        file_path=filepath,
                        line_number=table.line_number,
                        description=(
                            f"Foreign key {fk_name} on table '{fk.source_table}' "
                            f"column(s) [{', '.join(fk.source_columns)}] has no "
                            f"corresponding index. This will cause slow lookups on "
                            f"DELETE/UPDATE of the referenced table."
                        ),
                        remediation=(
                            f"CREATE INDEX idx_{fk.source_table}_"
                            f"{'_'.join(fk.source_columns)} "
                            f"ON {fk.source_table} ({', '.join(fk.source_columns)});"
                        ),
                        auto_fix_sql=(
                            f"CREATE INDEX CONCURRENTLY idx_{fk.source_table}_"
                            f"{'_'.join(fk.source_columns)} "
                            f"ON {fk.source_table} ({', '.join(fk.source_columns)});"
                        ),
                    )
                )

        return violations

    def _check_duplicate_indexes(
        self, indexes: list[PgIndex], filepath: str
    ) -> list[Violation]:
        """Detect indexes with identical column lists on the same table.

        Two indexes are duplicates if they are on the same table and have the
        exact same columns in the exact same order.
        """
        violations: list[Violation] = []
        seen: dict[str, PgIndex] = {}

        for idx in indexes:
            # Build a key: table_name + column list (all lowercased)
            key = (
                idx.table_name.lower()
                + ":"
                + ",".join(c.lower() for c in idx.columns)
            )
            if key in seen:
                original = seen[key]
                violations.append(
                    Violation(
                        rule_id=self.RULE_DUPLICATE_INDEX,
                        category=ViolationCategory.INDEX,
                        severity=Severity.HIGH,
                        action=Action.BLOCK_PR,
                        file_path=filepath,
                        line_number=0,
                        description=(
                            f"Index '{idx.name}' on table '{idx.table_name}' "
                            f"is a duplicate of index '{original.name}'. "
                            f"Both have columns [{', '.join(idx.columns)}]."
                        ),
                        remediation=(
                            f"Remove the duplicate index '{idx.name}' to reduce "
                            f"storage overhead and write amplification."
                        ),
                        auto_fix_sql=f"DROP INDEX IF EXISTS {idx.name};",
                    )
                )
            else:
                seen[key] = idx

        return violations

    def _check_overlapping_indexes(
        self, indexes: list[PgIndex], filepath: str
    ) -> list[Violation]:
        """Detect indexes that are prefix subsets of other indexes on the same table.

        An index A overlaps index B if A's columns are a proper prefix of B's columns
        and both are on the same table.
        """
        violations: list[Violation] = []
        reported_pairs: set[tuple[str, str]] = set()

        for i, idx_a in enumerate(indexes):
            for j, idx_b in enumerate(indexes):
                if i == j:
                    continue
                if idx_a.table_name.lower() != idx_b.table_name.lower():
                    continue

                cols_a = [c.lower() for c in idx_a.columns]
                cols_b = [c.lower() for c in idx_b.columns]

                # A is a proper prefix of B
                if (
                    len(cols_a) < len(cols_b)
                    and cols_b[: len(cols_a)] == cols_a
                ):
                    pair_key = (idx_a.name.lower(), idx_b.name.lower())
                    if pair_key in reported_pairs:
                        continue
                    reported_pairs.add(pair_key)

                    violations.append(
                        Violation(
                            rule_id=self.RULE_OVERLAPPING_INDEX,
                            category=ViolationCategory.INDEX,
                            severity=Severity.MEDIUM,
                            action=Action.WARN,
                            file_path=filepath,
                            line_number=0,
                            description=(
                                f"Index '{idx_a.name}' on table '{idx_a.table_name}' "
                                f"with columns [{', '.join(idx_a.columns)}] is a prefix "
                                f"of index '{idx_b.name}' with columns "
                                f"[{', '.join(idx_b.columns)}]. The smaller index is "
                                f"redundant."
                            ),
                            remediation=(
                                f"Consider removing index '{idx_a.name}' since "
                                f"index '{idx_b.name}' already covers its columns "
                                f"as a prefix."
                            ),
                            auto_fix_sql=f"DROP INDEX IF EXISTS {idx_a.name};",
                        )
                    )

        return violations

    def _check_composite_width(
        self, indexes: list[PgIndex], filepath: str
    ) -> list[Violation]:
        """Detect composite indexes with more than 5 columns."""
        violations: list[Violation] = []

        for idx in indexes:
            if len(idx.columns) > self._MAX_COMPOSITE_COLUMNS:
                violations.append(
                    Violation(
                        rule_id=self.RULE_COMPOSITE_WIDTH,
                        category=ViolationCategory.INDEX,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=filepath,
                        line_number=0,
                        description=(
                            f"Index '{idx.name}' on table '{idx.table_name}' has "
                            f"{len(idx.columns)} columns [{', '.join(idx.columns)}], "
                            f"exceeding the recommended maximum of "
                            f"{self._MAX_COMPOSITE_COLUMNS}. Wide composite indexes "
                            f"have diminishing returns and increase write overhead."
                        ),
                        remediation=(
                            f"Review whether all {len(idx.columns)} columns are "
                            f"necessary. Consider splitting into multiple narrower "
                            f"indexes or using INCLUDE for non-filtering columns."
                        ),
                    )
                )

        return violations

    def _check_column_order(
        self,
        indexes: list[PgIndex],
        tables: list[TableDefinition],
        filepath: str,
    ) -> list[Violation]:
        """Detect wrong column order in composite indexes.

        A composite index has wrong order if equality filter columns are placed
        after range filter columns. The optimal order is:
        equality columns first, then range columns, then sort columns.

        This check uses a heuristic: if a column name suggests a range filter
        (e.g., contains 'date', 'time', 'created', 'updated', 'amount', 'price',
        'age', 'count') and appears before an equality-like column, it flags the
        index.

        For composite indexes with 2+ columns, we check if range-type columns
        appear before non-range columns.
        """
        violations: list[Violation] = []

        # Heuristic patterns for range columns
        range_patterns = re.compile(
            r"(date|time|created|updated|modified|timestamp|amount|price|"
            r"age|count|total|balance|score|rank|position|seq|sequence|"
            r"start|end|from|to|min|max)",
            re.IGNORECASE,
        )

        for idx in indexes:
            if len(idx.columns) < 2:
                continue

            # Determine which columns are likely range columns
            range_positions: list[int] = []
            equality_positions: list[int] = []

            for pos, col in enumerate(idx.columns):
                if range_patterns.search(col):
                    range_positions.append(pos)
                else:
                    equality_positions.append(pos)

            # If we have both range and equality columns, check order
            if range_positions and equality_positions:
                # Bad order: any range column appears before an equality column
                min_range = min(range_positions)
                max_equality = max(equality_positions)

                if min_range < max_equality:
                    # Find the first offending range column before an equality col
                    for rp in range_positions:
                        later_equalities = [
                            ep for ep in equality_positions if ep > rp
                        ]
                        if later_equalities:
                            violations.append(
                                Violation(
                                    rule_id=self.RULE_COLUMN_ORDER,
                                    category=ViolationCategory.INDEX,
                                    severity=Severity.HIGH,
                                    action=Action.WARN,
                                    file_path=filepath,
                                    line_number=0,
                                    description=(
                                        f"Index '{idx.name}' on table "
                                        f"'{idx.table_name}' has suboptimal column "
                                        f"order. Range filter column "
                                        f"'{idx.columns[rp]}' (position {rp}) "
                                        f"appears before equality filter column "
                                        f"'{idx.columns[later_equalities[0]]}' "
                                        f"(position {later_equalities[0]}). "
                                        f"Equality columns should come first for "
                                        f"optimal B-tree traversal."
                                    ),
                                    remediation=(
                                        f"Reorder index columns to place equality "
                                        f"filter columns before range filter columns. "
                                        f"Suggested order: "
                                        f"[{', '.join(sorted(idx.columns, key=lambda c: 1 if range_patterns.search(c) else 0))}]."
                                    ),
                                )
                            )
                            break  # One violation per index is enough

        return violations

    def _check_naming_convention(
        self, indexes: list[PgIndex], filepath: str
    ) -> list[Violation]:
        """Validate index names against the configured naming convention regex.

        Only runs if config.index_naming_pattern is set.
        """
        violations: list[Violation] = []
        pattern = self._config.index_naming_pattern

        if not pattern:
            return violations

        try:
            regex = re.compile(pattern)
        except re.error:
            # Invalid regex in config - skip check silently
            # (config validation should catch this upstream)
            return violations

        for idx in indexes:
            if not regex.match(idx.name):
                violations.append(
                    Violation(
                        rule_id=self.RULE_NAMING_CONVENTION,
                        category=ViolationCategory.INDEX,
                        severity=Severity.MEDIUM,
                        action=Action.WARN,
                        file_path=filepath,
                        line_number=0,
                        description=(
                            f"Index '{idx.name}' on table '{idx.table_name}' "
                            f"does not match the configured naming convention "
                            f"pattern: {pattern}"
                        ),
                        remediation=(
                            f"Rename the index to follow the naming convention. "
                            f"Pattern: {pattern}"
                        ),
                    )
                )

        return violations
