"""Unit tests for the ConstantResolver class."""

import os
import textwrap

import pytest

from index_scanner_mcp.constant_resolver import ConstantResolver


@pytest.fixture
def resolver() -> ConstantResolver:
    return ConstantResolver()


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project tree with a constants file."""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    constants_file = src / "AppConstants.java"
    constants_file.write_text(
        textwrap.dedent("""\
            package com.example;

            public final class AppConstants {
                public static final String USERID = "userId";
                public static final String JOBIDS = "jobIds";
                public static final String COLL_NAME = "MyCollection";
                public static final int BATCH_SIZE = 100;
                private static final String SECRET = "hidden";
            }
        """)
    )
    return tmp_path


class TestResolveConstants:
    def test_finds_and_parses_constants_file(self, resolver, tmp_project):
        result = resolver.resolve_constants(str(tmp_project))
        assert result["AppConstants.USERID"] == "userId"
        assert result["AppConstants.JOBIDS"] == "jobIds"
        assert result["AppConstants.COLL_NAME"] == "MyCollection"

    def test_unqualified_names_also_present(self, resolver, tmp_project):
        result = resolver.resolve_constants(str(tmp_project))
        assert result["USERID"] == "userId"
        assert result["JOBIDS"] == "jobIds"

    def test_ignores_non_string_constants(self, resolver, tmp_project):
        result = resolver.resolve_constants(str(tmp_project))
        assert "BATCH_SIZE" not in result
        assert "AppConstants.BATCH_SIZE" not in result

    def test_ignores_non_public_constants(self, resolver, tmp_project):
        result = resolver.resolve_constants(str(tmp_project))
        assert "SECRET" not in result
        assert "AppConstants.SECRET" not in result

    def test_empty_dict_when_no_constant_files(self, resolver, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Foo.java").write_text("class Foo {}")
        result = resolver.resolve_constants(str(tmp_path))
        assert result == {}

    def test_skips_excluded_directories(self, resolver, tmp_path):
        target_dir = tmp_path / "target" / "classes"
        target_dir.mkdir(parents=True)
        (target_dir / "AppConstants.java").write_text(
            'public static final String X = "should_not_find";'
        )
        result = resolver.resolve_constants(str(tmp_path))
        assert result == {}

    def test_parses_config_files(self, resolver, tmp_path):
        (tmp_path / "DbConfig.java").write_text(
            textwrap.dedent("""\
                public class DbConfig {
                    public static final String DB_NAME = "mydb";
                }
            """)
        )
        result = resolver.resolve_constants(str(tmp_path))
        assert result["DbConfig.DB_NAME"] == "mydb"
        assert result["DB_NAME"] == "mydb"

    def test_multiple_constant_files(self, resolver, tmp_path):
        (tmp_path / "AppConstants.java").write_text(
            'public class AppConstants { public static final String A = "aaa"; }'
        )
        (tmp_path / "DbConfig.java").write_text(
            'public class DbConfig { public static final String B = "bbb"; }'
        )
        result = resolver.resolve_constants(str(tmp_path))
        assert result["AppConstants.A"] == "aaa"
        assert result["DbConfig.B"] == "bbb"

    def test_each_file_parsed_once(self, resolver, tmp_project):
        """Calling resolve_constants twice should re-scan (fresh state)."""
        result1 = resolver.resolve_constants(str(tmp_project))
        result2 = resolver.resolve_constants(str(tmp_project))
        assert result1 == result2
        # Internal cache should have exactly one class entry
        assert len(resolver.constants) == 1


class TestResolveOrFallback:
    """Tests for resolve_or_fallback – unresolved constant handling (Req 2.4, 8.3)."""

    def test_resolved_reference_returns_value_and_true(self, resolver, tmp_project):
        flat_map = resolver.resolve_constants(str(tmp_project))
        value, was_resolved = resolver.resolve_or_fallback("AppConstants.USERID", flat_map)
        assert value == "userId"
        assert was_resolved is True

    def test_unqualified_reference_resolves(self, resolver, tmp_project):
        flat_map = resolver.resolve_constants(str(tmp_project))
        value, was_resolved = resolver.resolve_or_fallback("JOBIDS", flat_map)
        assert value == "jobIds"
        assert was_resolved is True

    def test_missing_class_returns_raw_and_false(self, resolver, tmp_project):
        flat_map = resolver.resolve_constants(str(tmp_project))
        value, was_resolved = resolver.resolve_or_fallback("MissingClass.FIELD", flat_map)
        assert value == "MissingClass.FIELD"
        assert was_resolved is False

    def test_missing_field_returns_raw_and_false(self, resolver, tmp_project):
        flat_map = resolver.resolve_constants(str(tmp_project))
        value, was_resolved = resolver.resolve_or_fallback("AppConstants.NONEXISTENT", flat_map)
        assert value == "AppConstants.NONEXISTENT"
        assert was_resolved is False

    def test_empty_flat_map_returns_raw_and_false(self, resolver):
        value, was_resolved = resolver.resolve_or_fallback("Anything.HERE", {})
        assert value == "Anything.HERE"
        assert was_resolved is False

    def test_unresolved_can_be_added_as_warning(self, resolver, tmp_project):
        """Demonstrates the intended caller pattern: flag unresolved refs as warnings."""
        flat_map = resolver.resolve_constants(str(tmp_project))
        errors: list[str] = []
        ref = "AppConstants.MISSING_FIELD"
        value, was_resolved = resolver.resolve_or_fallback(ref, flat_map)
        if not was_resolved:
            errors.append(f"Unresolved constant reference: {ref}")
        assert len(errors) == 1
        assert "Unresolved constant reference" in errors[0]
        assert ref in errors[0]


class TestParseConstantFile:
    def test_extracts_fields(self, resolver, tmp_path):
        f = tmp_path / "TestConstants.java"
        f.write_text(
            textwrap.dedent("""\
                public class TestConstants {
                    public static final String FOO = "bar";
                    public static final String BAZ = "qux";
                }
            """)
        )
        fields = resolver.parse_constant_file(str(f))
        assert fields == {"FOO": "bar", "BAZ": "qux"}

    def test_empty_string_value(self, resolver, tmp_path):
        f = tmp_path / "EmptyConstants.java"
        f.write_text('public static final String EMPTY = "";')
        fields = resolver.parse_constant_file(str(f))
        assert fields == {"EMPTY": ""}

    def test_nonexistent_file_returns_empty(self, resolver):
        fields = resolver.parse_constant_file("/nonexistent/path/Foo.java")
        assert fields == {}


class TestResolve:
    def test_resolve_existing(self, resolver, tmp_project):
        resolver.resolve_constants(str(tmp_project))
        assert resolver.resolve("AppConstants", "USERID") == "userId"

    def test_resolve_missing_class(self, resolver, tmp_project):
        resolver.resolve_constants(str(tmp_project))
        assert resolver.resolve("MissingClass", "USERID") is None

    def test_resolve_missing_field(self, resolver, tmp_project):
        resolver.resolve_constants(str(tmp_project))
        assert resolver.resolve("AppConstants", "NONEXISTENT") is None


class TestRealAppConstants:
    """Integration-style tests against the actual AppConstants.java in the workspace."""

    REAL_PROJECT = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",
        "candidate-association-service",
    )

    @pytest.fixture(autouse=True)
    def _skip_if_missing(self):
        if not os.path.isdir(self.REAL_PROJECT):
            pytest.skip("candidate-association-service not found in workspace")

    def test_resolves_real_constants(self, resolver):
        result = resolver.resolve_constants(self.REAL_PROJECT)
        assert result["AppConstants.USERID"] == "userId"
        assert result["AppConstants.JOBIDS"] == "jobIds"
        assert result["AppConstants.COLL_CAND_ASSOCIATED_ENTITIES"] == "CandidateAssociatedEntities"

    def test_resolve_method_with_real_data(self, resolver):
        resolver.resolve_constants(self.REAL_PROJECT)
        assert resolver.resolve("AppConstants", "USERID") == "userId"
        assert resolver.resolve("AppConstants", "JOBIDS") == "jobIds"
        assert resolver.resolve("AppConstants", "COLL_CAND_ASSOCIATED_ENTITIES") == "CandidateAssociatedEntities"

    def test_unqualified_lookup(self, resolver):
        result = resolver.resolve_constants(self.REAL_PROJECT)
        assert result["USERID"] == "userId"
        assert result["JOBIDS"] == "jobIds"
