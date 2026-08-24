"""Constant Resolver - parses Java constant classes to resolve symbolic references."""

from __future__ import annotations

import os
from fnmatch import fnmatch

from index_scanner_mcp.constants import CONSTANT_FIELD_PATTERN, SKIP_DIRS


class ConstantResolver:
    """Parses Java constant classes (e.g. AppConstants.java) to resolve
    symbolic references like ``AppConstants.USERID`` to their string values.
    """

    def __init__(self) -> None:
        self.constants: dict[str, dict[str, str]] = {}  # {class_name: {FIELD: "value"}}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_constants(self, project_path: str) -> dict[str, str]:
        """Find and parse all constant classes, return flat map of
        ``qualified_name -> value`` **and** ``unqualified_name -> value``.

        Returns an empty dict when no constant files are found.
        """
        self.constants.clear()
        flat_map: dict[str, str] = {}

        constant_files = self._find_constant_files(project_path)
        for filepath in constant_files:
            class_name = self._extract_class_name(filepath)
            fields = self.parse_constant_file(filepath)
            self.constants[class_name] = fields

            for field_name, value in fields.items():
                flat_map[f"{class_name}.{field_name}"] = value
                # Also store unqualified for convenience
                flat_map[field_name] = value

        return flat_map

    def resolve_or_fallback(
        self, reference: str, flat_map: dict[str, str]
    ) -> tuple[str, bool]:
        """Resolve a constant reference, returning ``(resolved_value, was_resolved)``.

        Looks up *reference* (e.g. ``"AppConstants.USERID"``) in *flat_map*.
        If found, returns the resolved string value and ``True``.
        If not found, returns the raw *reference* unchanged and ``False``
        so that callers can record an unresolved-reference warning in
        :pyattr:`ScanResult.errors`.
        """
        value = flat_map.get(reference)
        if value is not None:
            return value, True
        return reference, False


    def parse_constant_file(self, filepath: str) -> dict[str, str]:
        """Extract ``public static final String`` fields from a Java file.

        Returns a dict mapping ``FIELD_NAME -> "value"`` for every matching
        declaration found in the file.
        """
        try:
            with open(filepath, encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            return {}

        fields: dict[str, str] = {}
        for match in CONSTANT_FIELD_PATTERN.finditer(content):
            field_name = match.group(1)
            field_value = match.group(2)
            fields[field_name] = field_value
        return fields

    def resolve(self, class_name: str, field_name: str) -> str | None:
        """Resolve a constant reference like ``AppConstants.USERID`` to its
        string value.  Returns ``None`` if the class or field is not found.
        """
        class_fields = self.constants.get(class_name)
        if class_fields is None:
            return None
        return class_fields.get(field_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_constant_files(project_path: str) -> list[str]:
        """Recursively find files matching ``*Constants.java`` and
        ``*Config.java`` under *project_path*, skipping excluded dirs.
        """
        matches: list[str] = []
        for root, dirs, files in os.walk(project_path):
            # Prune excluded directories in-place so os.walk skips them
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for filename in files:
                if fnmatch(filename, "*Constants.java") or fnmatch(filename, "*Config.java"):
                    matches.append(os.path.join(root, filename))
        return matches

    @staticmethod
    def _extract_class_name(filepath: str) -> str:
        """Derive the Java class name from a file path.

        ``/some/path/AppConstants.java`` → ``AppConstants``
        """
        basename = os.path.basename(filepath)
        return os.path.splitext(basename)[0]
