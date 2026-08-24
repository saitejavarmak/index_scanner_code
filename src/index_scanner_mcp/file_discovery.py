"""FileDiscovery - multi-language file walker for the index scanner."""

from __future__ import annotations

import os
from pathlib import Path

from index_scanner_mcp.constants import SCAN_EXTENSIONS, SKIP_DIRS

CATEGORY_MAP: dict[str, str] = {
    ".java": "java", ".kt": "java",
    ".py": "python",
    ".js": "javascript", ".ts": "javascript",
    ".sql": "config", ".xml": "config", ".yml": "config",
    ".yaml": "config", ".json": "config", ".properties": "config",
    ".conf": "config",
}


class FileDiscovery:
    """Recursive multi-language file walker with directory exclusion."""

    def __init__(self, skip_dirs: set[str] | None = None) -> None:
        self.skip_dirs = skip_dirs if skip_dirs is not None else SKIP_DIRS

    def discover_files(
        self, project_path: str, extensions: set[str] | None = None
    ) -> list[str]:
        allowed = extensions if extensions else set(SCAN_EXTENSIONS.keys())
        found: list[str] = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in self.skip_dirs]
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in allowed:
                    found.append(os.path.join(root, fname))
        return found

    def discover_by_category(self, project_path: str) -> dict[str, list[str]]:
        categories: dict[str, list[str]] = {
            "java": [], "python": [], "javascript": [], "config": [],
        }
        for filepath in self.discover_files(project_path):
            ext = Path(filepath).suffix.lower()
            cat = CATEGORY_MAP.get(ext)
            if cat:
                categories[cat].append(filepath)
        return categories
