"""Report generator for producing structured JSON-serializable reports from scan results."""

from __future__ import annotations

from index_scanner_mcp.models import IndexDefinition, IndexSuggestion, ScanResult


class ReportGenerator:
    """Generates structured reports from scan results."""

    def generate_report(self, scan_result: ScanResult) -> dict:
        """Generate a structured JSON-serializable report from a ScanResult.

        The report includes:
        - project_path: the scanned project directory
        - summary: aggregate statistics
        - indexes: detailed list of discovered index definitions with source traceability
        - suggestions: detailed list of suggested indexes from query analysis
        """
        indexes = [self._format_index(idx) for idx in scan_result.indexes]
        suggestions = [self._format_suggestion(s) for s in scan_result.suggestions]

        index_types: dict[str, int] = {}
        for idx in scan_result.indexes:
            index_types[idx.index_type] = index_types.get(idx.index_type, 0) + 1

        return {
            "project_path": scan_result.project_path,
            "database_names": scan_result.database_names,
            "summary": {
                "total_indexes": len(scan_result.indexes),
                "total_suggestions": len(scan_result.suggestions),
                "files_scanned": scan_result.files_scanned,
                "constants_resolved": scan_result.constants_resolved,
                "errors_count": len(scan_result.errors),
                "by_type": index_types,
            },
            "indexes": indexes,
            "suggestions": suggestions,
        }

    def _format_index(self, idx: IndexDefinition) -> dict:
        """Format a single IndexDefinition into a report dict."""
        source = {}
        if idx.source is not None:
            source = {
                "file": idx.source.file,
                "line": idx.source.line,
                "source_type": idx.source.source_type,
                "annotation": idx.source.annotation,
            }

        return {
            "collection": idx.collection,
            "database": idx.database,
            "fields": idx.fields,
            "options": {
                "unique": idx.unique,
                "sparse": idx.sparse,
                "expire_after_seconds": idx.expire_after_seconds,
                "index_type": idx.index_type,
            },
            "source": source,
        }

    def _format_suggestion(self, suggestion: IndexSuggestion) -> dict:
        """Format a single IndexSuggestion into a report dict."""
        return {
            "collection": suggestion.collection,
            "database": suggestion.database,
            "fields": suggestion.fields,
            "priority": suggestion.priority,
            "rationale": suggestion.rationale,
            "operations": suggestion.operations,
            "reference_count": suggestion.reference_count,
            "sample_locations": suggestion.sample_locations,
        }
