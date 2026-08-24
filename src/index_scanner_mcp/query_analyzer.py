"""Query Pattern Analyzer - analyzes MongoDB query patterns in Java code to suggest indexes."""

from __future__ import annotations

import re
from pathlib import Path

from index_scanner_mcp.constants import (
    COMPILED_COLLECTION_PATTERNS,
    COMPILED_OPERATION_PATTERNS_JAVA,
    COMPILED_QUERY_FIELD_PATTERNS_JAVA,
    IGNORE_FIELDS,
)
from index_scanner_mcp.models import FieldUsage, IndexSuggestion


class QueryPatternAnalyzer:
    """Analyzes MongoDB query patterns in Java code to suggest indexes
    based on how the database is queried.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_file(
        self, filepath: str, constant_map: dict[str, str]
    ) -> list[IndexSuggestion]:
        """Analyze a Java file for query patterns and suggest indexes.

        Returns an empty list if the file cannot be read or contains no
        query patterns.
        """
        try:
            with open(filepath, encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            return []

        field_usages = self.extract_query_fields(content, constant_map, filepath)
        if not field_usages:
            return []

        return self.generate_suggestions(field_usages)

    def extract_query_fields(
        self,
        content: str,
        constant_map: dict[str, str],
        filepath: str = "",
    ) -> list[FieldUsage]:
        """Extract field names used in find/update/aggregate queries.

        Detects ``BasicDBObject``, ``Document``, ``Filters.*`` query
        constructions and resolves ``AppConstants.FIELD`` references
        using *constant_map*.
        """
        lines = content.splitlines(keepends=True)
        if not lines:
            return []

        # Pre-scan: detect collections and operations in the file
        collections_in_file = self._detect_collections(content, constant_map)
        operations = self._detect_operations(content)

        field_usages: list[FieldUsage] = []

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("*"):
                continue

            for pattern, field_type in COMPILED_QUERY_FIELD_PATTERNS_JAVA:
                for match in pattern.finditer(line):
                    field_name = match.group(1) if match.lastindex else None
                    if not field_name:
                        continue

                    # Skip MongoDB operators, markers, and existing index refs
                    if field_name.startswith("$") or field_name in IGNORE_FIELDS:
                        continue
                    if field_type in (
                        "agg_match_marker",
                        "agg_sort_marker",
                        "agg_group_marker",
                        "existing_index",
                    ):
                        continue

                    # Resolve constant references and classify usage type
                    if field_type == "filter_constant":
                        resolved = self._resolve_constant(field_name, constant_map)
                        usage_type = "filter_equality"
                    elif field_type == "filter_equality":
                        resolved = field_name
                        usage_type = "filter_equality"
                    elif field_type == "filter_range":
                        resolved = field_name
                        usage_type = "filter_range"
                    elif field_type == "sort":
                        resolved = field_name
                        usage_type = "sort"
                    else:
                        resolved = field_name
                        # Default filters (BasicDBObject, Document, append, put)
                        # are typically equality unless we can detect otherwise
                        usage_type = "filter_equality"

                    # Find nearest operation context
                    operation = self._find_nearest_operation(operations, line_num)

                    # Find nearest collection
                    char_pos = sum(len(lines[i]) for i in range(line_num - 1))
                    collection = self._find_nearest_collection(
                        collections_in_file, char_pos
                    )

                    field_usages.append(
                        FieldUsage(
                            field=resolved,
                            collection=collection,
                            usage_type=usage_type,
                            operation=operation,
                            file=filepath,
                            line=line_num,
                        )
                    )

        return field_usages

    def generate_suggestions(
        self, field_usages: list[FieldUsage]
    ) -> list[IndexSuggestion]:
        """Group field usages and suggest single/compound indexes.

        Filters out:
        - Collections with backup/archive/temp/log patterns in their name
        - Single-field suggestions that are subsets of compound suggestions
        - Suggestions from collections with very few references (< 2 unique files)

        Assigns priority levels based on reference count:
        - high: ≥5 references or sort fields
        - medium: ≥2 references
        - low: 1 reference
        """
        # Pre-filter: remove usages from non-indexable collections
        field_usages = [
            fu for fu in field_usages
            if not self._is_non_indexable_collection(fu.collection)
        ]

        if not field_usages:
            return []
        # Group by (collection, field) for single-field suggestions
        field_usage_map: dict[tuple[str, str], list[FieldUsage]] = {}
        for fu in field_usages:
            key = (fu.collection, fu.field)
            field_usage_map.setdefault(key, []).append(fu)

        # Group by (collection, file, operation, line_bucket) for compound suggestions
        compound_groups: dict[tuple[str, str, str, int], list[FieldUsage]] = {}
        for fu in field_usages:
            bucket_line = (fu.line // 10) * 10
            key = (fu.collection, fu.file, fu.operation, bucket_line)
            compound_groups.setdefault(key, []).append(fu)

        suggestions: list[IndexSuggestion] = []
        seen: set[tuple[str, str | tuple[str, ...]]] = set()

        # Single field index suggestions
        for (collection, field), usages in field_usage_map.items():
            if field.startswith("$") or field in IGNORE_FIELDS:
                continue

            operations = sorted({u.operation for u in usages})
            total_refs = len(usages)

            priority = self._compute_priority(total_refs, usages)

            sample_locations = [
                f"{Path(u.file).name}:{u.line}" for u in usages[:3]
            ]

            rationale = (
                f"Field '{field}' used {total_refs} time(s) "
                f"in operations: {', '.join(operations)} "
                f"across {len({u.file for u in usages})} file(s)"
            )

            suggestion_key: tuple[str, str | tuple[str, ...]] = (collection, field)
            if suggestion_key not in seen:
                seen.add(suggestion_key)
                suggestions.append(
                    IndexSuggestion(
                        collection=collection,
                        fields={field: 1},
                        priority=priority,
                        rationale=rationale,
                        operations=operations,
                        reference_count=total_refs,
                        sample_locations=sample_locations,
                    )
                )

        # Compound index suggestions — apply ESR (Equality, Sort, Range) ordering
        for (collection, file, operation, _bucket), usages_list in compound_groups.items():
            unique_fields = list(
                dict.fromkeys(
                    fu.field
                    for fu in usages_list
                    if not fu.field.startswith("$") and fu.field not in IGNORE_FIELDS
                )
            )
            if len(unique_fields) < 2:
                continue

            # Classify fields by ESR category
            equality_fields = list(dict.fromkeys(
                fu.field for fu in usages_list
                if fu.usage_type in ("filter_equality", "filter")
                and fu.field not in IGNORE_FIELDS
                and not fu.field.startswith("$")
            ))
            sort_fields = list(dict.fromkeys(
                fu.field for fu in usages_list
                if fu.usage_type == "sort"
                and fu.field not in IGNORE_FIELDS
                and not fu.field.startswith("$")
            ))
            range_fields = list(dict.fromkeys(
                fu.field for fu in usages_list
                if fu.usage_type == "filter_range"
                and fu.field not in IGNORE_FIELDS
                and not fu.field.startswith("$")
            ))

            # Remove duplicates across categories (a field in equality shouldn't
            # also appear in sort/range for the same compound index)
            sort_fields = [f for f in sort_fields if f not in equality_fields]
            range_fields = [f for f in range_fields
                           if f not in equality_fields and f not in sort_fields]

            # ESR order: Equality first, then Sort, then Range
            ordered = list(dict.fromkeys(equality_fields + sort_fields + range_fields))
            if len(ordered) < 2:
                ordered = list(dict.fromkeys(unique_fields))
            if len(ordered) < 2:
                continue

            compound_key: tuple[str, str | tuple[str, ...]] = (
                collection,
                tuple(sorted(ordered[:5])),
            )
            if compound_key in seen:
                continue
            seen.add(compound_key)

            index_spec = {f: 1 for f in ordered[:5]}
            sample_locations = [
                f"{Path(fu.file).name}:{fu.line}" for fu in usages_list[:3]
            ]

            # Build ESR rationale
            esr_parts = []
            if equality_fields:
                esr_parts.append(f"E={equality_fields}")
            if sort_fields:
                esr_parts.append(f"S={sort_fields}")
            if range_fields:
                esr_parts.append(f"R={range_fields}")
            esr_label = ", ".join(esr_parts) if esr_parts else ""

            suggestions.append(
                IndexSuggestion(
                    collection=collection,
                    fields=index_spec,
                    priority="medium" if len(ordered) <= 3 else "high",
                    rationale=(
                        f"ESR compound index ({esr_label}) — "
                        f"fields queried together in {operation} "
                        f"at {Path(file).name}"
                    ),
                    operations=[operation],
                    reference_count=len(usages_list),
                    sample_locations=sample_locations,
                )
            )

        # Sort by priority then by reference count descending
        priority_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(
            key=lambda s: (priority_order.get(s.priority, 3), -s.reference_count)
        )

        # Filter out suggestions where collection is "unknown" — not actionable
        suggestions = [s for s in suggestions if s.collection != "unknown"]

        # Remove single-field suggestions that are subsets of compound suggestions
        # (if {userId:1, source:1} is suggested, don't also suggest {userId:1} alone)
        compound_fields_by_collection: dict[str, set[str]] = {}
        for s in suggestions:
            if len(s.fields) >= 2:
                compound_fields_by_collection.setdefault(s.collection, set()).update(s.fields.keys())

        suggestions = [
            s for s in suggestions
            if not (
                len(s.fields) == 1
                and s.priority == "low"
                and s.collection in compound_fields_by_collection
                and list(s.fields.keys())[0] in compound_fields_by_collection[s.collection]
            )
        ]

        return suggestions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_constant(
        constant_name: str, constant_map: dict[str, str]
    ) -> str:
        """Resolve an AppConstants field name through the constant map.

        Tries ``AppConstants.<name>`` first, then the bare name.
        Falls back to the raw constant name if unresolved.
        """
        qualified = f"AppConstants.{constant_name}"
        if qualified in constant_map:
            return constant_map[qualified]
        if constant_name in constant_map:
            return constant_map[constant_name]
        return constant_name

    @staticmethod
    def _detect_collections(
        content: str, constant_map: dict[str, str]
    ) -> list[dict]:
        """Detect collection references in the file content.

        Also detects collection names passed as method parameters or
        stored in class fields (common in utility/DAO classes).
        Handles entity_class type by converting CamelCase class names
        to likely collection names (lowercase).
        """
        import re as _re

        collections: list[dict] = []
        for pattern, ptype in COMPILED_COLLECTION_PATTERNS:
            for m in pattern.finditer(content):
                coll_name = m.group(1)
                if ptype == "constant":
                    qualified = f"AppConstants.{coll_name}"
                    if qualified in constant_map:
                        coll_name = constant_map[qualified]
                    elif coll_name in constant_map:
                        coll_name = constant_map[coll_name]
                elif ptype == "entity_class":
                    # Convert CamelCase entity class to collection name
                    # e.g., "CandidateProfile" -> "candidateProfile" or "candidate_profile"
                    # MongoDB default is lowercase class name
                    if coll_name[0].isupper():
                        coll_name = coll_name[0].lower() + coll_name[1:]
                # Skip common false positives from generic patterns
                if coll_name in ('db', 'this', 'self', 'null', 'true', 'false',
                                 'class', 'new', 'return', 'void', 'public',
                                 'private', 'static', 'final', 'String',
                                 'client', 'connection', 'session', 'template',
                                 'factory', 'builder', 'config', 'options',
                                 'result', 'cursor', 'iterator', 'logger',
                                 'log', 'out', 'err', 'System', 'Object',
                                 'Query', 'Criteria', 'Update', 'Document',
                                 'List', 'Map', 'Set', 'Collection', 'Optional'):
                    continue
                collections.append(
                    {"name": coll_name, "type": ptype, "pos": m.start()}
                )

        # Also detect collection names from method calls with string args
        # e.g. mongoTemplate.find(query, "collectionName") or
        # getMongoTemplate().getCollection(collName) where collName is a param
        # Look for string literals passed to mongo-related method calls
        for m in _re.finditer(
            r'(?:mongoTemplate|mongoOperations|template|mongo)\s*\.\s*\w+\s*\([^)]*["\']([a-z]\w+)["\']',
            content, _re.IGNORECASE
        ):
            coll_name = m.group(1)
            if coll_name not in ('admin', 'local', 'config', 'test') and len(coll_name) > 2:
                collections.append(
                    {"name": coll_name, "type": "direct", "pos": m.start()}
                )

        return collections

    @staticmethod
    def _detect_operations(content: str) -> list[dict]:
        """Detect DB operations and their locations in the file."""
        operations: list[dict] = []
        for pattern, op_type in COMPILED_OPERATION_PATTERNS_JAVA:
            for m in pattern.finditer(content):
                line_num = content[: m.start()].count("\n") + 1
                operations.append(
                    {"type": op_type, "line": line_num, "pos": m.start()}
                )
        return operations

    @staticmethod
    def _find_nearest_operation(operations: list[dict], line_num: int) -> str:
        """Find the nearest preceding operation within 50 lines."""
        nearest_op = None
        for op in operations:
            if op["line"] <= line_num and line_num - op["line"] < 50:
                if nearest_op is None or op["line"] > nearest_op["line"]:
                    nearest_op = op
        return nearest_op["type"] if nearest_op else "unknown"

    @staticmethod
    def _find_nearest_collection(
        collections: list[dict], char_pos: int
    ) -> str:
        """Find the nearest preceding collection reference."""
        nearest_coll = None
        for coll in collections:
            if coll["pos"] <= char_pos:
                if nearest_coll is None or coll["pos"] > nearest_coll["pos"]:
                    nearest_coll = coll
        return nearest_coll["name"] if nearest_coll else "unknown"

    @staticmethod
    def _compute_priority(total_refs: int, usages: list[FieldUsage]) -> str:
        """Compute priority based on reference count and usage types."""
        has_sort = any(u.usage_type == "sort" for u in usages)
        has_range = any(u.usage_type == "filter_range" for u in usages)
        if total_refs >= 5 or has_sort:
            return "high"
        if total_refs >= 2 or has_range:
            return "medium"
        return "low"

    @staticmethod
    def _is_non_indexable_collection(collection: str) -> bool:
        """Return True if the collection name suggests it shouldn't be indexed.

        Filters out:
        - Backup/archive/temp collections
        - Log/audit collections (typically append-only, rarely queried by field)
        - Unknown collections
        - Collections with patterns suggesting utility/non-production data
        """
        if not collection or collection == "unknown":
            return True

        name_lower = collection.lower()

        # Backup/restore/archive patterns
        skip_patterns = (
            'backup', 'bkp', 'restore', 'archive', 'archived',
            'temp', 'tmp', 'temporary',
            'log', 'logs', 'audit', 'auditlog',
            'deleted', 'trash', 'recyclebin',
            'dump', 'export', 'import',
            'migration', 'migrate',
            'seed', 'sample', 'test', 'mock',
            'history',  # often append-only, huge, not indexed
        )

        for pattern in skip_patterns:
            if pattern in name_lower:
                return True

        # Collections ending with common non-production suffixes
        skip_suffixes = ('_bkp', '_backup', '_old', '_archive', '_temp', '_tmp', '_deleted')
        for suffix in skip_suffixes:
            if name_lower.endswith(suffix):
                return True

        return False
