"""Unit tests for the ReportGenerator class."""

import pytest

from index_scanner_mcp.models import (
    IndexDefinition,
    IndexSource,
    IndexSuggestion,
    ScanResult,
)
from index_scanner_mcp.report_generator import ReportGenerator


@pytest.fixture
def generator() -> ReportGenerator:
    return ReportGenerator()


def _make_source(
    file: str = "Entity.java",
    line: int = 10,
    source_type: str = "annotation",
    annotation: str | None = "@Indexed",
) -> IndexSource:
    return IndexSource(file=file, line=line, source_type=source_type, annotation=annotation)


def _make_index(
    collection: str = "users",
    fields: dict | None = None,
    unique: bool = False,
    sparse: bool = False,
    expire_after_seconds: int | None = None,
    index_type: str = "standard",
    source: IndexSource | None = None,
) -> IndexDefinition:
    return IndexDefinition(
        collection=collection,
        fields=fields or {"email": 1},
        unique=unique,
        sparse=sparse,
        expire_after_seconds=expire_after_seconds,
        index_type=index_type,
        source=source or _make_source(),
    )


def _make_suggestion(
    collection: str = "orders",
    fields: dict | None = None,
    priority: str = "high",
    rationale: str = "Frequently queried field",
    operations: list[str] | None = None,
    reference_count: int = 5,
    sample_locations: list[str] | None = None,
) -> IndexSuggestion:
    return IndexSuggestion(
        collection=collection,
        fields=fields or {"status": 1},
        priority=priority,
        rationale=rationale,
        operations=operations or ["find"],
        reference_count=reference_count,
        sample_locations=sample_locations or ["OrderRepo.java:42"],
    )


def _make_scan_result(
    project_path: str = "/home/user/project",
    indexes: list[IndexDefinition] | None = None,
    suggestions: list[IndexSuggestion] | None = None,
    constants_resolved: int = 3,
    files_scanned: int = 10,
    errors: list[str] | None = None,
) -> ScanResult:
    return ScanResult(
        project_path=project_path,
        indexes=indexes or [],
        suggestions=suggestions or [],
        constants_resolved=constants_resolved,
        files_scanned=files_scanned,
        errors=errors or [],
    )


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


class TestReportStructure:
    def test_report_has_required_top_level_keys(self, generator):
        result = _make_scan_result()
        report = generator.generate_report(result)
        assert "project_path" in report
        assert "summary" in report
        assert "indexes" in report
        assert "suggestions" in report

    def test_project_path_matches(self, generator):
        result = _make_scan_result(project_path="/my/project")
        report = generator.generate_report(result)
        assert report["project_path"] == "/my/project"


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


class TestSummaryStatistics:
    def test_total_indexes(self, generator):
        indexes = [_make_index(), _make_index(collection="orders", fields={"status": 1})]
        result = _make_scan_result(indexes=indexes)
        report = generator.generate_report(result)
        assert report["summary"]["total_indexes"] == 2

    def test_total_suggestions(self, generator):
        suggestions = [_make_suggestion(), _make_suggestion(collection="products")]
        result = _make_scan_result(suggestions=suggestions)
        report = generator.generate_report(result)
        assert report["summary"]["total_suggestions"] == 2

    def test_files_scanned(self, generator):
        result = _make_scan_result(files_scanned=25)
        report = generator.generate_report(result)
        assert report["summary"]["files_scanned"] == 25

    def test_constants_resolved(self, generator):
        result = _make_scan_result(constants_resolved=7)
        report = generator.generate_report(result)
        assert report["summary"]["constants_resolved"] == 7

    def test_errors_count(self, generator):
        result = _make_scan_result(errors=["err1", "err2", "err3"])
        report = generator.generate_report(result)
        assert report["summary"]["errors_count"] == 3

    def test_by_type_counts(self, generator):
        indexes = [
            _make_index(index_type="standard"),
            _make_index(collection="a", fields={"f": 1}, index_type="standard"),
            _make_index(collection="b", fields={"g": "text"}, index_type="text"),
        ]
        result = _make_scan_result(indexes=indexes)
        report = generator.generate_report(result)
        assert report["summary"]["by_type"] == {"standard": 2, "text": 1}

    def test_empty_scan_result(self, generator):
        result = _make_scan_result(files_scanned=0, constants_resolved=0)
        report = generator.generate_report(result)
        assert report["summary"]["total_indexes"] == 0
        assert report["summary"]["total_suggestions"] == 0
        assert report["summary"]["errors_count"] == 0
        assert report["summary"]["by_type"] == {}


# ---------------------------------------------------------------------------
# Index entries
# ---------------------------------------------------------------------------


class TestIndexEntries:
    def test_index_has_collection(self, generator):
        result = _make_scan_result(indexes=[_make_index(collection="users")])
        report = generator.generate_report(result)
        assert report["indexes"][0]["collection"] == "users"

    def test_index_has_fields(self, generator):
        result = _make_scan_result(indexes=[_make_index(fields={"email": 1, "name": -1})])
        report = generator.generate_report(result)
        assert report["indexes"][0]["fields"] == {"email": 1, "name": -1}

    def test_index_has_options(self, generator):
        idx = _make_index(unique=True, sparse=True, expire_after_seconds=3600)
        result = _make_scan_result(indexes=[idx])
        report = generator.generate_report(result)
        opts = report["indexes"][0]["options"]
        assert opts["unique"] is True
        assert opts["sparse"] is True
        assert opts["expire_after_seconds"] == 3600

    def test_index_source_traceability(self, generator):
        src = _make_source(file="User.java", line=42, source_type="annotation", annotation="@CompoundIndex")
        idx = _make_index(source=src)
        result = _make_scan_result(indexes=[idx])
        report = generator.generate_report(result)
        source = report["indexes"][0]["source"]
        assert source["file"] == "User.java"
        assert source["line"] == 42
        assert source["source_type"] == "annotation"
        assert source["annotation"] == "@CompoundIndex"

    def test_index_without_source(self, generator):
        idx = IndexDefinition(collection="test", fields={"a": 1})
        result = _make_scan_result(indexes=[idx])
        report = generator.generate_report(result)
        assert report["indexes"][0]["source"] == {}

    def test_multiple_indexes_preserved(self, generator):
        indexes = [
            _make_index(collection="a", fields={"x": 1}),
            _make_index(collection="b", fields={"y": -1}),
        ]
        result = _make_scan_result(indexes=indexes)
        report = generator.generate_report(result)
        assert len(report["indexes"]) == 2
        collections = {i["collection"] for i in report["indexes"]}
        assert collections == {"a", "b"}


# ---------------------------------------------------------------------------
# Suggestion entries
# ---------------------------------------------------------------------------


class TestSuggestionEntries:
    def test_suggestion_has_collection(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(collection="orders")])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["collection"] == "orders"

    def test_suggestion_has_fields(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(fields={"status": 1, "date": -1})])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["fields"] == {"status": 1, "date": -1}

    def test_suggestion_has_priority(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(priority="medium")])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["priority"] == "medium"

    def test_suggestion_has_rationale(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(rationale="Hot path query")])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["rationale"] == "Hot path query"

    def test_suggestion_has_operations(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(operations=["find", "aggregate"])])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["operations"] == ["find", "aggregate"]

    def test_suggestion_has_reference_count(self, generator):
        result = _make_scan_result(suggestions=[_make_suggestion(reference_count=12)])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["reference_count"] == 12

    def test_suggestion_has_sample_locations(self, generator):
        locs = ["Repo.java:10", "Service.java:20"]
        result = _make_scan_result(suggestions=[_make_suggestion(sample_locations=locs)])
        report = generator.generate_report(result)
        assert report["suggestions"][0]["sample_locations"] == locs
