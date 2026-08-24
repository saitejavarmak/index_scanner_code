"""Application Code Scanner for the PostgreSQL Guardrails system.

Scans Java source files for unsafe database access patterns including
SQL injection risks, unsafe Statement usage, and unparameterized DML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import (
    Action,
    Severity,
    Violation,
    ViolationCategory,
)


class ApplicationCodeScanner:
    """Scans Java source code for unsafe database access patterns."""

    # Rule IDs for this scanner
    RULE_STATEMENT_USAGE = "APP001"
    RULE_EXECUTE_ON_STATEMENT = "APP002"
    RULE_STRING_CONCAT_SQL = "APP003"
    RULE_SELECT_STAR = "APP004"
    RULE_UNPARAMETERIZED_DELETE = "APP005"
    RULE_UNPARAMETERIZED_UPDATE = "APP006"

    def __init__(self, config: GuardrailConfig) -> None:
        self.config = config

    def scan_file(self, filepath: str) -> list[Violation]:
        """Scan a Java source file for unsafe database access patterns.

        Args:
            filepath: Path to a Java source file.

        Returns:
            A list of Violation objects for detected issues.
        """
        path = Path(filepath)
        if not path.exists() or not path.is_file():
            return []
        if path.suffix != ".java":
            return []

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        violations: list[Violation] = []
        violations.extend(self._check_statement_usage(content, filepath))
        violations.extend(self._check_string_concatenation_sql(content, filepath))
        violations.extend(self._check_select_star_in_code(content, filepath))
        violations.extend(self._check_unparameterized_dml(content, filepath))
        return violations

    def scan_directory(self, directory: str) -> list[Violation]:
        """Scan all Java files in a directory tree for unsafe patterns.

        Args:
            directory: Path to the directory to scan.

        Returns:
            A list of Violation objects for detected issues.
        """
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return []

        violations: list[Violation] = []
        for java_file in dir_path.rglob("*.java"):
            if java_file.is_file():
                violations.extend(self.scan_file(str(java_file)))
        return violations

    def _get_severity(self, rule_id: str, default: Severity) -> Severity:
        """Get the effective severity for a rule, respecting config overrides."""
        if rule_id in self.config.severity_overrides:
            override_value = self.config.severity_overrides[rule_id]
            try:
                return Severity(override_value)
            except ValueError:
                pass
        return default

    def _is_rule_disabled(self, rule_id: str) -> bool:
        """Check if a rule is disabled in the configuration."""
        return rule_id in self.config.disabled_rules

    def _get_action(self, severity: Severity) -> Action:
        """Determine the action based on severity level."""
        if severity == Severity.CRITICAL:
            return Action.BLOCK_PR
        return Action.WARN

    def _check_statement_usage(
        self, content: str, filepath: str
    ) -> list[Violation]:
        """Detect Statement() or createStatement() usage (SQL injection risk).

        Also detects execute(), executeQuery(), executeUpdate() called on
        Statement objects (not PreparedStatement).

        Requirements: 7.2, 7.3
        """
        violations: list[Violation] = []
        lines = content.splitlines()

        # Check for Statement() / createStatement() usage (Req 7.2)
        if not self._is_rule_disabled(self.RULE_STATEMENT_USAGE):
            severity = self._get_severity(
                self.RULE_STATEMENT_USAGE, Severity.CRITICAL
            )
            # Pattern: new Statement() or .createStatement()
            pattern = re.compile(
                r"\b(?:new\s+Statement\s*\(|\.createStatement\s*\()"
            )
            for i, line in enumerate(lines, start=1):
                if pattern.search(line):
                    violations.append(
                        Violation(
                            rule_id=self.RULE_STATEMENT_USAGE,
                            category=ViolationCategory.APPLICATION_CODE,
                            severity=severity,
                            action=self._get_action(severity),
                            file_path=filepath,
                            line_number=i,
                            description=(
                                "Use of Statement/createStatement() detected. "
                                "This is vulnerable to SQL injection."
                            ),
                            remediation=(
                                "Use PreparedStatement with parameterized queries "
                                "instead of Statement to prevent SQL injection."
                            ),
                        )
                    )

        # Check for execute/executeQuery/executeUpdate on Statement (Req 7.3)
        if not self._is_rule_disabled(self.RULE_EXECUTE_ON_STATEMENT):
            severity = self._get_severity(
                self.RULE_EXECUTE_ON_STATEMENT, Severity.CRITICAL
            )
            # Detect Statement variable declarations and track their usage
            # Pattern: Statement variable names
            stmt_var_pattern = re.compile(
                r"\bStatement\s+(\w+)\s*[=;]"
            )
            # Also match method-chained calls like conn.createStatement().executeQuery(...)
            chained_pattern = re.compile(
                r"\.createStatement\s*\(\s*\)\s*\.\s*(?:execute|executeQuery|executeUpdate)\s*\("
            )

            # Find all Statement variable names
            stmt_vars: set[str] = set()
            for line in lines:
                match = stmt_var_pattern.search(line)
                if match:
                    stmt_vars.add(match.group(1))

            # Check for execute calls on Statement variables
            if stmt_vars:
                execute_pattern = re.compile(
                    r"\b("
                    + "|".join(re.escape(v) for v in stmt_vars)
                    + r")\s*\.\s*(?:execute|executeQuery|executeUpdate)\s*\("
                )
                for i, line in enumerate(lines, start=1):
                    if execute_pattern.search(line):
                        violations.append(
                            Violation(
                                rule_id=self.RULE_EXECUTE_ON_STATEMENT,
                                category=ViolationCategory.APPLICATION_CODE,
                                severity=severity,
                                action=self._get_action(severity),
                                file_path=filepath,
                                line_number=i,
                                description=(
                                    "execute/executeQuery/executeUpdate called on "
                                    "a Statement object. This is vulnerable to SQL injection."
                                ),
                                remediation=(
                                    "Use PreparedStatement with parameterized queries. "
                                    "Replace Statement.execute*() with "
                                    "PreparedStatement.execute*()."
                                ),
                            )
                        )

            # Check for chained calls: conn.createStatement().executeQuery(...)
            for i, line in enumerate(lines, start=1):
                if chained_pattern.search(line):
                    violations.append(
                        Violation(
                            rule_id=self.RULE_EXECUTE_ON_STATEMENT,
                            category=ViolationCategory.APPLICATION_CODE,
                            severity=severity,
                            action=self._get_action(severity),
                            file_path=filepath,
                            line_number=i,
                            description=(
                                "execute/executeQuery/executeUpdate called on "
                                "a Statement object (chained). This is vulnerable "
                                "to SQL injection."
                            ),
                            remediation=(
                                "Use PreparedStatement with parameterized queries. "
                                "Replace Statement.execute*() with "
                                "PreparedStatement.execute*()."
                            ),
                        )
                    )

        return violations

    def _check_string_concatenation_sql(
        self, content: str, filepath: str
    ) -> list[Violation]:
        """Detect string concatenation in SQL query construction.

        Looks for patterns like: String sql = "SELECT ... " + variable
        or sql += variable patterns that indicate SQL injection risk.

        Requirements: 7.4
        """
        if self._is_rule_disabled(self.RULE_STRING_CONCAT_SQL):
            return []

        violations: list[Violation] = []
        severity = self._get_severity(
            self.RULE_STRING_CONCAT_SQL, Severity.CRITICAL
        )
        lines = content.splitlines()

        # Pattern: SQL keywords in string literal concatenated with a variable
        # Matches patterns like:
        #   "SELECT ... " + varName
        #   "WHERE ... " + someVar
        #   sql = "..." + variable
        #   sql += variable
        sql_keywords = (
            r"(?:SELECT|INSERT|UPDATE|DELETE|WHERE|FROM|SET|VALUES|INTO|JOIN|"
            r"AND|OR|ORDER\s+BY|GROUP\s+BY|HAVING)"
        )

        # Pattern 1: String literal with SQL keyword followed by + variable
        concat_pattern = re.compile(
            r'"[^"]*\b' + sql_keywords + r'\b[^"]*"\s*\+\s*(?!"|\')\w+',
            re.IGNORECASE,
        )

        # Pattern 2: variable + "SQL keyword..."
        concat_pattern2 = re.compile(
            r'\w+\s*\+\s*"[^"]*\b' + sql_keywords + r"\b",
            re.IGNORECASE,
        )

        # Pattern 3: += with SQL-related variable context
        # Looking for lines like: sql += someVariable (not string literals)
        concat_assign_pattern = re.compile(
            r"(?:sql|query|stmt|statement)\w*\s*\+\=\s*(?!\s*\")[^\";]+",
            re.IGNORECASE,
        )

        for i, line in enumerate(lines, start=1):
            if (
                concat_pattern.search(line)
                or concat_pattern2.search(line)
                or concat_assign_pattern.search(line)
            ):
                violations.append(
                    Violation(
                        rule_id=self.RULE_STRING_CONCAT_SQL,
                        category=ViolationCategory.APPLICATION_CODE,
                        severity=severity,
                        action=self._get_action(severity),
                        file_path=filepath,
                        line_number=i,
                        description=(
                            "String concatenation detected in SQL query construction. "
                            "This is vulnerable to SQL injection."
                        ),
                        remediation=(
                            "Use PreparedStatement with parameter placeholders (?) "
                            "instead of string concatenation to prevent SQL injection."
                        ),
                    )
                )

        return violations

    def _check_select_star_in_code(
        self, content: str, filepath: str
    ) -> list[Violation]:
        """Detect SELECT * in Java string literals.

        Requirements: 7.1
        """
        if self._is_rule_disabled(self.RULE_SELECT_STAR):
            return []

        violations: list[Violation] = []
        severity = self._get_severity(self.RULE_SELECT_STAR, Severity.HIGH)
        lines = content.splitlines()

        # Pattern: SELECT * inside a string literal (between quotes)
        # Matches: "SELECT * FROM ...", "select * from ..."
        select_star_pattern = re.compile(
            r'"[^"]*\bSELECT\s+\*[^"]*"', re.IGNORECASE
        )

        for i, line in enumerate(lines, start=1):
            if select_star_pattern.search(line):
                violations.append(
                    Violation(
                        rule_id=self.RULE_SELECT_STAR,
                        category=ViolationCategory.APPLICATION_CODE,
                        severity=severity,
                        action=self._get_action(severity),
                        file_path=filepath,
                        line_number=i,
                        description=(
                            "SELECT * detected in SQL string literal. "
                            "This fetches unnecessary columns and impacts performance."
                        ),
                        remediation=(
                            "Specify only the required columns explicitly "
                            "instead of using SELECT *."
                        ),
                    )
                )

        return violations

    def _check_unparameterized_dml(
        self, content: str, filepath: str
    ) -> list[Violation]:
        """Detect hardcoded DELETE/UPDATE without parameterized conditions.

        Looks for DELETE/UPDATE in string literals without '?' placeholders,
        indicating they use hardcoded values instead of parameters.

        Requirements: 7.5, 7.6
        """
        violations: list[Violation] = []
        lines = content.splitlines()

        # Check DELETE without parameterized conditions (Req 7.5)
        if not self._is_rule_disabled(self.RULE_UNPARAMETERIZED_DELETE):
            severity = self._get_severity(
                self.RULE_UNPARAMETERIZED_DELETE, Severity.HIGH
            )
            # Pattern: DELETE keyword in a string literal without ? placeholder
            delete_pattern = re.compile(
                r'"[^"]*\bDELETE\b[^"]*"', re.IGNORECASE
            )
            for i, line in enumerate(lines, start=1):
                match = delete_pattern.search(line)
                if match:
                    literal = match.group(0)
                    # If the literal does not contain '?' it's unparameterized
                    if "?" not in literal:
                        violations.append(
                            Violation(
                                rule_id=self.RULE_UNPARAMETERIZED_DELETE,
                                category=ViolationCategory.APPLICATION_CODE,
                                severity=severity,
                                action=self._get_action(severity),
                                file_path=filepath,
                                line_number=i,
                                description=(
                                    "Hardcoded DELETE statement without parameterized "
                                    "conditions detected in string literal."
                                ),
                                remediation=(
                                    "Use PreparedStatement with '?' placeholders for "
                                    "WHERE clause conditions in DELETE statements."
                                ),
                            )
                        )

        # Check UPDATE without parameterized conditions (Req 7.6)
        if not self._is_rule_disabled(self.RULE_UNPARAMETERIZED_UPDATE):
            severity = self._get_severity(
                self.RULE_UNPARAMETERIZED_UPDATE, Severity.HIGH
            )
            # Pattern: UPDATE keyword in a string literal without ? placeholder
            update_pattern = re.compile(
                r'"[^"]*\bUPDATE\b[^"]*"', re.IGNORECASE
            )
            for i, line in enumerate(lines, start=1):
                match = update_pattern.search(line)
                if match:
                    literal = match.group(0)
                    # If the literal does not contain '?' it's unparameterized
                    if "?" not in literal:
                        violations.append(
                            Violation(
                                rule_id=self.RULE_UNPARAMETERIZED_UPDATE,
                                category=ViolationCategory.APPLICATION_CODE,
                                severity=severity,
                                action=self._get_action(severity),
                                file_path=filepath,
                                line_number=i,
                                description=(
                                    "Hardcoded UPDATE statement without parameterized "
                                    "conditions detected in string literal."
                                ),
                                remediation=(
                                    "Use PreparedStatement with '?' placeholders for "
                                    "SET and WHERE clause values in UPDATE statements."
                                ),
                            )
                        )

        return violations
