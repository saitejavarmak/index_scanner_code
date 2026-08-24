"""Team-level scanner that uses the service catalog to auto-discover repositories
and run the appropriate database guardrails per service.

Requirements: 12.2, 12.3, 12.4, 12.5, 12.6, 12.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from index_scanner_mcp.pg.config_loader import ConfigLoader, GuardrailConfig
from index_scanner_mcp.pg.engine import PostgresGuardrailEngine
from index_scanner_mcp.pg.gate_decision import GateDecisionEvaluator
from index_scanner_mcp.pg.models import GateDecision, Violation
from index_scanner_mcp.pg.service_catalog import ServiceCatalog, ServiceEntry

logger = logging.getLogger(__name__)


@dataclass
class TeamScanResult:
    """Aggregated scan results for all services belonging to a team.

    Attributes:
        team_name:             The name of the scanned team.
        total_services:        Total number of services found in the catalog for
                               this team.
        scanned_services:      Number of services that were actually scanned
                               (had a valid repo path and a supported DB type).
        skipped_services:      Number of services skipped because their URI
                               location was "service not found" or their DB type
                               is "None" / empty.
        postgres_services:     Service names for which PostgreSQL guardrails were
                               executed.
        mongodb_services:      Service names for which the MongoDB scanner was
                               executed.
        violations_by_service: Mapping of service_name → list of Violation
                               objects found for that service.
        all_violations:        Flat list of every Violation across all services.
        gate_decision:         Combined pass/fail decision across all violations,
                               or ``None`` when no services were scanned.
        errors:                Human-readable error messages collected during
                               scanning (one entry per service/exception).
    """

    team_name: str
    total_services: int
    scanned_services: int
    skipped_services: int
    postgres_services: list[str] = field(default_factory=list)
    mongodb_services: list[str] = field(default_factory=list)
    violations_by_service: dict[str, list[Violation]] = field(default_factory=dict)
    all_violations: list[Violation] = field(default_factory=list)
    gate_decision: GateDecision | None = None
    errors: list[str] = field(default_factory=list)


class TeamScanner:
    """Scan all services belonging to a team using the service catalog.

    The scanner:
    1. Loads the service catalog from *catalog_path*.
    2. Filters it to the services owned by *team_name*.
    3. For each service:
       - Resolves its repository path from the catalog.
       - Runs PostgreSQL guardrails when DB Service includes "PostgreSQL".
       - Runs the MongoDB scanner when DB Service includes "MongoDB".
    4. Aggregates all violations and computes a combined
       :class:`~index_scanner_mcp.pg.models.GateDecision`.

    Example usage::

        scanner = TeamScanner("platform-team", "/path/to/catalog.csv")
        result = scanner.scan()
        print(result.gate_decision)

    Args:
        team_name:    Name of the team to scan (case-insensitive match against
                      the catalog's ``Team`` column).
        catalog_path: Path to the service catalog CSV file.
        config:       Optional :class:`GuardrailConfig` to use for PostgreSQL
                      guardrails.  When ``None`` the default configuration is
                      loaded via :class:`ConfigLoader`.
    """

    def __init__(
        self,
        team_name: str,
        catalog_path: str,
        config: GuardrailConfig | None = None,
    ) -> None:
        self._team_name = team_name
        self._catalog_path = catalog_path
        self._config = config or ConfigLoader().load()
        self._catalog = ServiceCatalog(catalog_path)
        self._pg_engine = PostgresGuardrailEngine(self._config)
        self._gate_evaluator = GateDecisionEvaluator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> TeamScanResult:
        """Run guardrails for all services belonging to the configured team.

        Returns:
            A :class:`TeamScanResult` with per-service and aggregated results.
        """
        team_services = self._catalog.filter_by_team(self._team_name)

        total_services = len(team_services)
        scanned_services = 0
        skipped_services = 0
        postgres_services: list[str] = []
        mongodb_services: list[str] = []
        violations_by_service: dict[str, list[Violation]] = {}
        all_violations: list[Violation] = []
        errors: list[str] = []

        for service in team_services:
            # Resolve repo path — skip if not found
            repo_paths = self._catalog.get_repo_paths([service])
            if not repo_paths:
                logger.warning(
                    "Skipping service '%s' — no valid repo path found.",
                    service.service_name,
                )
                skipped_services += 1
                continue

            db_service = service.db_service.strip().lower()

            # Skip services with no DB type
            if not db_service or db_service == "none":
                logger.info(
                    "Skipping service '%s' — DB Service is '%s'.",
                    service.service_name,
                    service.db_service,
                )
                skipped_services += 1
                continue

            repo_path = repo_paths[0]
            service_violations: list[Violation] = []
            service_errors: list[str] = []

            has_postgres = ServiceCatalog.has_postgres(service.db_service)
            has_mongo = ServiceCatalog.has_mongodb(service.db_service)

            # PostgreSQL guardrails
            if has_postgres:
                pg_violations, pg_errors = self._scan_postgres_service(service)
                service_violations.extend(pg_violations)
                service_errors.extend(pg_errors)
                postgres_services.append(service.service_name)

            # MongoDB scanner
            if has_mongo:
                mongo_violations, mongo_errors = self._scan_mongodb_service(service)
                service_violations.extend(mongo_violations)
                service_errors.extend(mongo_errors)
                mongodb_services.append(service.service_name)

            scanned_services += 1
            violations_by_service[service.service_name] = service_violations
            all_violations.extend(service_violations)
            errors.extend(service_errors)

        # Compute combined gate decision when at least one service was scanned
        gate_decision: GateDecision | None = None
        if scanned_services > 0:
            gate_decision = self._gate_evaluator.evaluate(all_violations)

        return TeamScanResult(
            team_name=self._team_name,
            total_services=total_services,
            scanned_services=scanned_services,
            skipped_services=skipped_services,
            postgres_services=postgres_services,
            mongodb_services=mongodb_services,
            violations_by_service=violations_by_service,
            all_violations=all_violations,
            gate_decision=gate_decision,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_postgres_service(
        self, service: ServiceEntry
    ) -> tuple[list[Violation], list[str]]:
        """Run PostgreSQL guardrails on a single service.

        Args:
            service: The service entry from the catalog.

        Returns:
            A tuple of ``(violations, errors)`` collected for this service.
        """
        violations: list[Violation] = []
        errors: list[str] = []

        repo_paths = self._catalog.get_repo_paths([service])
        if not repo_paths:
            errors.append(
                f"[{service.service_name}] No valid repo path for PostgreSQL scan."
            )
            return violations, errors

        repo_path = repo_paths[0]
        try:
            result = self._pg_engine.run_analysis(repo_path)
            violations.extend(result.violations)
            errors.extend(
                f"[{service.service_name}] {e}" for e in result.errors
            )
        except Exception as exc:
            msg = (
                f"[{service.service_name}] PostgreSQL guardrail scan failed "
                f"for path '{repo_path}': {exc}"
            )
            logger.error(msg, exc_info=True)
            errors.append(msg)

        return violations, errors

    def _scan_mongodb_service(
        self, service: ServiceEntry
    ) -> tuple[list[Violation], list[str]]:
        """Run the MongoDB scanner on a single service.

        The MongoDB scanner (``ScannerEngine``) produces ``ScanResult``
        objects rather than ``Violation`` objects, so no violations are
        returned in the PG sense.  Errors and warnings are collected and
        surfaced as error strings so callers are aware of any scan issues.

        If the MongoDB scanner is unavailable or raises an unexpected
        exception, the failure is logged as a warning and the scan is
        skipped gracefully.

        Args:
            service: The service entry from the catalog.

        Returns:
            A tuple of ``(violations, errors)``.  The violations list is
            always empty because the MongoDB scanner uses its own result
            model; scan errors are captured in the errors list.
        """
        violations: list[Violation] = []
        errors: list[str] = []

        repo_paths = self._catalog.get_repo_paths([service])
        if not repo_paths:
            errors.append(
                f"[{service.service_name}] No valid repo path for MongoDB scan."
            )
            return violations, errors

        repo_path = repo_paths[0]

        try:
            from index_scanner_mcp.scanner_engine import ScannerEngine  # type: ignore[import]

            mongo_scanner = ScannerEngine()
            scan_result = mongo_scanner.scan_project(repo_path)

            # Surface any scanner-level errors as warnings
            for err in scan_result.errors:
                errors.append(f"[{service.service_name}] MongoDB scanner: {err}")

            logger.info(
                "MongoDB scan complete for service '%s': %d index(es) found, "
                "%d suggestion(s), %d file(s) scanned.",
                service.service_name,
                len(scan_result.indexes),
                len(scan_result.suggestions),
                scan_result.files_scanned,
            )

        except ImportError:
            logger.warning(
                "MongoDB ScannerEngine not available; skipping MongoDB scan "
                "for service '%s'.",
                service.service_name,
            )
        except Exception as exc:
            msg = (
                f"[{service.service_name}] MongoDB scan failed for path "
                f"'{repo_path}': {exc}"
            )
            logger.warning(msg, exc_info=True)
            errors.append(msg)

        return violations, errors
