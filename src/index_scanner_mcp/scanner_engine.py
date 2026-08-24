"""Scanner Engine - orchestrates file discovery, parsing, and result aggregation."""

from __future__ import annotations

import os

from index_scanner_mcp.annotation_parser import AnnotationParser
from index_scanner_mcp.constant_resolver import ConstantResolver
from index_scanner_mcp.constants import SKIP_DIRS
from index_scanner_mcp.models import IndexDefinition, IndexSuggestion, ScanResult
from index_scanner_mcp.query_analyzer import QueryPatternAnalyzer


class ScannerEngine:
    """Orchestrates file discovery, constant resolution, annotation parsing,
    query analysis, and result aggregation across project directories.
    """

    def __init__(self, skip_dirs: set[str] | None = None) -> None:
        self.skip_dirs = skip_dirs if skip_dirs is not None else SKIP_DIRS
        self.constant_resolver = ConstantResolver()
        self.annotation_parser = AnnotationParser()
        self.query_analyzer = QueryPatternAnalyzer()

    def scan_project(self, project_path: str) -> ScanResult:
        """Scan a single project and return all discovered index definitions.

        Returns an error ``ScanResult`` if *project_path* does not exist or
        is not a directory.
        """
        if not os.path.isdir(project_path):
            return ScanResult(
                project_path=project_path,
                errors=[f"Path is not a valid directory: {project_path}"],
            )

        # Step 1: Resolve constants across the project
        constant_map = self.constant_resolver.resolve_constants(project_path)

        # Step 1b: Detect database names from properties and constants
        database_names = self._detect_database_names(project_path, constant_map)

        # Step 2: Discover all Java source files
        java_files = self.discover_java_files(project_path)

        indexes: list[IndexDefinition] = []
        suggestions: list[IndexSuggestion] = []
        errors: list[str] = []

        # Step 3: Parse each file for annotations and query patterns
        for filepath in java_files:
            try:
                file_indexes = self.annotation_parser.parse_file(filepath, constant_map)
                indexes.extend(file_indexes)
            except Exception as e:
                errors.append(f"{filepath}: {e}")

            try:
                file_suggestions = self.query_analyzer.analyze_file(filepath, constant_map)
                suggestions.extend(file_suggestions)
            except Exception as e:
                errors.append(f"{filepath}: {e}")

        # Step 4: Deduplicate indexes
        unique_indexes = self._deduplicate_indexes(indexes)

        # Step 5: Filter suggestions that match existing indexes
        filtered_suggestions = self._filter_suggestions(unique_indexes, suggestions)

        # Step 6: Assign database names to indexes AND suggestions if detected
        if database_names:
            primary_db = database_names[0]
            for idx in unique_indexes:
                if idx.database is None:
                    idx.database = primary_db
            for sug in filtered_suggestions:
                if sug.database is None:
                    sug.database = primary_db

        return ScanResult(
            project_path=project_path,
            indexes=unique_indexes,
            suggestions=filtered_suggestions,
            constants_resolved=len(constant_map),
            files_scanned=len(java_files),
            errors=errors,
            database_names=database_names,
        )

    def scan_multiple_projects(self, root_path: str) -> dict[str, ScanResult]:
        """Scan all sub-projects under a root directory.

        Each immediate subdirectory of *root_path* is treated as an
        independent project.  Returns a dict mapping project directory
        names to their ``ScanResult``.
        """
        if not os.path.isdir(root_path):
            return {}

        results: dict[str, ScanResult] = {}
        for entry in sorted(os.listdir(root_path)):
            sub_path = os.path.join(root_path, entry)
            if os.path.isdir(sub_path) and entry not in self.skip_dirs:
                results[entry] = self.scan_project(sub_path)
        return results

    def discover_java_files(self, project_path: str) -> list[str]:
        """Recursively find all ``.java`` files, skipping excluded directories and test files."""
        java_files: list[str] = []
        # Skip test directories
        test_dirs = {'test', 'tests', 'src/test', 'testFixtures'}
        for root, dirs, files in os.walk(project_path):
            # Prune excluded directories in-place so os.walk skips them
            dirs[:] = [d for d in dirs if d not in self.skip_dirs and d not in test_dirs]
            for filename in files:
                if filename.endswith(".java"):
                    # Skip test files
                    if filename.endswith("Test.java") or filename.endswith("Tests.java") or filename.startswith("Test"):
                        continue
                    # Skip backup/restore/migration/utility files that don't
                    # represent production query paths
                    if self._is_non_query_file(filename):
                        continue
                    java_files.append(os.path.join(root, filename))
        return java_files

    @staticmethod
    def _is_non_query_file(filename: str) -> bool:
        """Return True if the file is unlikely to contain production query patterns.

        Filters out backup utilities, data migration scripts, seed data,
        import/export tools, and test helpers that should not generate
        index suggestions.
        """
        name_lower = filename.lower()
        # Patterns indicating non-production-query code
        skip_keywords = (
            'backup', 'restore', 'migration', 'migrate', 'datadump',
            'import', 'export', 'seed', 'seeder', 'fixture',
            'mock', 'stub', 'fake', 'dummy', 'sample',
            'archive', 'purge', 'cleanup', 'housekeep',
            'oneoff', 'one_off', 'onetime', 'one_time',
            'adhoc', 'ad_hoc', 'temp', 'temporary',
            'benchmark', 'perf_test', 'loadtest',
        )
        for kw in skip_keywords:
            if kw in name_lower:
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(idx: IndexDefinition) -> tuple:
        """Build a hashable deduplication key for an IndexDefinition.

        Two definitions are duplicates when they share the same
        (collection, fields dict, unique, sparse, expire_after_seconds).
        """
        fields_tuple = tuple(sorted(idx.fields.items()))
        return (
            idx.collection,
            fields_tuple,
            idx.unique,
            idx.sparse,
            idx.expire_after_seconds,
        )

    @staticmethod
    def _detect_database_names(
        project_path: str, constant_map: dict[str, str]
    ) -> list[str]:
        """Detect MongoDB database names from config files, constants, and Java code.

        Searches:
        - application.properties / application.yml / bootstrap.yml
        - .env files
        - Java config classes (MongoClient, MongoTemplate, @Value annotations)
        - Constants with DB_NAME patterns
        - MongoDB URI strings containing database names
        """
        import re

        db_names: list[str] = []
        seen: set[str] = set()

        # Patterns for properties/yml files
        db_property_patterns = [
            re.compile(r"spring\.data\.mongodb\.database\s*[:=]\s*(.+)"),
            re.compile(r"spring\.data\.mongodb\.uri\s*[:=]\s*mongodb(?:\+srv)?://[^/]+/(\w+)"),
            re.compile(r"mongodb\.database\s*[:=]\s*(.+)"),
            re.compile(r"mongodb\.uri\s*[:=]\s*mongodb(?:\+srv)?://[^/]+/(\w+)"),
            re.compile(r"primary\.mongodb\.(?:\w+\.)?database\s*[:=]\s*(.+)"),
            re.compile(r"app\.mongo\.database(?:\.\w+)?\s*[:=]\s*(.+)"),
            re.compile(r"(?:\w+)\.mongo\.db\s*[:=]\s*(.+)"),
            re.compile(r"mongo\.(?:\w+\.)?database\s*[:=]\s*(.+)"),
        ]

        # Patterns for .env files
        env_patterns = [
            re.compile(r"(?:\w*MONGO)_DB(?:_NAME)?(?:_\w+)?\s*=\s*['\"]?(\w+)['\"]?"),
            re.compile(r"DB_NAME\s*=\s*['\"]?(\w+)['\"]?"),
            re.compile(r"MONGODB_DATABASE\s*=\s*['\"]?(\w+)['\"]?"),
            re.compile(r"MONGO_DATABASE\s*=\s*['\"]?(\w+)['\"]?"),
        ]

        # Patterns for Java code — MongoClient/MongoTemplate config
        java_db_patterns = [
            # new MongoClient("host").getDatabase("dbName")
            re.compile(r'getDatabase\s*\(\s*"([^"]+)"'),
            # mongoTemplate = new MongoTemplate(factory, "dbName")
            re.compile(r'MongoTemplate\s*\([^,]+,\s*"([^"]+)"'),
            # @Value("${spring.data.mongodb.database}") or @Value("${mongo.db}")
            re.compile(r'@Value\s*\(\s*"\$\{[^}]*(?:database|\.db)\s*(?::\s*(\w+))?\}"'),
            # getSiblingDB("dbName")
            re.compile(r'getSiblingDB\s*\(\s*"([^"]+)"'),
            # MongoNamespace("dbName", "collection")
            re.compile(r'MongoNamespace\s*\(\s*"([^"]+)"'),
            # client.getDB("dbName")
            re.compile(r'getDB\s*\(\s*"([^"]+)"'),
        ]

        # Config file names to scan
        # Names that are NOT databases — common false positives
        _db_blocklist = {
            "admin", "local", "config", "test", "true", "false", "null",
            "ALL", "NONE", "DEFAULT", "SYSTEM",
        }

        def _is_valid_db_name(name: str) -> bool:
            """Filter out values that aren't real database names."""
            if not name or len(name) < 2 or len(name) > 64:
                return False
            if name.startswith("$") or name.startswith("{"):
                return False
            if name in _db_blocklist:
                return False
            # Skip if it looks like a Java constant (ALL_CAPS_WITH_UNDERSCORES)
            if name.isupper() and len(name) > 3:
                return False
            # Skip PascalCase names (likely class names, not DB names)
            if name[0].isupper() and any(c.isupper() for c in name[1:]) and not name.isupper():
                return False
            # Skip camelCase that doesn't contain db/mongo hints
            if name[0].islower() and any(c.isupper() for c in name[1:]):
                lower = name.lower()
                if "db" not in lower and "mongo" not in lower:
                    return False
            return True

        config_files = {
            "application.properties", "application.yml", "application.yaml",
            "bootstrap.properties", "bootstrap.yml", "bootstrap.yaml",
            "application-dev.properties", "application-dev.yml",
            "application-local.properties", "application-local.yml",
            "application-prod.properties", "application-prod.yml",
        }
        env_files = {".env", ".env.local", ".env.dev", ".env.example"}

        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                filepath = os.path.join(root, fname)

                # Properties/YAML config files
                if fname in config_files:
                    try:
                        with open(filepath, encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                for pattern in db_property_patterns:
                                    m = pattern.search(line)
                                    if m:
                                        db_name = m.group(1).strip().strip("'\"")
                                        # Skip placeholders like ${MONGO_DB}
                                        if _is_valid_db_name(db_name) and db_name not in seen:
                                            seen.add(db_name)
                                            db_names.append(db_name)
                    except (OSError, UnicodeDecodeError):
                        pass

                # .env files
                elif fname in env_files:
                    try:
                        with open(filepath, encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                for pattern in env_patterns:
                                    m = pattern.search(line)
                                    if m:
                                        db_name = m.group(1).strip().strip("'\"")
                                        if _is_valid_db_name(db_name) and db_name not in seen:
                                            seen.add(db_name)
                                            db_names.append(db_name)
                    except (OSError, UnicodeDecodeError):
                        pass

                # Java files — scan for DB connection patterns
                elif fname.endswith(".java"):
                    try:
                        with open(filepath, encoding="utf-8") as fh:
                            content = fh.read()
                        for pattern in java_db_patterns:
                            for m in pattern.finditer(content):
                                db_name = m.group(1)
                                if _is_valid_db_name(db_name) and db_name not in seen:
                                    seen.add(db_name)
                                    db_names.append(db_name)
                    except (OSError, UnicodeDecodeError):
                        pass

        # Also check constants for DB name patterns
        # Only match keys that are clearly database name constants
        # e.g. DB_NAME, MONGO_DB, DATABASE_NAME — NOT collection-related keys
        db_constant_keys = [
            k for k in constant_map
            if any(
                token in k.upper()
                for token in ("DB_NAME", "DATABASE_NAME", "MONGO_DATABASE", "MONGODB_DATABASE")
            )
            and not any(
                skip in k.upper()
                for skip in ("COLLECTION", "TABLE", "INDEX", "FIELD", "COLUMN")
            )
        ]
        for key in db_constant_keys:
            val = constant_map[key]
            if _is_valid_db_name(val) and val not in seen:
                seen.add(val)
                db_names.append(val)

        return db_names

    @classmethod
    def _deduplicate_indexes(
        cls, indexes: list[IndexDefinition]
    ) -> list[IndexDefinition]:
        """Remove duplicate IndexDefinitions, keeping the first occurrence."""
        seen: set[tuple] = set()
        unique: list[IndexDefinition] = []
        for idx in indexes:
            key = cls._dedup_key(idx)
            if key not in seen:
                seen.add(key)
                unique.append(idx)
        return unique

    @staticmethod
    def _suggestion_matches_index(
        suggestion: IndexSuggestion, index: IndexDefinition
    ) -> bool:
        """Check whether a suggestion's (collection, fields) matches an index."""
        if suggestion.collection != index.collection:
            return False
        return set(suggestion.fields.keys()) == set(index.fields.keys())

    @classmethod
    def _filter_suggestions(
        cls,
        indexes: list[IndexDefinition],
        suggestions: list[IndexSuggestion],
    ) -> list[IndexSuggestion]:
        """Remove suggestions whose (collection, fields) match an existing index."""
        return [
            s
            for s in suggestions
            if not any(cls._suggestion_matches_index(s, idx) for idx in indexes)
        ]
