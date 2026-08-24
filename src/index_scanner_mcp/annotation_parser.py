"""Annotation Parser - extracts MongoDB index definitions from Spring Data annotations."""

from __future__ import annotations

import re

from index_scanner_mcp.constants import (
    COMPOUND_DEF_FIELD_PATTERN,
    COMPOUND_INDEX_DEF_PATTERN,
    COMPOUND_INDEX_PATTERN,
    COMPOUND_INDEXES_PATTERN,
    DOCUMENT_ANNOTATION_PATTERN,
    GEOSPATIAL_INDEXED_PATTERN,
    HASH_INDEXED_PATTERN,
    INDEXED_FIELD_PATTERN,
    JAVA_FIELD_DECLARATION_PATTERN,
    TEXT_INDEXED_PATTERN,
    WILDCARD_INDEXED_PATTERN,
)
from index_scanner_mcp.models import IndexDefinition, IndexSource


class AnnotationParser:
    """Extracts index definitions from Spring Data MongoDB annotations on
    Java entity classes.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(
        self, filepath: str, constant_map: dict[str, str]
    ) -> list[IndexDefinition]:
        """Parse a Java file for MongoDB index annotations.

        Returns an empty list when the file has no ``@Document`` annotation
        or cannot be read.
        """
        try:
            with open(filepath, encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            return []

        collection = self.parse_document_annotation(content)
        if collection is None:
            return []

        indexes: list[IndexDefinition] = []

        # Class-level compound indexes
        indexes.extend(
            self._parse_compound_indexes(content, collection, filepath, constant_map)
        )

        # Field-level @Indexed annotations
        indexes.extend(
            self._parse_indexed_fields(content, collection, filepath, constant_map)
        )

        # Special index annotations: @TextIndexed, @GeoSpatialIndexed, etc.
        indexes.extend(
            self._parse_special_indexed_fields(content, collection, filepath, constant_map)
        )

        return indexes

    def parse_document_annotation(self, content: str) -> str | None:
        """Extract collection name from ``@Document`` annotation.

        If @Document has a collection attribute, uses that.
        If @Document exists but without collection name, derives it from the class name.
        Returns ``None`` if no ``@Document`` annotation is found.
        """
        match = DOCUMENT_ANNOTATION_PATTERN.search(content)
        if match:
            return match.group(1)

        # Check for @Document without collection name — derive from class name
        bare_doc = re.search(r'@Document\b', content)
        if bare_doc:
            # Find the class declaration after @Document
            class_match = re.search(
                r'@Document\b[^{]*?(?:public\s+)?class\s+(\w+)',
                content, re.DOTALL
            )
            if class_match:
                class_name = class_match.group(1)
                # Spring Data convention: lowercase first letter
                return class_name[0].lower() + class_name[1:] if class_name else None

        return None

    def parse_compound_index(self, annotation_text: str) -> dict[str, int]:
        """Parse ``@CompoundIndex`` *def* attribute into a field→direction map.

        *annotation_text* is the raw ``def`` value, e.g.
        ``"{'email': 1, 'tenantId': -1}"``.

        Returns an empty dict if the text cannot be parsed.
        """
        fields: dict[str, int] = {}
        for match in COMPOUND_DEF_FIELD_PATTERN.finditer(annotation_text):
            field_name = match.group(1)
            direction = int(match.group(2))
            fields[field_name] = direction
        return fields

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_line_number(self, content: str, pos: int) -> int:
        """Return the 1-based line number for a character position."""
        return content[:pos].count("\n") + 1

    def _resolve_field_name(
        self, raw_name: str, constant_map: dict[str, str]
    ) -> str:
        """Resolve a field name through the constant map.

        If *raw_name* is found in *constant_map*, the resolved value is
        returned.  Otherwise the raw name is returned unchanged.
        """
        return constant_map.get(raw_name, raw_name)

    def _extract_boolean_attr(self, text: str, attr: str) -> bool:
        """Check whether *attr* ``= true`` appears in *text*."""
        pattern = re.compile(rf"{attr}\s*=\s*true", re.IGNORECASE)
        return bool(pattern.search(text))

    def _extract_int_attr(self, text: str, attr: str) -> int | None:
        """Extract an integer attribute value from annotation text."""
        pattern = re.compile(rf"{attr}\s*=\s*(\d+)")
        match = pattern.search(text)
        if match:
            return int(match.group(1))
        return None

    def _extract_name_attr(self, text: str) -> str | None:
        """Extract the ``name`` attribute from annotation text."""
        pattern = re.compile(r"""name\s*=\s*["']([^"']+)["']""")
        match = pattern.search(text)
        if match:
            return match.group(1)
        return None

    # ------------------------------------------------------------------
    # Compound index parsing
    # ------------------------------------------------------------------

    def _parse_compound_indexes(
        self,
        content: str,
        collection: str,
        filepath: str,
        constant_map: dict[str, str],
    ) -> list[IndexDefinition]:
        """Parse class-level ``@CompoundIndex`` and ``@CompoundIndexes``."""
        indexes: list[IndexDefinition] = []

        # First, handle @CompoundIndexes({ ... }) wrapper
        for wrapper_match in COMPOUND_INDEXES_PATTERN.finditer(content):
            wrapper_body = wrapper_match.group(1)
            wrapper_start = wrapper_match.start()
            for ci_match in COMPOUND_INDEX_PATTERN.finditer(wrapper_body):
                idx = self._build_compound_index(
                    ci_match.group(1),
                    collection,
                    filepath,
                    self._get_line_number(content, wrapper_start + ci_match.start()),
                    constant_map,
                )
                if idx is not None:
                    indexes.append(idx)

        # Then, handle standalone @CompoundIndex (not inside @CompoundIndexes)
        # We need to avoid double-counting those already inside @CompoundIndexes
        compound_indexes_spans: list[tuple[int, int]] = [
            (m.start(), m.end()) for m in COMPOUND_INDEXES_PATTERN.finditer(content)
        ]

        for ci_match in COMPOUND_INDEX_PATTERN.finditer(content):
            pos = ci_match.start()
            # Skip if this match is inside a @CompoundIndexes wrapper
            inside_wrapper = any(
                start <= pos < end for start, end in compound_indexes_spans
            )
            if inside_wrapper:
                continue
            idx = self._build_compound_index(
                ci_match.group(1),
                collection,
                filepath,
                self._get_line_number(content, pos),
                constant_map,
            )
            if idx is not None:
                indexes.append(idx)

        return indexes

    def _build_compound_index(
        self,
        annotation_body: str,
        collection: str,
        filepath: str,
        line: int,
        constant_map: dict[str, str],
    ) -> IndexDefinition | None:
        """Build an IndexDefinition from a ``@CompoundIndex`` annotation body."""
        def_str = self._extract_def_attribute(annotation_body)
        if def_str is None:
            return None
        fields = self.parse_compound_index(def_str)
        if not fields:
            return None

        # Resolve constant references in field names
        resolved_fields: dict[str, int] = {}
        for fname, direction in fields.items():
            resolved_fields[self._resolve_field_name(fname, constant_map)] = direction

        name = self._extract_name_attr(annotation_body)
        unique = self._extract_boolean_attr(annotation_body, "unique")

        return IndexDefinition(
            collection=collection,
            fields=resolved_fields,
            name=name,
            unique=unique,
            source=IndexSource(
                file=filepath,
                line=line,
                source_type="annotation",
                annotation="@CompoundIndex",
            ),
        )
    @staticmethod
    def _extract_def_attribute(annotation_body: str) -> str | None:
        """Extract the ``def`` attribute value from a ``@CompoundIndex`` body.

        Handles the case where the def value is wrapped in double quotes but
        contains single quotes internally, e.g.::

            def = "{'email': 1, 'tenantId': -1}"

        The standard ``COMPOUND_INDEX_DEF_PATTERN`` regex fails here because
        ``[^"']+`` stops at the first inner single quote.
        """
        # Find 'def' followed by '=' and an opening quote
        pattern = re.compile(r"""def\s*=\s*(["'])""")
        m = pattern.search(annotation_body)
        if not m:
            return None
        quote_char = m.group(1)
        start = m.end()  # position right after the opening quote
        # Find the matching closing quote (same type)
        end = annotation_body.find(quote_char, start)
        if end == -1:
            return None
        return annotation_body[start:end]


    # ------------------------------------------------------------------
    # Field-level @Indexed parsing
    # ------------------------------------------------------------------

    def _parse_indexed_fields(
        self,
        content: str,
        collection: str,
        filepath: str,
        constant_map: dict[str, str],
    ) -> list[IndexDefinition]:
        """Parse ``@Indexed`` annotations and the field declarations that follow."""
        indexes: list[IndexDefinition] = []

        for match in INDEXED_FIELD_PATTERN.finditer(content):
            annotation_opts = match.group(1) or ""
            annotation_end = match.end()
            line = self._get_line_number(content, match.start())

            # Find the next field declaration after the annotation
            field_name = self._find_next_field_name(content, annotation_end)
            if field_name is None:
                continue

            resolved_name = self._resolve_field_name(field_name, constant_map)
            unique = self._extract_boolean_attr(annotation_opts, "unique")
            sparse = self._extract_boolean_attr(annotation_opts, "sparse")
            ttl = self._extract_int_attr(annotation_opts, "expireAfterSeconds")

            indexes.append(
                IndexDefinition(
                    collection=collection,
                    fields={resolved_name: 1},
                    unique=unique,
                    sparse=sparse,
                    expire_after_seconds=ttl,
                    source=IndexSource(
                        file=filepath,
                        line=line,
                        source_type="annotation",
                        annotation="@Indexed",
                    ),
                )
            )

        return indexes

    # ------------------------------------------------------------------
    # Special index annotations
    # ------------------------------------------------------------------

    _SPECIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (TEXT_INDEXED_PATTERN, "text"),
        (GEOSPATIAL_INDEXED_PATTERN, "geospatial"),
        (HASH_INDEXED_PATTERN, "hashed"),
        (WILDCARD_INDEXED_PATTERN, "wildcard"),
    ]

    def _parse_special_indexed_fields(
        self,
        content: str,
        collection: str,
        filepath: str,
        constant_map: dict[str, str],
    ) -> list[IndexDefinition]:
        """Parse ``@TextIndexed``, ``@GeoSpatialIndexed``, ``@HashIndexed``,
        and ``@WildcardIndexed`` annotations.
        """
        indexes: list[IndexDefinition] = []

        for pattern, index_type in self._SPECIAL_PATTERNS:
            for match in pattern.finditer(content):
                annotation_end = match.end()
                line = self._get_line_number(content, match.start())

                field_name = self._find_next_field_name(content, annotation_end)
                if field_name is None:
                    continue

                resolved_name = self._resolve_field_name(field_name, constant_map)

                # Determine the direction value based on index type
                direction: int | str
                if index_type == "text":
                    direction = "text"
                elif index_type == "hashed":
                    direction = "hashed"
                else:
                    direction = 1

                indexes.append(
                    IndexDefinition(
                        collection=collection,
                        fields={resolved_name: direction},
                        index_type=index_type,
                        source=IndexSource(
                            file=filepath,
                            line=line,
                            source_type="annotation",
                            annotation=f"@{match.group(0).strip()}",
                        ),
                    )
                )

        return indexes

    # ------------------------------------------------------------------
    # Field name extraction
    # ------------------------------------------------------------------

    def _find_next_field_name(self, content: str, start_pos: int) -> str | None:
        """Find the Java field name declared after position *start_pos*.

        Scans forward from *start_pos* looking for a field declaration like
        ``private String fieldName;``.  Returns ``None`` if no declaration
        is found within a reasonable range.
        """
        # Look ahead a limited window (annotations may have whitespace/comments)
        search_window = content[start_pos : start_pos + 500]
        match = JAVA_FIELD_DECLARATION_PATTERN.search(search_window)
        if match:
            return match.group(1)
        return None
