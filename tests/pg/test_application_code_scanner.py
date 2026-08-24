"""Unit tests for the ApplicationCodeScanner."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from index_scanner_mcp.pg.application_code_scanner import ApplicationCodeScanner
from index_scanner_mcp.pg.config_loader import GuardrailConfig
from index_scanner_mcp.pg.models import Action, Severity, ViolationCategory


@pytest.fixture
def scanner() -> ApplicationCodeScanner:
    """Create a scanner with default config."""
    return ApplicationCodeScanner(GuardrailConfig())


@pytest.fixture
def tmp_java_file(tmp_path: Path):
    """Helper to create a temporary Java file with given content."""

    def _create(content: str, filename: str = "Test.java") -> str:
        file_path = tmp_path / filename
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    return _create


class TestStatementUsage:
    """Tests for detecting Statement/createStatement usage (Req 7.2)."""

    def test_create_statement_detected(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        Statement stmt = conn.createStatement();
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP001"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.CRITICAL
        assert rule_violations[0].category == ViolationCategory.APPLICATION_CODE

    def test_new_statement_detected(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        Statement stmt = new Statement();
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP001"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.CRITICAL

    def test_prepared_statement_not_flagged(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        PreparedStatement ps = conn.prepareStatement("SELECT id FROM users WHERE id = ?");
        ps.setInt(1, userId);
        ResultSet rs = ps.executeQuery();
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        # APP001 (Statement usage) should NOT be triggered
        rule_violations = [
            v for v in violations if v.rule_id == "APP001"
        ]
        assert len(rule_violations) == 0


class TestExecuteOnStatement:
    """Tests for detecting execute/executeQuery/executeUpdate on Statement (Req 7.3)."""

    def test_execute_query_on_statement(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery("SELECT * FROM users");
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP002"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.CRITICAL

    def test_execute_update_on_statement(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void update() {
        Statement stmt = conn.createStatement();
        stmt.executeUpdate("DELETE FROM users WHERE id = 1");
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP002"
        ]
        assert len(rule_violations) >= 1

    def test_chained_create_statement_execute(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        ResultSet rs = conn.createStatement().executeQuery("SELECT 1");
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP002"
        ]
        assert len(rule_violations) >= 1


class TestStringConcatenationSQL:
    """Tests for detecting string concatenation in SQL (Req 7.4)."""

    def test_concat_variable_in_select(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query(String name) {
        String sql = "SELECT * FROM users WHERE name = '" + name + "'";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP003"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.CRITICAL

    def test_concat_in_where_clause(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query(int id) {
        String sql = "SELECT name FROM users WHERE id = " + id;
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP003"
        ]
        assert len(rule_violations) >= 1

    def test_parameterized_query_not_flagged(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query(int id) {
        String sql = "SELECT name FROM users WHERE id = ?";
        PreparedStatement ps = conn.prepareStatement(sql);
        ps.setInt(1, id);
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP003"
        ]
        assert len(rule_violations) == 0

    def test_plus_equals_with_variable(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query(String filter) {
        String sql = "SELECT * FROM orders";
        sql += filter;
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP003"
        ]
        assert len(rule_violations) >= 1


class TestSelectStarInCode:
    """Tests for detecting SELECT * in Java string literals (Req 7.1)."""

    def test_select_star_detected(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        String sql = "SELECT * FROM users";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP004"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.HIGH

    def test_select_specific_columns_not_flagged(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        String sql = "SELECT id, name FROM users";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP004"
        ]
        assert len(rule_violations) == 0

    def test_select_star_case_insensitive(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query() {
        String sql = "select * from users";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP004"
        ]
        assert len(rule_violations) >= 1


class TestUnparameterizedDML:
    """Tests for detecting unparameterized DELETE/UPDATE (Req 7.5, 7.6)."""

    def test_hardcoded_delete_detected(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void delete() {
        String sql = "DELETE FROM users WHERE id = 1";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP005"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.HIGH

    def test_parameterized_delete_not_flagged(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void delete() {
        String sql = "DELETE FROM users WHERE id = ?";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP005"
        ]
        assert len(rule_violations) == 0

    def test_hardcoded_update_detected(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void update() {
        String sql = "UPDATE users SET name = 'admin' WHERE id = 1";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP006"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.HIGH

    def test_parameterized_update_not_flagged(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void update() {
        String sql = "UPDATE users SET name = ? WHERE id = ?";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP006"
        ]
        assert len(rule_violations) == 0


class TestConfiguration:
    """Tests for config-driven behavior (disabled rules, severity overrides)."""

    def test_disabled_rule_produces_no_violations(self, tmp_java_file):
        config = GuardrailConfig(
            disabled_rules=["APP001", "APP002", "APP003", "APP004", "APP005", "APP006"]
        )
        scanner = ApplicationCodeScanner(config)
        code = '''
public class Dao {
    public void query() {
        Statement stmt = conn.createStatement();
        stmt.executeQuery("SELECT * FROM users");
        String sql = "DELETE FROM users WHERE id = " + id;
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        assert len(violations) == 0

    def test_severity_override_applied(self, tmp_java_file):
        config = GuardrailConfig(
            severity_overrides={"APP004": "Medium"}
        )
        scanner = ApplicationCodeScanner(config)
        code = '''
public class Dao {
    public void query() {
        String sql = "SELECT * FROM users";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        rule_violations = [
            v for v in violations if v.rule_id == "APP004"
        ]
        assert len(rule_violations) >= 1
        assert rule_violations[0].severity == Severity.MEDIUM

    def test_single_rule_disabled(self, tmp_java_file):
        config = GuardrailConfig(disabled_rules=["APP004"])
        scanner = ApplicationCodeScanner(config)
        code = '''
public class Dao {
    public void query() {
        Statement stmt = conn.createStatement();
        String sql = "SELECT * FROM users";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        # APP004 should be skipped, but APP001 should still fire
        assert all(v.rule_id != "APP004" for v in violations)
        app001 = [v for v in violations if v.rule_id == "APP001"]
        assert len(app001) >= 1


class TestFileHandling:
    """Tests for file handling edge cases."""

    def test_non_java_file_skipped(self, scanner: ApplicationCodeScanner, tmp_path):
        file_path = tmp_path / "Test.py"
        file_path.write_text("Statement stmt = conn.createStatement();")
        violations = scanner.scan_file(str(file_path))
        assert len(violations) == 0

    def test_nonexistent_file(self, scanner: ApplicationCodeScanner):
        violations = scanner.scan_file("/nonexistent/path/Test.java")
        assert len(violations) == 0

    def test_scan_directory(self, scanner: ApplicationCodeScanner, tmp_path):
        # Create multiple Java files
        (tmp_path / "Good.java").write_text(
            'class Good { String sql = "SELECT id FROM users WHERE id = ?"; }'
        )
        (tmp_path / "Bad.java").write_text(
            'class Bad { String sql = "SELECT * FROM users"; }'
        )
        violations = scanner.scan_directory(str(tmp_path))
        assert any(v.rule_id == "APP004" for v in violations)

    def test_scan_directory_nonexistent(self, scanner: ApplicationCodeScanner):
        violations = scanner.scan_directory("/nonexistent/path/")
        assert len(violations) == 0

    def test_scan_directory_recursive(
        self, scanner: ApplicationCodeScanner, tmp_path
    ):
        subdir = tmp_path / "sub" / "pkg"
        subdir.mkdir(parents=True)
        (subdir / "Dao.java").write_text(
            'class Dao { Statement s = conn.createStatement(); }'
        )
        violations = scanner.scan_directory(str(tmp_path))
        assert any(v.rule_id == "APP001" for v in violations)

    def test_all_violations_have_application_code_category(
        self, scanner: ApplicationCodeScanner, tmp_java_file
    ):
        code = '''
public class Dao {
    public void query(String name) {
        Statement stmt = conn.createStatement();
        stmt.executeQuery("SELECT * FROM users WHERE name = '" + name + "'");
        String sql = "DELETE FROM users WHERE id = 1";
        String sql2 = "UPDATE users SET name = 'x' WHERE id = 2";
    }
}
'''
        filepath = tmp_java_file(code)
        violations = scanner.scan_file(filepath)
        assert len(violations) > 0
        for v in violations:
            assert v.category == ViolationCategory.APPLICATION_CODE
