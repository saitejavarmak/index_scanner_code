"""Helm context loader for the PostgreSQL Guardrails system.

Reads helm values files from team helm chart repositories to extract
database connection information. This gives the guardrails engine a
team-wide view of database topology — which services connect to which
databases, what connection strings look like, and what environment
variables are configured.

The helm repo naming convention is ``{team}-helm-charts``.

Usage::

    loader = HelmContextLoader(
        team_name="analytics",
        helm_repo_base="/path/to/repos"
    )
    context = loader.load()
    # context.services["erm-skills-data-service"].databases
    # -> [DatabaseConnection(type="PostgreSQL", host="...", db_name="...")]
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Patterns to detect DB connection strings in helm values
_MONGO_URI_PATTERNS = [
    re.compile(r"mongodb(?:\+srv)?://[^\s\"']+", re.IGNORECASE),
]
_PG_URI_PATTERNS = [
    re.compile(r"jdbc:postgresql://[^\s\"']+", re.IGNORECASE),
    re.compile(r"postgresql://[^\s\"']+", re.IGNORECASE),
    re.compile(r"postgres://[^\s\"']+", re.IGNORECASE),
]

# Known env var keys that hold DB connection info
_MONGO_ENV_KEYS = {
    "mongo_uri", "mongodb_uri", "mongo_url", "mongodb_url",
    "spring.data.mongodb.uri", "spring_data_mongodb_uri",
    "mongo_connection_string", "mongodb_connection_string",
}
_PG_ENV_KEYS = {
    "database_url", "postgres_url", "postgresql_url", "pg_url",
    "spring.datasource.url", "spring_datasource_url",
    "jdbc_url", "db_url", "db_connection_string",
    "sqlalchemy_database_uri",
}
_DB_HOST_KEYS = {
    "db_host", "database_host", "postgres_host", "pg_host",
    "mongo_host", "mongodb_host",
    "spring.data.mongodb.host", "spring.datasource.host",
}
_DB_NAME_KEYS = {
    "db_name", "database_name", "postgres_db", "pg_database",
    "mongo_database", "mongodb_database",
    "spring.data.mongodb.database", "spring.datasource.database",
}


@dataclass
class DatabaseConnection:
    """A database connection discovered from helm values."""

    db_type: str  # "PostgreSQL", "MongoDB", "Unknown"
    host: str = ""
    port: int = 0
    db_name: str = ""
    connection_string: str = ""
    env_var_name: str = ""  # which env var held this value
    source_file: str = ""  # which values file this came from


@dataclass
class ServiceHelmContext:
    """Helm-derived context for a single service."""

    service_name: str
    values_file: str = ""
    databases: list[DatabaseConnection] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    raw_values: dict = field(default_factory=dict)


@dataclass
class TeamHelmContext:
    """Aggregated helm context for all services in a team."""

    team_name: str
    helm_repo_path: str = ""
    services: dict[str, ServiceHelmContext] = field(default_factory=dict)
    all_databases: list[DatabaseConnection] = field(default_factory=list)
    postgres_databases: list[DatabaseConnection] = field(default_factory=list)
    mongo_databases: list[DatabaseConnection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class HelmContextLoader:
    """Load database context from a team's helm chart repository.

    Reads helm values files referenced in the service catalog and extracts
    database connection information (connection strings, hosts, DB names,
    environment variables).

    Args:
        team_name:      Name of the team (used to derive helm repo name).
        helm_repo_base: Base directory containing helm chart repos.
                        The team's helm repo is expected at
                        ``{helm_repo_base}/{team}-helm-charts/``.
        catalog_entries: Optional list of (service_name, values_path) tuples
                        from the service catalog. If provided, only those
                        values files are scanned.
    """

    def __init__(
        self,
        team_name: str,
        helm_repo_base: str,
        catalog_entries: list[tuple[str, str]] | None = None,
    ) -> None:
        self._team_name = team_name
        self._helm_repo_base = helm_repo_base
        self._catalog_entries = catalog_entries or []

    @property
    def helm_repo_path(self) -> str:
        """Resolved path to the team's helm chart repository."""
        return str(Path(self._helm_repo_base) / f"{self._team_name}-helm-charts")

    def load(self) -> TeamHelmContext:
        """Load and parse all helm values files for the team.

        Returns:
            A :class:`TeamHelmContext` with database connections extracted
            from all discoverable values files.
        """
        context = TeamHelmContext(
            team_name=self._team_name,
            helm_repo_path=self.helm_repo_path,
        )

        repo_path = Path(self.helm_repo_path)
        if not repo_path.exists():
            context.errors.append(
                f"Helm repo not found: {self.helm_repo_path}. "
                f"Expected at {{helm_repo_base}}/{self._team_name}-helm-charts/"
            )
            logger.warning(
                "Helm repo '%s' does not exist — skipping helm context loading.",
                self.helm_repo_path,
            )
            return context

        # Determine which values files to scan
        values_files = self._resolve_values_files(repo_path)

        for service_name, values_path in values_files:
            svc_context = self._load_service_context(
                service_name, values_path, str(repo_path)
            )
            context.services[service_name] = svc_context
            context.all_databases.extend(svc_context.databases)

        # Categorise databases
        context.postgres_databases = [
            db for db in context.all_databases if db.db_type == "PostgreSQL"
        ]
        context.mongo_databases = [
            db for db in context.all_databases if db.db_type == "MongoDB"
        ]

        logger.info(
            "Helm context loaded for team '%s': %d services, %d PG connections, %d Mongo connections",
            self._team_name,
            len(context.services),
            len(context.postgres_databases),
            len(context.mongo_databases),
        )

        return context

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_values_files(
        self, repo_path: Path
    ) -> list[tuple[str, str]]:
        """Determine which values files to scan.

        If catalog entries were provided, use those. Otherwise, discover
        all ``values*.yaml`` files in the repo.

        Returns:
            List of (service_name, absolute_path_to_values_file) tuples.
        """
        results: list[tuple[str, str]] = []

        if self._catalog_entries:
            for service_name, rel_path in self._catalog_entries:
                if not rel_path or "service not found" in rel_path.lower():
                    continue
                abs_path = repo_path / rel_path
                if abs_path.exists():
                    results.append((service_name, str(abs_path)))
                else:
                    # Try without the filename (might be a directory)
                    logger.debug(
                        "Values file not found at '%s' for service '%s'",
                        abs_path,
                        service_name,
                    )
        else:
            # Auto-discover all values files
            for vf in sorted(repo_path.rglob("values*.yaml")):
                if vf.is_file():
                    # Derive service name from parent directory
                    svc_name = vf.parent.name
                    results.append((svc_name, str(vf)))

        return results

    def _load_service_context(
        self, service_name: str, values_path: str, repo_base: str
    ) -> ServiceHelmContext:
        """Parse a single values file and extract DB connections."""
        svc = ServiceHelmContext(
            service_name=service_name,
            values_file=values_path,
        )

        try:
            with open(values_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            logger.warning(
                "Failed to parse values file '%s': %s", values_path, exc
            )
            return svc

        if not isinstance(raw, dict):
            return svc

        svc.raw_values = raw

        # Extract all env vars (flattened)
        env_vars = self._extract_env_vars(raw)
        svc.env_vars = env_vars

        # Find database connections
        svc.databases = self._extract_db_connections(env_vars, values_path)

        return svc

    def _extract_env_vars(self, values: dict, prefix: str = "") -> dict[str, str]:
        """Recursively extract key-value pairs that look like env vars.

        Handles common helm patterns:
        - ``env: [{name: X, value: Y}]``
        - ``envVars: {KEY: VALUE}``
        - Flat key-value mappings
        """
        env_vars: dict[str, str] = {}

        for key, value in values.items():
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                # Recurse into nested dicts
                env_vars.update(self._extract_env_vars(value, full_key))
            elif isinstance(value, list):
                # Handle env: [{name: X, value: Y}] pattern
                for item in value:
                    if isinstance(item, dict) and "name" in item:
                        name = str(item["name"])
                        val = str(item.get("value", item.get("valueFrom", "")))
                        env_vars[name.lower()] = val
            elif isinstance(value, str):
                env_vars[full_key.lower()] = value
                # Also store just the leaf key
                env_vars[key.lower()] = value

        return env_vars

    def _extract_db_connections(
        self, env_vars: dict[str, str], source_file: str
    ) -> list[DatabaseConnection]:
        """Identify database connections from extracted env vars."""
        connections: list[DatabaseConnection] = []
        seen_strings: set[str] = set()

        for key, value in env_vars.items():
            if not value or value == "None":
                continue

            key_lower = key.lower().replace("-", "_").replace(".", "_")

            # Check for PostgreSQL connection strings
            for pattern in _PG_URI_PATTERNS:
                if pattern.search(value) and value not in seen_strings:
                    seen_strings.add(value)
                    conn = self._parse_pg_uri(value, key, source_file)
                    connections.append(conn)

            # Check for MongoDB connection strings
            for pattern in _MONGO_URI_PATTERNS:
                if pattern.search(value) and value not in seen_strings:
                    seen_strings.add(value)
                    conn = self._parse_mongo_uri(value, key, source_file)
                    connections.append(conn)

            # Check for known DB env var keys (without explicit URI)
            if key_lower in _PG_ENV_KEYS and value not in seen_strings:
                if any(p.search(value) for p in _PG_URI_PATTERNS):
                    seen_strings.add(value)
                    conn = self._parse_pg_uri(value, key, source_file)
                    connections.append(conn)

            if key_lower in _MONGO_ENV_KEYS and value not in seen_strings:
                if any(p.search(value) for p in _MONGO_URI_PATTERNS):
                    seen_strings.add(value)
                    conn = self._parse_mongo_uri(value, key, source_file)
                    connections.append(conn)

            # Check for host/name keys
            if key_lower in _DB_HOST_KEYS:
                # We'll record it but can't determine type without more info
                pass

        return connections

    @staticmethod
    def _parse_pg_uri(
        uri: str, env_var: str, source_file: str
    ) -> DatabaseConnection:
        """Extract host, port, and db_name from a PostgreSQL URI."""
        host = ""
        port = 5432
        db_name = ""

        # Try to parse jdbc:postgresql://host:port/dbname or postgresql://...
        match = re.search(
            r"(?:jdbc:)?postgres(?:ql)?://([^/:]+)(?::(\d+))?(?:/([^?\s]+))?",
            uri,
            re.IGNORECASE,
        )
        if match:
            host = match.group(1) or ""
            port = int(match.group(2)) if match.group(2) else 5432
            db_name = match.group(3) or ""

        return DatabaseConnection(
            db_type="PostgreSQL",
            host=host,
            port=port,
            db_name=db_name,
            connection_string=uri,
            env_var_name=env_var,
            source_file=source_file,
        )

    @staticmethod
    def _parse_mongo_uri(
        uri: str, env_var: str, source_file: str
    ) -> DatabaseConnection:
        """Extract host, port, and db_name from a MongoDB URI."""
        host = ""
        port = 27017
        db_name = ""

        match = re.search(
            r"mongodb(?:\+srv)?://(?:[^@]+@)?([^/:]+)(?::(\d+))?(?:/([^?\s]+))?",
            uri,
            re.IGNORECASE,
        )
        if match:
            host = match.group(1) or ""
            port = int(match.group(2)) if match.group(2) else 27017
            db_name = match.group(3) or ""

        return DatabaseConnection(
            db_type="MongoDB",
            host=host,
            port=port,
            db_name=db_name,
            connection_string=uri,
            env_var_name=env_var,
            source_file=source_file,
        )
