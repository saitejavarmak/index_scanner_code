"""Script generator for MongoDB index creation scripts.

Converts IndexDefinition objects into executable MongoDB shell scripts,
Python pymongo scripts, and verification scripts.
"""

from __future__ import annotations

import json
from collections import defaultdict

from index_scanner_mcp.models import IndexDefinition


class ScriptGenerator:
    """Generates executable scripts from IndexDefinition objects."""

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _group_by_collection(
        indexes: list[IndexDefinition],
    ) -> dict[str, list[IndexDefinition]]:
        """Group indexes by collection name, preserving insertion order."""
        grouped: dict[str, list[IndexDefinition]] = defaultdict(list)
        for idx in indexes:
            grouped[idx.collection].append(idx)
        return dict(grouped)

    @staticmethod
    def _build_options(idx: IndexDefinition) -> dict:
        """Build an options dict from an IndexDefinition."""
        opts: dict = {}
        if idx.unique:
            opts["unique"] = True
        if idx.sparse:
            opts["sparse"] = True
        if idx.expire_after_seconds is not None:
            opts["expireAfterSeconds"] = idx.expire_after_seconds
        return opts

    # -- mongo shell ---------------------------------------------------------

    @staticmethod
    def _format_fields_shell(fields: dict[str, int]) -> str:
        """Format fields dict as a JSON-like MongoDB shell object."""
        parts = []
        for name, direction in fields.items():
            if isinstance(direction, str):
                parts.append(f'"{name}": "{direction}"')
            else:
                parts.append(f'"{name}": {direction}')
        return "{" + ", ".join(parts) + "}"

    @staticmethod
    def _format_options_shell(options: dict) -> str:
        """Format options dict as a JSON-like MongoDB shell object."""
        parts = []
        for key, value in options.items():
            if isinstance(value, bool):
                parts.append(f'"{key}": {"true" if value else "false"}')
            elif isinstance(value, int):
                parts.append(f'"{key}": {value}')
            else:
                parts.append(f'"{key}": "{value}"')
        return "{" + ", ".join(parts) + "}"

    def generate_mongo_shell(
        self, indexes: list[IndexDefinition], db_name: str | None = None
    ) -> str:
        """Generate MongoDB shell script (mongosh) for creating indexes.

        Args:
            indexes: List of IndexDefinition objects to generate scripts for.
            db_name: Optional database name to include a ``use`` statement.
                     If not provided, uses database names from IndexDefinitions.

        Returns:
            A syntactically valid MongoDB shell script string.
        """
        lines: list[str] = [
            "// Auto-generated MongoDB index creation script",
            "// Generated from source code analysis",
            "",
        ]

        # Determine if we should group by database
        db_names_from_indexes = {
            idx.database for idx in indexes if idx.database
        }

        if db_name:
            # Explicit db_name overrides everything
            lines.append(f"use {db_name};")
            lines.append("")
            grouped = self._group_by_collection(indexes)
            for collection in sorted(grouped):
                lines.append(f"// Collection: {collection}")
                for idx in grouped[collection]:
                    lines.append(self._format_create_index_shell(collection, idx))
                lines.append("")
        elif db_names_from_indexes:
            # Group by database, then by collection
            by_db = self._group_by_database(indexes)
            for db in sorted(by_db):
                lines.append(f"// Database: {db}")
                lines.append(f"use {db};")
                lines.append("")
                grouped = self._group_by_collection(by_db[db])
                for collection in sorted(grouped):
                    lines.append(f"// Collection: {collection}")
                    for idx in grouped[collection]:
                        lines.append(self._format_create_index_shell(collection, idx))
                    lines.append("")
        else:
            grouped = self._group_by_collection(indexes)
            for collection in sorted(grouped):
                lines.append(f"// Collection: {collection}")
                for idx in grouped[collection]:
                    lines.append(self._format_create_index_shell(collection, idx))
                lines.append("")

        return "\n".join(lines)

    def _format_create_index_shell(self, collection: str, idx: IndexDefinition) -> str:
        """Format a single createIndex statement."""
        fields_str = self._format_fields_shell(idx.fields)
        options = self._build_options(idx)
        if options:
            opts_str = self._format_options_shell(options)
            return f"db.{collection}.createIndex({fields_str}, {opts_str});"
        return f"db.{collection}.createIndex({fields_str});"

    @staticmethod
    def _group_by_database(
        indexes: list[IndexDefinition],
    ) -> dict[str, list[IndexDefinition]]:
        """Group indexes by database name."""
        grouped: dict[str, list[IndexDefinition]] = defaultdict(list)
        for idx in indexes:
            db = idx.database or "unknown"
            grouped[db].append(idx)
        return dict(grouped)

    # -- pymongo -------------------------------------------------------------

    @staticmethod
    def _direction_to_pymongo(direction: int | str) -> str:
        """Map a direction value to a pymongo constant name."""
        if isinstance(direction, str):
            if direction == "text":
                return "TEXT"
            if direction == "hashed":
                return "HASHED"
            return str(direction)
        return "ASCENDING" if direction == 1 else "DESCENDING"

    def generate_pymongo(
        self, indexes: list[IndexDefinition], db_name: str | None = None
    ) -> str:
        """Generate Python pymongo script for creating indexes.

        Args:
            indexes: List of IndexDefinition objects to generate scripts for.
            db_name: Optional database name for the connection.

        Returns:
            A valid Python script string using pymongo.
        """
        # Collect which pymongo constants we actually need
        needed_constants: set[str] = set()
        for idx in indexes:
            for direction in idx.fields.values():
                needed_constants.add(self._direction_to_pymongo(direction))

        # Build import line
        constant_imports = sorted(needed_constants)
        import_line = (
            "from pymongo import MongoClient, " + ", ".join(constant_imports)
        )

        db_str = db_name or "mydb"
        lines: list[str] = [
            "#!/usr/bin/env python3",
            '"""Auto-generated pymongo index creation script."""',
            "",
            import_line,
            "",
            'client = MongoClient("mongodb://localhost:27017")',
            f'db = client["{db_str}"]',
            "",
        ]

        grouped = self._group_by_collection(indexes)
        for collection in sorted(grouped):
            lines.append(f"# Collection: {collection}")
            for idx in grouped[collection]:
                keys_list = ", ".join(
                    f'("{name}", {self._direction_to_pymongo(d)})'
                    for name, d in idx.fields.items()
                )
                options = self._build_options(idx)
                if options:
                    opts_parts = []
                    for key, value in options.items():
                        if key == "expireAfterSeconds":
                            opts_parts.append(f"expireAfterSeconds={value}")
                        elif isinstance(value, bool):
                            opts_parts.append(
                                f"{key}={value}"
                            )
                        else:
                            opts_parts.append(f"{key}={value!r}")
                    opts_str = ", ".join(opts_parts)
                    lines.append(
                        f'db["{collection}"].create_index([{keys_list}], {opts_str})'
                    )
                else:
                    lines.append(
                        f'db["{collection}"].create_index([{keys_list}])'
                    )
            lines.append("")

        return "\n".join(lines)

    # -- verification script --------------------------------------------------

    def generate_verification_script(
        self, indexes: list[IndexDefinition], db_name: str | None = None
    ) -> str:
        """Generate a MongoDB shell script that verifies expected indexes exist.

        The script checks each expected index against the actual indexes on the
        target database and reports any missing ones.

        Args:
            indexes: List of IndexDefinition objects to verify.
            db_name: Optional database name to include a ``use`` statement.

        Returns:
            A MongoDB shell script string for verification.
        """
        lines: list[str] = [
            "// Auto-generated index verification script",
            "// Checks expected indexes against actual database indexes",
            "",
        ]

        if db_name:
            lines.append(f"use {db_name};")
            lines.append("")

        lines.append("var missing = [];")
        lines.append("")

        grouped = self._group_by_collection(indexes)
        for collection in sorted(grouped):
            lines.append(f"// Check indexes for: {collection}")
            lines.append(
                f"var existing_{collection} = db.{collection}.getIndexes()"
                ".map(function(i) { return JSON.stringify(i.key); });"
            )
            for idx in grouped[collection]:
                key_obj = self._build_key_json(idx.fields)
                human_readable = self._format_fields_human(idx.fields)
                lines.append(
                    f"if (existing_{collection}.indexOf('{key_obj}') === -1) "
                    f'missing.push("{collection}: {human_readable}");'
                )
            lines.append("")

        lines.append(
            'if (missing.length === 0) { print("All indexes present"); }'
        )
        lines.append(
            'else { print("Missing indexes: " + missing.join(", ")); }'
        )
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_key_json(fields: dict[str, int]) -> str:
        """Build a JSON string matching MongoDB's index key format.

        MongoDB stores index keys as ``{"field": 1}`` style JSON, so we
        replicate that exact format for comparison.
        """
        parts = []
        for name, direction in fields.items():
            if isinstance(direction, str):
                parts.append(f'\\"{name}\\":\\"{direction}\\"')
            else:
                parts.append(f'\\"{name}\\":{direction}')
        return "{" + ",".join(parts) + "}"

    @staticmethod
    def _format_fields_human(fields: dict[str, int]) -> str:
        """Human-readable field spec for missing-index messages."""
        parts = []
        for name, direction in fields.items():
            parts.append(f"{name}: {direction}")
        return "{" + ", ".join(parts) + "}"

    # -- PostgreSQL SQL -------------------------------------------------------

    def generate_postgresql_sql(
        self, indexes: list[IndexDefinition], db_name: str | None = None
    ) -> str:
        """Generate PostgreSQL CREATE INDEX statements.

        Args:
            indexes: List of IndexDefinition objects to generate SQL for.
            db_name: Optional schema name (defaults to 'public').

        Returns:
            A valid SQL script string with CREATE INDEX CONCURRENTLY statements.
        """
        schema = db_name or "public"
        lines: list[str] = [
            "-- Auto-generated PostgreSQL index creation script",
            "-- Generated from source code analysis",
            f"-- Schema: {schema}",
            "",
        ]

        grouped = self._group_by_collection(indexes)
        for table in sorted(grouped):
            lines.append(f"-- Index suggestions for {table}")
            for idx in grouped[table]:
                idx_name = self._build_pg_index_name(table, idx.fields)
                columns = self._format_pg_columns(idx.fields)
                unique_prefix = "UNIQUE " if idx.unique else ""
                lines.append(
                    f"CREATE {unique_prefix}INDEX CONCURRENTLY IF NOT EXISTS {idx_name} "
                    f"ON {schema}.{table} ({columns});"
                )
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_pg_index_name(table: str, fields: dict[str, int | str]) -> str:
        """Build a PostgreSQL index name like idx_table_field1_field2."""
        field_names = "_".join(
            name.replace(".", "_") for name in fields.keys()
        )
        # Truncate if too long (PostgreSQL limit is 63 chars)
        idx_name = f"idx_{table}_{field_names}"
        if len(idx_name) > 63:
            idx_name = idx_name[:63]
        return idx_name

    @staticmethod
    def _format_pg_columns(fields: dict[str, int | str]) -> str:
        """Format fields dict as PostgreSQL column list with optional sort direction."""
        parts = []
        for name, direction in fields.items():
            if isinstance(direction, int) and direction == -1:
                parts.append(f"{name} DESC")
            else:
                parts.append(name)
        return ", ".join(parts)
