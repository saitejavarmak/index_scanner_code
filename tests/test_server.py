"""Tests for the refactored server.py — scan_and_export tool and modular integration."""

import json
import os
import tempfile
import shutil

import pytest


# We need the server module on the path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the server functions directly (they are module-level, not class methods)
from index_scanner_mcp.server import scan_and_export, scan_indexes, suggest_indexes


@pytest.fixture
def sample_project(tmp_path):
    """Create a minimal Java project with MongoDB annotations."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "User.java").write_text(
        'package com.example;\n'
        '\n'
        'import org.springframework.data.mongodb.core.mapping.Document;\n'
        'import org.springframework.data.mongodb.core.index.Indexed;\n'
        'import org.springframework.data.mongodb.core.index.CompoundIndex;\n'
        '\n'
        '@Document(collection = "users")\n'
        '@CompoundIndex(def = "{\'email\': 1, \'tenantId\': 1}")\n'
        'public class User {\n'
        '    @Indexed(unique = true)\n'
        '    private String email;\n'
        '\n'
        '    @Indexed\n'
        '    private String tenantId;\n'
        '}\n'
    )
    return str(tmp_path)


@pytest.fixture
def empty_project(tmp_path):
    """Create a project directory with no Java files."""
    (tmp_path / "readme.txt").write_text("empty project")
    return str(tmp_path)


class TestScanAndExport:
    """Tests for the scan_and_export MCP tool."""

    def test_returns_json(self, sample_project):
        result = scan_and_export(sample_project)
        data = json.loads(result)
        assert "script" in data
        assert "report" in data

    def test_mongo_shell_format(self, sample_project):
        result = scan_and_export(sample_project, format="mongo_shell")
        data = json.loads(result)
        assert data["format"] == "mongo_shell"
        assert "createIndex" in data["script"]

    def test_pymongo_format(self, sample_project):
        result = scan_and_export(sample_project, format="pymongo")
        data = json.loads(result)
        assert data["format"] == "pymongo"
        assert "create_index" in data["script"]
        assert "#!/usr/bin/env python3" in data["script"]

    def test_pymongo_uses_python3(self, sample_project):
        result = scan_and_export(sample_project, format="pymongo")
        data = json.loads(result)
        assert data["script"].startswith("#!/usr/bin/env python3")

    def test_includes_db_name(self, sample_project):
        result = scan_and_export(sample_project, format="mongo_shell", db_name="testdb")
        data = json.loads(result)
        assert "use testdb;" in data["script"]

    def test_report_has_summary(self, sample_project):
        result = scan_and_export(sample_project)
        data = json.loads(result)
        report = data["report"]
        assert "summary" in report
        assert report["summary"]["total_indexes"] > 0
        assert report["summary"]["files_scanned"] > 0

    def test_report_has_indexes(self, sample_project):
        result = scan_and_export(sample_project)
        data = json.loads(result)
        report = data["report"]
        assert len(report["indexes"]) > 0
        for idx in report["indexes"]:
            assert "collection" in idx
            assert "fields" in idx
            assert "source" in idx

    def test_indexes_found_count(self, sample_project):
        result = scan_and_export(sample_project)
        data = json.loads(result)
        assert data["indexes_found"] == len(data["report"]["indexes"])

    def test_invalid_path(self):
        result = scan_and_export("/nonexistent/path/xyz")
        data = json.loads(result)
        assert "error" in data

    def test_file_path_not_directory(self, sample_project):
        filepath = os.path.join(sample_project, "src", "User.java")
        result = scan_and_export(filepath)
        data = json.loads(result)
        assert "error" in data

    def test_invalid_format(self, sample_project):
        result = scan_and_export(sample_project, format="invalid_format")
        data = json.loads(result)
        assert "error" in data
        assert "Invalid format" in data["error"]

    def test_empty_project(self, empty_project):
        result = scan_and_export(empty_project)
        data = json.loads(result)
        assert data["indexes_found"] == 0
        assert data["script"] == ""


class TestExistingToolsPreserved:
    """Verify existing MCP tools still work after refactoring."""

    def test_scan_indexes_returns_json(self, sample_project):
        result = scan_indexes(sample_project)
        data = json.loads(result)
        # Should find index annotations
        assert "total_indexes_found" in data or "message" in data

    def test_scan_indexes_invalid_path(self):
        result = scan_indexes("/nonexistent/path/xyz")
        data = json.loads(result)
        assert "error" in data

    def test_suggest_indexes_returns_json(self, sample_project):
        result = suggest_indexes(sample_project)
        data = json.loads(result)
        # May or may not find suggestions, but should return valid JSON
        assert isinstance(data, dict)
