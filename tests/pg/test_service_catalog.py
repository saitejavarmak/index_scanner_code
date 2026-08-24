"""Unit tests for ServiceCatalog and ServiceEntry.

Requirements covered: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.10
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from index_scanner_mcp.pg.service_catalog import ServiceCatalog, ServiceEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CSV_HEADER = (
    "Namespace,Team,ServiceName,Team Size,Team Members,Language,"
    "URI location if present,Sub Team,DB Service\n"
)


def _write_csv(tmp_path: Path, body: str) -> str:
    """Write a CSV file with the standard header and return its path."""
    p = tmp_path / "catalog.csv"
    p.write_text(_CSV_HEADER + textwrap.dedent(body), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Requirement 12.1 – Load from CSV with correct columns
# ---------------------------------------------------------------------------


class TestLoad:
    def test_load_single_row(self, tmp_path):
        path = _write_csv(
            tmp_path,
            """\
            ns1,team-a,svc-auth,3,alice bob charlie,Java,/repos/svc-auth,,PostgreSQL
            """,
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()

        assert len(entries) == 1
        e = entries[0]
        assert e.namespace == "ns1"
        assert e.team == "team-a"
        assert e.service_name == "svc-auth"
        assert e.team_size == "3"
        assert e.team_members == "alice bob charlie"
        assert e.language == "Java"
        assert e.uri_location == "/repos/svc-auth"
        assert e.sub_team == ""
        assert e.db_service == "PostgreSQL"

    def test_load_multiple_rows(self, tmp_path):
        path = _write_csv(
            tmp_path,
            """\
            ns1,team-a,svc-auth,3,,Java,/repos/svc-auth,,PostgreSQL
            ns2,team-b,svc-data,5,,Python,/repos/svc-data,,MongoDB
            ns3,team-c,svc-both,2,,Go,/repos/svc-both,,MongoDB, PostgreSQL
            """,
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()
        assert len(entries) == 3

    def test_load_empty_file(self, tmp_path):
        p = tmp_path / "catalog.csv"
        p.write_text("", encoding="utf-8")
        catalog = ServiceCatalog(str(p))
        entries = catalog.load()
        assert entries == []

    def test_load_header_only(self, tmp_path):
        p = tmp_path / "catalog.csv"
        p.write_text(_CSV_HEADER, encoding="utf-8")
        catalog = ServiceCatalog(str(p))
        entries = catalog.load()
        assert entries == []

    def test_missing_required_column_raises(self, tmp_path):
        p = tmp_path / "catalog.csv"
        # omit "DB Service" column
        p.write_text(
            "Namespace,Team,ServiceName,Team Size,Team Members,Language,"
            "URI location if present,Sub Team\n"
            "ns1,team-a,svc,3,,Java,/repos,,\n",
            encoding="utf-8",
        )
        catalog = ServiceCatalog(str(p))
        with pytest.raises(ValueError, match="db service"):
            catalog.load()

    def test_file_not_found_raises(self, tmp_path):
        catalog = ServiceCatalog(str(tmp_path / "nonexistent.csv"))
        with pytest.raises(FileNotFoundError):
            catalog.load()

    def test_load_caches_entries(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1,team-a,svc,3,,Java,/repos/svc,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)
        first = catalog.load()
        second = catalog.load()
        assert first is second  # same list object returned from cache

    def test_sub_team_populated(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1,team-a,svc,3,,Java,/repos/svc,payments-subteam,PostgreSQL\n",
        )
        entries = ServiceCatalog(path).load()
        assert entries[0].sub_team == "payments-subteam"


# ---------------------------------------------------------------------------
# Requirement 12.2 & 12.9 – Filter by team / namespace / sub-team
# ---------------------------------------------------------------------------


class TestFilters:
    def _catalog(self, tmp_path: Path) -> ServiceCatalog:
        path = _write_csv(
            tmp_path,
            """\
            ns-a,alpha,svc-1,2,,Java,/repos/svc-1,frontend,PostgreSQL
            ns-a,alpha,svc-2,3,,Python,/repos/svc-2,backend,MongoDB
            ns-b,beta,svc-3,4,,Go,/repos/svc-3,backend,PostgreSQL
            ns-b,gamma,svc-4,1,,Rust,/repos/svc-4,,None
            """,
        )
        return ServiceCatalog(path)

    def test_filter_by_team(self, tmp_path):
        catalog = self._catalog(tmp_path)
        results = catalog.filter_by_team("alpha")
        assert len(results) == 2
        assert all(e.team == "alpha" for e in results)

    def test_filter_by_team_case_insensitive(self, tmp_path):
        catalog = self._catalog(tmp_path)
        assert len(catalog.filter_by_team("ALPHA")) == 2

    def test_filter_by_team_no_match(self, tmp_path):
        catalog = self._catalog(tmp_path)
        assert catalog.filter_by_team("unknown-team") == []

    def test_filter_by_namespace(self, tmp_path):
        catalog = self._catalog(tmp_path)
        results = catalog.filter_by_namespace("ns-a")
        assert len(results) == 2
        assert all(e.namespace == "ns-a" for e in results)

    def test_filter_by_namespace_case_insensitive(self, tmp_path):
        catalog = self._catalog(tmp_path)
        assert len(catalog.filter_by_namespace("NS-B")) == 2

    def test_filter_by_sub_team(self, tmp_path):
        catalog = self._catalog(tmp_path)
        results = catalog.filter_by_sub_team("backend")
        assert len(results) == 2
        assert all(e.sub_team == "backend" for e in results)

    def test_filter_by_sub_team_case_insensitive(self, tmp_path):
        catalog = self._catalog(tmp_path)
        assert len(catalog.filter_by_sub_team("FRONTEND")) == 1

    def test_filter_auto_loads(self, tmp_path):
        """Calling filter before load() should auto-load entries."""
        path = _write_csv(
            tmp_path,
            "ns1,team-a,svc,3,,Java,/repos/svc,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)
        # Do NOT call catalog.load() explicitly
        results = catalog.filter_by_team("team-a")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Requirements 12.4, 12.5, 12.6, 12.7 – DB type determination
# ---------------------------------------------------------------------------


class TestDbTypeFilters:
    @pytest.fixture()
    def services(self):
        return [
            ServiceEntry("", "t", "pg-svc", "", "", "", "/r1", "", "PostgreSQL"),
            ServiceEntry("", "t", "mg-svc", "", "", "", "/r2", "", "MongoDB"),
            ServiceEntry("", "t", "both-svc", "", "", "", "/r3", "", "MongoDB, PostgreSQL"),
            ServiceEntry("", "t", "both-rev", "", "", "", "/r4", "", "PostgreSQL, MongoDB"),
            ServiceEntry("", "t", "none-svc", "", "", "", "/r5", "", "None"),
            ServiceEntry("", "t", "empty-svc", "", "", "", "/r6", "", ""),
        ]

    def test_get_postgres_services(self, services):
        catalog = ServiceCatalog.__new__(ServiceCatalog)
        results = catalog.get_postgres_services(services)
        names = {e.service_name for e in results}
        assert names == {"pg-svc", "both-svc", "both-rev"}

    def test_get_mongodb_services(self, services):
        catalog = ServiceCatalog.__new__(ServiceCatalog)
        results = catalog.get_mongodb_services(services)
        names = {e.service_name for e in results}
        assert names == {"mg-svc", "both-svc", "both-rev"}

    def test_has_postgres_true(self):
        assert ServiceCatalog.has_postgres("PostgreSQL") is True
        assert ServiceCatalog.has_postgres("MongoDB, PostgreSQL") is True
        assert ServiceCatalog.has_postgres("POSTGRESQL") is True
        assert ServiceCatalog.has_postgres("postgresql") is True

    def test_has_postgres_false(self):
        assert ServiceCatalog.has_postgres("MongoDB") is False
        assert ServiceCatalog.has_postgres("None") is False
        assert ServiceCatalog.has_postgres("") is False

    def test_has_mongodb_true(self):
        assert ServiceCatalog.has_mongodb("MongoDB") is True
        assert ServiceCatalog.has_mongodb("MongoDB, PostgreSQL") is True
        assert ServiceCatalog.has_mongodb("MONGODB") is True

    def test_has_mongodb_false(self):
        assert ServiceCatalog.has_mongodb("PostgreSQL") is False
        assert ServiceCatalog.has_mongodb("None") is False
        assert ServiceCatalog.has_mongodb("") is False

    def test_none_db_excluded_from_both(self, services):
        catalog = ServiceCatalog.__new__(ServiceCatalog)
        pg = {e.service_name for e in catalog.get_postgres_services(services)}
        mg = {e.service_name for e in catalog.get_mongodb_services(services)}
        assert "none-svc" not in pg
        assert "none-svc" not in mg
        assert "empty-svc" not in pg
        assert "empty-svc" not in mg


# ---------------------------------------------------------------------------
# Requirements 12.3 & 12.10 – get_repo_paths / service not found
# ---------------------------------------------------------------------------


class TestGetRepoPaths:
    def test_returns_valid_paths(self, tmp_path):
        path = _write_csv(
            tmp_path,
            """\
            ns1,t,svc-1,1,,Java,/repos/svc-1,,PostgreSQL
            ns1,t,svc-2,1,,Java,/repos/svc-2,,PostgreSQL
            """,
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()
        paths = catalog.get_repo_paths(entries)
        assert paths == ["/repos/svc-1", "/repos/svc-2"]

    def test_skips_service_not_found(self, tmp_path, caplog):
        path = _write_csv(
            tmp_path,
            """\
            ns1,t,found-svc,1,,Java,/repos/found,,PostgreSQL
            ns1,t,missing-svc,1,,Java,service not found,,PostgreSQL
            """,
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()

        with caplog.at_level(logging.WARNING):
            paths = catalog.get_repo_paths(entries)

        assert paths == ["/repos/found"]
        assert any("missing-svc" in m for m in caplog.messages)

    def test_skips_service_not_found_case_insensitive(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1,t,svc,1,,Java,Service Not Found,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()
        paths = catalog.get_repo_paths(entries)
        assert paths == []

    def test_skips_empty_uri(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1,t,svc,1,,Java,,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)
        entries = catalog.load()
        paths = catalog.get_repo_paths(entries)
        assert paths == []

    def test_warning_logged_on_load_for_not_found(self, tmp_path, caplog):
        """Warning must be emitted at load time, not only at get_repo_paths time."""
        path = _write_csv(
            tmp_path,
            "ns1,t,missing-svc,1,,Java,service not found,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)

        with caplog.at_level(logging.WARNING):
            catalog.load()

        assert any("missing-svc" in m for m in caplog.messages)

    def test_empty_services_list_returns_empty(self, tmp_path):
        path = _write_csv(tmp_path, "")
        catalog = ServiceCatalog(path)
        assert catalog.get_repo_paths([]) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_whitespace_in_team_name_is_stripped(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1, team-a ,svc,1,,Java,/repos/svc,,PostgreSQL\n",
        )
        entries = ServiceCatalog(path).load()
        assert entries[0].team == "team-a"

    def test_filter_with_leading_trailing_whitespace_in_argument(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "ns1,team-a,svc,1,,Java,/repos/svc,,PostgreSQL\n",
        )
        catalog = ServiceCatalog(path)
        results = catalog.filter_by_team("  team-a  ")
        assert len(results) == 1

    def test_both_db_types_in_either_order(self):
        assert ServiceCatalog.has_postgres("MongoDB, PostgreSQL") is True
        assert ServiceCatalog.has_postgres("PostgreSQL, MongoDB") is True
        assert ServiceCatalog.has_mongodb("MongoDB, PostgreSQL") is True
        assert ServiceCatalog.has_mongodb("PostgreSQL, MongoDB") is True
