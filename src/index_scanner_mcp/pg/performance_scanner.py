"""Performance scanner for detecting SQL query anti-patterns.

This module provides the PerformanceScanner class that analyzes SQL files
for performance-related issues such as SELECT *, missing WHERE clauses,
leading wildcards in LIKE, ORDER BY RANDOM(), large OFFSET values,
Cartesian JOINs, and function calls on indexed columns in WHERE clauses.
"""

from __future__ import annotations

import re
from pathlib import Path

from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import (
    Action,
    Severity,
    SQLQuery,
    Violation,
    ViolationCategory,
)
from index_scanner_mcp.pg.sql_parser import SQLParser


class PerformanceScanner:
    """Scan SQL files for performance anti-patterns."""

    # Rule IDs for each check
    RULE_SELECT_STAR = "PERF001"
    RULE_MISSING_WHERE_DML = "PERF002"
    RULE_MISSING_WHERE_SELECT = "PERF003"
    RULE_LEADING_WILDCARD = "PERF004"
    RULE_ORDER_BY_RANDOM = "PERF005"
    RULE_LARGE_OFFSET = "PERF006"
    RULE_CARTESIAN_JOIN = "PERF007"
    RULE_FUNCTION_ON_INDEXED_COLUMN = "PERF008"

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config
        self._parser = SQLParser()

    def scan_file(self, filepath: str) -> list[Violation]:
        """Read a SQL file and check for performance anti-patterns.

        Args:
            filepath: Path to the SQL file to scan.

        Returns:
            A list of Violation objects for detected anti-patterns.
        """
        content = Path(filepath).read_text(encoding="utf-8")
        queries = self._parser.extract_queries(content, file_path=filepath)

        violations: list[Violation] = []
        for query in queries:
            violations.extend(self._check_select_star(query))
            violations.extend(self._check_missing_where(query))
            violations.extend(self._check_cartesian_join(query))
            violations.extend(self._check_leading_wildcard(query))
            violations.extend(self._check_function_on_indexed_column(query))
            violations.extend(self._check_large_offset(query))
            violations.extend(self._check_order_by_random(query))

        return violations

    # ─── Internal Check Methods ──────────────────────────────────────────

    def _check_select_star(self, query: SQLQuery) -> list[Violation]:
        """Detect SELECT * usage (Requirement 5.1)."""
        if self._is_rule_disabled(self.RULE_SELECT_STAR):
            return []

        if query.query_type != "SELECT":
            return []

        # Check for SELECT * pattern in the raw SQL
        if re.search(r"\bSELECT\s+\*\s", query.raw_sql, re.IGNORECASE):
            return [
                self._create_violation(
                    rule_id=self.RULE_SELECT_STAR,
                    severity=Severity.HIGH,
                    action=Action.WARN,
                    file_path=query.file_path,
                    line_number=query.line_number,
                    description=(
                        "SELECT * detected. Selecting all columns can cause "
                        "unnecessary I/O and prevent covering index usage."
                    ),
                    remediation=(
                        "Explicitly list only the columns needed by the application."
                    ),
                )
            ]
        return []

    def _check_missing_where(self, query: SQLQuery) -> list[Violation]:
        """Detect DELETE/UPDATE without WHERE (Critical) and SELECT without WHERE (High).

        Requirements 5.2, 5.3, 5.4.
        """
        violations: list[Violation] = []

        if query.query_type in ("DELETE", "UPDATE"):
            if self._is_rule_disabled(self.RULE_MISSING_WHERE_DML):
                return []
            if not query.has_where:
                violations.append(
                    self._create_violation(
                        rule_id=self.RULE_MISSING_WHERE_DML,
                        severity=Severity.CRITICAL,
                        action=Action.BLOCK_PR,
                        file_path=query.file_path,
                        line_number=query.line_number,
                        description=(
                            f"{query.query_type} without WHERE clause detected. "
                            f"This will affect all rows in the table."
                        ),
                        remediation=(
                            f"Add a WHERE clause to limit the scope of the "
                            f"{query.query_type} statement."
                        ),
                    )
                )
        elif query.query_type == "SELECT":
            if self._is_rule_disabled(self.RULE_MISSING_WHERE_SELECT):
                return []
            if not query.has_where:
                violations.append(
                    self._create_violation(
                        rule_id=self.RULE_MISSING_WHERE_SELECT,
                        severity=Severity.HIGH,
                        action=Action.WARN,
                        file_path=query.file_path,
                        line_number=query.line_number,
                        description=(
                            "SELECT without WHERE clause detected. On large tables "
                            "this causes full table scans."
                        ),
                        remediation=(
                            "Add a WHERE clause to filter rows, or confirm that a "
                            "full table scan is intentional for this query."
                        ),
                    )
                )

        return violations

    def _check_cartesian_join(self, query: SQLQuery) -> list[Violation]:
        """Detect JOIN without ON clause (Cartesian product). Requirement 5.7."""
        if self._is_rule_disabled(self.RULE_CARTESIAN_JOIN):
            return []

        raw_upper = query.raw_sql.upper()

        # Check if there's a JOIN keyword present
        has_join = bool(
            re.search(
                r"\b(?:INNER\s+)?JOIN\b|\bLEFT\s+(?:OUTER\s+)?JOIN\b|"
                r"\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b",
                query.raw_sql,
                re.IGNORECASE,
            )
        )

        if not has_join:
            # Also detect implicit Cartesian: FROM t1, t2 without WHERE
            # Multiple tables in FROM without WHERE is a Cartesian product
            if len(query.tables) > 1 and not query.has_where and "CROSS JOIN" not in raw_upper:
                return [
                    self._create_violation(
                        rule_id=self.RULE_CARTESIAN_JOIN,
                        severity=Severity.CRITICAL,
                        action=Action.BLOCK_PR,
                        file_path=query.file_path,
                        line_number=query.line_number,
                        description=(
                            "Cartesian product detected: multiple tables in FROM "
                            "without a WHERE clause to join them."
                        ),
                        remediation=(
                            "Add a JOIN with an ON clause, or add a WHERE clause "
                            "to establish the join condition."
                        ),
                    )
                ]
            return []

        # Has explicit JOIN - check if there's no ON clause
        # A JOIN without ON is a Cartesian join
        # Look for JOIN ... without a following ON
        join_pattern = re.finditer(
            r"\b(?:INNER\s+|LEFT\s+(?:OUTER\s+)?|RIGHT\s+(?:OUTER\s+)?|"
            r"FULL\s+(?:OUTER\s+)?)?JOIN\s+(\S+)",
            query.raw_sql,
            re.IGNORECASE,
        )

        for match in join_pattern:
            # Check if there's an ON clause after this JOIN before the next JOIN/WHERE/GROUP/ORDER/LIMIT
            after_join = query.raw_sql[match.end():]
            # Look for ON before the next major clause
            next_clause = re.search(
                r"\b(?:JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|UNION)\b",
                after_join,
                re.IGNORECASE,
            )
            segment = after_join[:next_clause.start()] if next_clause else after_join
            has_on = bool(re.search(r"\bON\b", segment, re.IGNORECASE))
            has_using = bool(re.search(r"\bUSING\b", segment, re.IGNORECASE))

            if not has_on and not has_using:
                return [
                    self._create_violation(
                        rule_id=self.RULE_CARTESIAN_JOIN,
                        severity=Severity.CRITICAL,
                        action=Action.BLOCK_PR,
                        file_path=query.file_path,
                        line_number=query.line_number,
                        description=(
                            "JOIN without ON clause detected (Cartesian product). "
                            "This produces a cross product of all rows."
                        ),
                        remediation=(
                            "Add an ON clause to the JOIN specifying the join condition."
                        ),
                    )
                ]

        return []

    def _check_leading_wildcard(self, query: SQLQuery) -> list[Violation]:
        """Detect LIKE with leading wildcard pattern. Requirement 5.9."""
        if self._is_rule_disabled(self.RULE_LEADING_WILDCARD):
            return []

        # Match LIKE '%...' or LIKE '%..._' patterns (leading wildcard)
        if re.search(
            r"\bLIKE\s+['\"]%",
            query.raw_sql,
            re.IGNORECASE,
        ):
            return [
                self._create_violation(
                    rule_id=self.RULE_LEADING_WILDCARD,
                    severity=Severity.HIGH,
                    action=Action.WARN,
                    file_path=query.file_path,
                    line_number=query.line_number,
                    description=(
                        "LIKE with leading wildcard detected (e.g., LIKE '%value'). "
                        "This prevents index usage and causes full table scans."
                    ),
                    remediation=(
                        "Consider using full-text search (tsvector/tsquery), "
                        "a trigram index (pg_trgm), or restructuring the query "
                        "to avoid leading wildcards."
                    ),
                )
            ]
        return []

    def _check_function_on_indexed_column(self, query: SQLQuery) -> list[Violation]:
        """Detect function calls on columns in WHERE clause. Requirements 5.10, 5.11."""
        if self._is_rule_disabled(self.RULE_FUNCTION_ON_INDEXED_COLUMN):
            return []

        # Extract the WHERE clause from raw SQL
        where_match = re.search(
            r"\bWHERE\s+(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|\bUNION\b|$)",
            query.raw_sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not where_match:
            return []

        where_clause = where_match.group(1)

        # Common functions that prevent index usage when applied to columns
        # Pattern: FUNCTION(column) or FUNCTION(table.column) in comparison context
        match = re.search(
            r"\b(LOWER|UPPER|TRIM|LTRIM|RTRIM|COALESCE|CAST|TO_CHAR|TO_DATE|"
            r"TO_TIMESTAMP|TO_NUMBER|DATE|EXTRACT|LENGTH|SUBSTR|SUBSTRING|"
            r"LEFT|RIGHT|REPLACE|CONCAT|ABS|CEIL|FLOOR|ROUND|"
            r"DATE_TRUNC|AGE)\s*\(\s*(\w+(?:\.\w+)?)\s*",
            where_clause,
            re.IGNORECASE,
        )

        if match:
            func_name = match.group(1).upper()
            col_name = match.group(2)
            return [
                self._create_violation(
                    rule_id=self.RULE_FUNCTION_ON_INDEXED_COLUMN,
                    severity=Severity.HIGH,
                    action=Action.WARN,
                    file_path=query.file_path,
                    line_number=query.line_number,
                    description=(
                        f"Function {func_name}() applied to column '{col_name}' "
                        f"in WHERE clause. This prevents index usage on that column."
                    ),
                    remediation=(
                        f"Create an expression index: "
                        f"CREATE INDEX idx_on_{col_name}_{func_name.lower()} "
                        f"ON table_name ({func_name.lower()}({col_name})); "
                        f"Or restructure the query to avoid wrapping the column "
                        f"in a function."
                    ),
                )
            ]
        return []

    def _check_large_offset(self, query: SQLQuery) -> list[Violation]:
        """Detect OFFSET exceeding configured threshold. Requirement 5.6."""
        if self._is_rule_disabled(self.RULE_LARGE_OFFSET):
            return []

        if query.offset_value is None:
            return []

        threshold = self._config.thresholds.offset_limit
        if query.offset_value > threshold:
            return [
                self._create_violation(
                    rule_id=self.RULE_LARGE_OFFSET,
                    severity=Severity.HIGH,
                    action=Action.WARN,
                    file_path=query.file_path,
                    line_number=query.line_number,
                    description=(
                        f"OFFSET {query.offset_value} exceeds threshold "
                        f"({threshold}). Large offsets cause the database to "
                        f"scan and discard many rows."
                    ),
                    remediation=(
                        "Use keyset pagination (WHERE id > last_seen_id) "
                        "instead of OFFSET for better performance on large datasets."
                    ),
                )
            ]
        return []

    def _check_order_by_random(self, query: SQLQuery) -> list[Violation]:
        """Detect ORDER BY RANDOM(). Requirement 5.5."""
        if self._is_rule_disabled(self.RULE_ORDER_BY_RANDOM):
            return []

        if re.search(
            r"\bORDER\s+BY\s+RANDOM\s*\(\s*\)",
            query.raw_sql,
            re.IGNORECASE,
        ):
            return [
                self._create_violation(
                    rule_id=self.RULE_ORDER_BY_RANDOM,
                    severity=Severity.HIGH,
                    action=Action.WARN,
                    file_path=query.file_path,
                    line_number=query.line_number,
                    description=(
                        "ORDER BY RANDOM() detected. This requires a full table "
                        "scan and sort, which is extremely expensive on large tables."
                    ),
                    remediation=(
                        "Use TABLESAMPLE for approximate random sampling, or "
                        "pre-compute random ordering with a materialized view, "
                        "or use a randomized ID-based approach."
                    ),
                )
            ]
        return []

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _is_rule_disabled(self, rule_id: str) -> bool:
        """Check if a rule is disabled in the configuration."""
        return rule_id in self._config.disabled_rules

    def _get_severity(self, rule_id: str, default: Severity) -> Severity:
        """Get the effective severity for a rule, respecting overrides."""
        override = self._config.severity_overrides.get(rule_id)
        if override:
            severity_map = {
                "Critical": Severity.CRITICAL,
                "High": Severity.HIGH,
                "Medium": Severity.MEDIUM,
            }
            return severity_map.get(override, default)
        return default

    def _create_violation(
        self,
        rule_id: str,
        severity: Severity,
        action: Action,
        file_path: str,
        line_number: int,
        description: str,
        remediation: str,
    ) -> Violation:
        """Create a Violation with severity override support."""
        effective_severity = self._get_severity(rule_id, severity)
        return Violation(
            rule_id=rule_id,
            category=ViolationCategory.PERFORMANCE,
            severity=effective_severity,
            action=action,
            file_path=file_path,
            line_number=line_number,
            description=description,
            remediation=remediation,
        )
