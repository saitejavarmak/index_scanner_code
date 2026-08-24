"""Unit tests for the data models."""

import pytest

from index_scanner_mcp.models import (
    FieldUsage,
    IndexDefinition,
    IndexSource,
    IndexSuggestion,
    ScanResult,
)


# --- IndexSource tests ---


class TestIndexSource:
    def test_valid_annotation_source(self):
        src = IndexSource(file="Foo.java", line=10, source_type="annotation", annotation="@Indexed")
        assert src.file == "Foo.java"
        assert src.line == 10
        assert src.source_type == "annotation"
        assert src.annotation == "@Indexed"
        assert src.context == ""

    def test_valid_programmatic_source(self):
        src = IndexSource(file="Bar.java", line=5, source_type="programmatic")
        assert src.source_type == "programmatic"

    def test_valid_query_suggestion_source(self):
        src = IndexSource(file="Baz.java", line=1, source_type="query_suggestion")
        assert src.source_type == "query_suggestion"

    def test_empty_file_raises(self):
        with pytest.raises(ValueError, match="file must be non-empty"):
            IndexSource(file="", line=1, source_type="annotation")

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValueError, match="source_type must be one of"):
            IndexSource(file="X.java", line=1, source_type="unknown")


# --- IndexDefinition tests ---


class TestIndexDefinition:
    def test_valid_simple_index(self):
        idx = IndexDefinition(collection="users", fields={"email": 1})
        assert idx.collection == "users"
        assert idx.fields == {"email": 1}
        assert idx.unique is False
        assert idx.sparse is False
        assert idx.source is None

    def test_valid_compound_index(self):
        idx = IndexDefinition(
            collection="orders",
            fields={"userId": 1, "createdAt": -1},
            unique=True,
        )
        assert idx.fields == {"userId": 1, "createdAt": -1}
        assert idx.unique is True

    def test_valid_text_index(self):
        idx = IndexDefinition(
            collection="articles",
            fields={"content": "text"},
            index_type="text",
        )
        assert idx.fields == {"content": "text"}
        assert idx.index_type == "text"

    def test_valid_hashed_index(self):
        idx = IndexDefinition(
            collection="sessions",
            fields={"token": "hashed"},
            index_type="hashed",
        )
        assert idx.fields == {"token": "hashed"}

    def test_ttl_index(self):
        idx = IndexDefinition(
            collection="sessions",
            fields={"expiresAt": 1},
            expire_after_seconds=3600,
        )
        assert idx.expire_after_seconds == 3600

    def test_empty_collection_raises(self):
        with pytest.raises(ValueError, match="collection must be non-empty"):
            IndexDefinition(collection="", fields={"a": 1})

    def test_empty_fields_raises(self):
        with pytest.raises(ValueError, match="fields must have at least one entry"):
            IndexDefinition(collection="users", fields={})

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="Invalid direction 2"):
            IndexDefinition(collection="users", fields={"email": 2})

    def test_invalid_string_direction_raises(self):
        with pytest.raises(ValueError, match="Invalid direction 'bogus'"):
            IndexDefinition(collection="users", fields={"email": "bogus"})

    def test_with_source(self):
        src = IndexSource(file="Entity.java", line=42, source_type="annotation")
        idx = IndexDefinition(collection="items", fields={"name": 1}, source=src)
        assert idx.source.file == "Entity.java"
        assert idx.source.line == 42


# --- IndexSuggestion tests ---


class TestIndexSuggestion:
    def test_valid_suggestion(self):
        s = IndexSuggestion(
            collection="orders",
            fields={"status": 1},
            priority="high",
            rationale="Frequently filtered",
            operations=["find"],
            reference_count=15,
            sample_locations=["OrderRepo.java:30"],
        )
        assert s.collection == "orders"
        assert s.priority == "high"
        assert s.reference_count == 15

    def test_empty_collection_raises(self):
        with pytest.raises(ValueError, match="collection must be non-empty"):
            IndexSuggestion(collection="", fields={"a": 1}, priority="low", rationale="x")

    def test_empty_fields_raises(self):
        with pytest.raises(ValueError, match="fields must have at least one entry"):
            IndexSuggestion(collection="c", fields={}, priority="low", rationale="x")

    def test_invalid_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be one of"):
            IndexSuggestion(collection="c", fields={"a": 1}, priority="critical", rationale="x")

    def test_defaults(self):
        s = IndexSuggestion(collection="c", fields={"a": 1}, priority="medium", rationale="r")
        assert s.operations == []
        assert s.reference_count == 0
        assert s.sample_locations == []


# --- ScanResult tests ---


class TestScanResult:
    def test_valid_scan_result(self):
        r = ScanResult(project_path="/home/user/project")
        assert r.project_path == "/home/user/project"
        assert r.indexes == []
        assert r.suggestions == []
        assert r.files_scanned == 0
        assert r.errors == []

    def test_empty_project_path_raises(self):
        with pytest.raises(ValueError, match="project_path must be non-empty"):
            ScanResult(project_path="")


# --- FieldUsage tests ---


class TestFieldUsage:
    def test_valid_field_usage(self):
        fu = FieldUsage(
            field="userId",
            collection="orders",
            usage_type="filter",
            operation="find",
            file="OrderRepo.java",
            line=55,
        )
        assert fu.field == "userId"
        assert fu.usage_type == "filter"

    def test_empty_field_raises(self):
        with pytest.raises(ValueError, match="field must be non-empty"):
            FieldUsage(field="", collection="c", usage_type="filter", operation="find", file="X.java", line=1)

    def test_empty_collection_raises(self):
        with pytest.raises(ValueError, match="collection must be non-empty"):
            FieldUsage(field="f", collection="", usage_type="filter", operation="find", file="X.java", line=1)

    def test_invalid_usage_type_raises(self):
        with pytest.raises(ValueError, match="usage_type must be one of"):
            FieldUsage(field="f", collection="c", usage_type="aggregate", operation="find", file="X.java", line=1)

    def test_sort_usage(self):
        fu = FieldUsage(field="date", collection="events", usage_type="sort", operation="find", file="E.java", line=1)
        assert fu.usage_type == "sort"

    def test_projection_usage(self):
        fu = FieldUsage(field="name", collection="users", usage_type="projection", operation="find", file="U.java", line=1)
        assert fu.usage_type == "projection"
