"""Service catalog integration for the PostgreSQL Guardrails system.

Loads a CSV-based service registry that maps teams to their services,
code repositories (URI locations), programming languages, and database types.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Sentinel value in the URI location column indicating the repo was not found
_SERVICE_NOT_FOUND = "service not found"

# Case-insensitive DB Service tokens
_DB_POSTGRES = "postgresql"
_DB_MONGO = "mongodb"


@dataclass
class ServiceEntry:
    """A single row from the service catalog CSV.

    Attributes:
        namespace:    Kubernetes/organizational namespace.
        team:         Owning team name.
        service_name: Human-readable service name.
        team_size:    Number of team members (as a string, may be empty).
        team_members: Comma-separated team member list (as a string).
        language:     Primary programming language.
        uri_location: Repository path / URI, or "service not found" when absent.
        sub_team:     Optional sub-team grouping.
        db_service:   Database type(s): "PostgreSQL", "MongoDB",
                      "MongoDB, PostgreSQL", "None", or "".
    """

    namespace: str
    team: str
    service_name: str
    team_size: str
    team_members: str
    language: str
    uri_location: str
    sub_team: str
    db_service: str


class ServiceCatalog:
    """Load and query a CSV service catalog.

    The CSV file is expected to have these columns (order matters only when
    names are absent; the loader uses the header row):
        Namespace, Team, ServiceName, Team Size, Team Members, Language,
        URI location if present, Sub Team, DB Service

    Example usage::

        catalog = ServiceCatalog("/path/to/catalog.csv")
        all_services = catalog.load()
        team_services = catalog.filter_by_team("platform-team")
        pg_services = catalog.get_postgres_services(team_services)
        paths = catalog.get_repo_paths(pg_services)
    """

    # Map CSV header names → ServiceEntry field names
    _COLUMN_MAP: dict[str, str] = {
        "namespace": "namespace",
        "team": "team",
        "servicename": "service_name",
        "team size": "team_size",
        "team members": "team_members",
        "language": "language",
        "uri location if present": "uri_location",
        "sub team": "sub_team",
        "db service": "db_service",
    }

    def __init__(self, catalog_path: str) -> None:
        """Initialise with the path to the service catalog CSV file.

        Args:
            catalog_path: Absolute or relative path to the CSV file.
        """
        self._catalog_path = catalog_path
        self._entries: list[ServiceEntry] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> list[ServiceEntry]:
        """Read the CSV catalog and return all entries.

        Services whose URI location contains "service not found" (case-
        insensitive) are included in the returned list but flagged with a
        warning log.  Callers that need only valid repo paths should use
        :meth:`get_repo_paths`, which excludes those entries automatically.

        Returns:
            A list of :class:`ServiceEntry` objects, one per CSV row.

        Raises:
            FileNotFoundError: If the CSV file does not exist.
            ValueError:        If required columns are missing from the CSV.
        """
        if self._entries is not None:
            return self._entries

        entries: list[ServiceEntry] = []

        with open(self._catalog_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)

            if reader.fieldnames is None:
                logger.warning(
                    "Service catalog '%s' appears to be empty.", self._catalog_path
                )
                self._entries = entries
                return entries

            # Normalise header names for flexible matching
            normalised_headers = {
                h.strip().lower(): h for h in reader.fieldnames if h is not None
            }
            self._validate_columns(normalised_headers)

            for row in reader:
                entry = self._parse_row(row, normalised_headers)
                if entry is not None:
                    entries.append(entry)

        self._entries = entries
        return entries

    def filter_by_team(self, team_name: str) -> list[ServiceEntry]:
        """Return services belonging to *team_name* (case-insensitive).

        Args:
            team_name: The team name to filter on.

        Returns:
            Filtered list of :class:`ServiceEntry` objects.
        """
        needle = team_name.strip().lower()
        return [e for e in self._get_entries() if e.team.strip().lower() == needle]

    def filter_by_namespace(self, namespace: str) -> list[ServiceEntry]:
        """Return services in *namespace* (case-insensitive).

        Args:
            namespace: The namespace to filter on.

        Returns:
            Filtered list of :class:`ServiceEntry` objects.
        """
        needle = namespace.strip().lower()
        return [
            e for e in self._get_entries() if e.namespace.strip().lower() == needle
        ]

    def filter_by_sub_team(self, sub_team: str) -> list[ServiceEntry]:
        """Return services belonging to *sub_team* (case-insensitive).

        Args:
            sub_team: The sub-team name to filter on.

        Returns:
            Filtered list of :class:`ServiceEntry` objects.
        """
        needle = sub_team.strip().lower()
        return [
            e for e in self._get_entries() if e.sub_team.strip().lower() == needle
        ]

    def get_postgres_services(
        self, services: list[ServiceEntry]
    ) -> list[ServiceEntry]:
        """Filter *services* to those that use PostgreSQL.

        Matches DB Service values "PostgreSQL" and "MongoDB, PostgreSQL"
        (case-insensitive, any ordering).

        Args:
            services: Input list to filter.

        Returns:
            Services where :meth:`has_postgres` returns ``True``.
        """
        return [s for s in services if self.has_postgres(s.db_service)]

    def get_mongodb_services(
        self, services: list[ServiceEntry]
    ) -> list[ServiceEntry]:
        """Filter *services* to those that use MongoDB.

        Matches DB Service values "MongoDB" and "MongoDB, PostgreSQL"
        (case-insensitive, any ordering).

        Args:
            services: Input list to filter.

        Returns:
            Services where :meth:`has_mongodb` returns ``True``.
        """
        return [s for s in services if self.has_mongodb(s.db_service)]

    def get_repo_paths(self, services: list[ServiceEntry]) -> list[str]:
        """Return valid repository paths from *services*.

        Services with "service not found" in their URI location are skipped
        and a warning is logged for each one.  Empty URI locations are also
        skipped.

        Args:
            services: Input list of service entries.

        Returns:
            A list of non-empty repository path strings.
        """
        paths: list[str] = []
        for entry in services:
            uri = entry.uri_location.strip()
            if not uri:
                logger.debug(
                    "Skipping service '%s' — empty URI location.", entry.service_name
                )
                continue
            if _SERVICE_NOT_FOUND in uri.lower():
                logger.warning(
                    "Skipping service '%s' (team: %s) — URI location is '%s'.",
                    entry.service_name,
                    entry.team,
                    uri,
                )
                continue
            paths.append(uri)
        return paths

    # ------------------------------------------------------------------
    # DB-type helpers
    # ------------------------------------------------------------------

    @staticmethod
    def has_postgres(db_service: str) -> bool:
        """Return ``True`` when *db_service* includes PostgreSQL.

        Matching is case-insensitive and handles comma-separated values
        in any order (e.g. "MongoDB, PostgreSQL" and "PostgreSQL, MongoDB").

        Args:
            db_service: The raw ``DB Service`` column value.

        Returns:
            ``True`` if PostgreSQL is listed; ``False`` otherwise.
        """
        return _DB_POSTGRES in db_service.lower()

    @staticmethod
    def has_mongodb(db_service: str) -> bool:
        """Return ``True`` when *db_service* includes MongoDB.

        Matching is case-insensitive and handles comma-separated values
        in any order.

        Args:
            db_service: The raw ``DB Service`` column value.

        Returns:
            ``True`` if MongoDB is listed; ``False`` otherwise.
        """
        return _DB_MONGO in db_service.lower()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_entries(self) -> list[ServiceEntry]:
        """Return the cached entry list, loading it on first access."""
        if self._entries is None:
            self.load()
        assert self._entries is not None  # noqa: S101 – guaranteed by load()
        return self._entries

    def _validate_columns(self, normalised_headers: dict[str, str]) -> None:
        """Raise :exc:`ValueError` if any required column header is missing.

        Args:
            normalised_headers: Mapping of lowercased header → original header.

        Raises:
            ValueError: When one or more required columns are absent.
        """
        required = set(self._COLUMN_MAP.keys())
        present = set(normalised_headers.keys())
        missing = required - present
        if missing:
            raise ValueError(
                f"Service catalog CSV is missing required columns: "
                f"{sorted(missing)!r}.  Found: {sorted(present)!r}"
            )

    def _parse_row(
        self,
        row: dict[str, str | None],
        normalised_headers: dict[str, str],
    ) -> ServiceEntry | None:
        """Convert a CSV *row* to a :class:`ServiceEntry`.

        Returns ``None`` if the row is entirely blank (e.g. trailing newline).

        Args:
            row:                The raw csv.DictReader row.
            normalised_headers: Mapping of lowercased header → original header.

        Returns:
            A :class:`ServiceEntry`, or ``None`` for blank rows.
        """
        # Check for completely empty rows
        values = list(row.values())
        if all(not (v or "").strip() for v in values):
            return None

        def _get(column_key: str) -> str:
            """Fetch cell value by the lowercased column key."""
            original_header = normalised_headers[column_key]
            return (row.get(original_header) or "").strip()

        entry = ServiceEntry(
            namespace=_get("namespace"),
            team=_get("team"),
            service_name=_get("servicename"),
            team_size=_get("team size"),
            team_members=_get("team members"),
            language=_get("language"),
            uri_location=_get("uri location if present"),
            sub_team=_get("sub team"),
            db_service=_get("db service"),
        )

        # Warn immediately about "service not found" URIs so the caller
        # doesn't have to separately inspect every entry.
        if _SERVICE_NOT_FOUND in entry.uri_location.lower():
            logger.warning(
                "Service '%s' (team: %s) has URI location '%s' — will be "
                "excluded from repository path resolution.",
                entry.service_name,
                entry.team,
                entry.uri_location,
            )

        return entry
