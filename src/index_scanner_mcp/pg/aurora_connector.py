"""Aurora PostgreSQL connector for runtime validation.

Provides the AuroraConnector class which connects to an AWS Aurora
PostgreSQL instance to execute EXPLAIN ANALYZE plans, detect unused
indexes via pg_stat_user_indexes, and query index sizes.

The connector is optional — if psycopg2 is not installed, an ImportError
is raised with installation instructions.

Usage::

    from index_scanner_mcp.pg.config_loader import AuroraConnectionConfig
    from index_scanner_mcp.pg.aurora_connector import AuroraConnector

    config = AuroraConnectionConfig(host="my-aurora.cluster.us-east-1.rds.amazonaws.com")
    connector = AuroraConnector(config)

    with connector.session() as conn:
        unused = conn.get_unused_indexes()
        sizes  = conn.get_index_sizes()
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

from index_scanner_mcp.pg.config_loader import AuroraConnectionConfig
from index_scanner_mcp.pg.models import ExplainResult, IndexSize, UnusedIndex

if TYPE_CHECKING:
    # Only for type-checking; actual import is deferred.
    import psycopg2  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL used to query runtime state
# ---------------------------------------------------------------------------

_UNUSED_INDEXES_SQL = """
SELECT
    indexrelname  AS index_name,
    relname       AS table_name,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
    idx_scan,
    idx_tup_read
FROM pg_stat_user_indexes i
WHERE idx_scan = 0
ORDER BY pg_relation_size(i.indexrelid) DESC
"""

_INDEX_SIZES_SQL = """
SELECT
    indexname                                                   AS index_name,
    tablename                                                   AS table_name,
    pg_relation_size(schemaname || '.' || indexname)            AS size_bytes,
    pg_size_pretty(pg_relation_size(schemaname || '.' || indexname)) AS size_human
FROM pg_indexes
ORDER BY pg_relation_size(schemaname || '.' || indexname) DESC NULLS LAST
"""

# Regex patterns used to extract runtime info from EXPLAIN ANALYZE text output
_RE_EXECUTION_TIME = re.compile(
    r"Execution\s+(?:Time|time):\s+([\d.]+)\s+ms", re.IGNORECASE
)
_RE_SEQ_SCAN = re.compile(r"\bSeq Scan\b", re.IGNORECASE)
_RE_INDEX_SCAN = re.compile(r"\bIndex(?:Only)?\s+Scan\b", re.IGNORECASE)
_RE_ROWS = re.compile(r"rows=(\d+)", re.IGNORECASE)


def _require_psycopg2():
    """Import psycopg2, raising a clear ImportError if it is not installed."""
    try:
        import psycopg2  # noqa: PLC0415

        return psycopg2
    except ImportError as exc:
        raise ImportError(
            "psycopg2 is required for Aurora runtime validation but is not installed. "
            "Install it with:\n"
            "    pip install psycopg2-binary\n"
            "or add 'psycopg2-binary' to your project's optional dependencies."
        ) from exc


class AuroraConnector:
    """Connect to an Aurora PostgreSQL instance for runtime validation.

    Supports:
    - Executing EXPLAIN ANALYZE on arbitrary queries
    - Querying pg_stat_user_indexes for unused index detection
    - Querying pg_indexes for index size information
    - Context manager (``session()``) for safe connection lifecycle

    Connection errors are handled gracefully:
    - Timeout / operational errors → logged as warning, re-raised as
      ``AuroraConnectionError``
    - Authentication failure → logged as error, re-raised as
      ``AuroraConnectionError``
    - SSL certificate error → logged as warning, re-raised as
      ``AuroraConnectionError``

    Args:
        config: An :class:`~index_scanner_mcp.pg.config_loader.AuroraConnectionConfig`
            instance containing connection parameters.
    """

    def __init__(self, config: AuroraConnectionConfig) -> None:
        self._config = config
        self._connection = None  # set by connect()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a connection to the Aurora PostgreSQL instance.

        Reads the password from the environment variable specified by
        ``config.password_env_var`` (default: ``AURORA_PG_PASSWORD``).

        Raises:
            ImportError: If psycopg2 is not installed.
            AuroraConnectionError: If the connection attempt fails for any
                reason (timeout, authentication, SSL, etc.).
        """
        psycopg2 = _require_psycopg2()

        password = self._config.get_password()

        # Build connection keyword arguments
        connect_kwargs: dict = {
            "host": self._config.host,
            "port": self._config.port,
            "dbname": self._config.database,
            "user": self._config.username,
            "connect_timeout": 10,
            "sslmode": self._config.ssl_mode,
        }
        if password is not None:
            connect_kwargs["password"] = password

        logger.debug(
            "Connecting to Aurora PostgreSQL at %s:%s/%s as %s",
            self._config.host,
            self._config.port,
            self._config.database,
            self._config.username,
        )

        try:
            self._connection = psycopg2.connect(**connect_kwargs)
            # Use autocommit so EXPLAIN ANALYZE doesn't open a transaction
            self._connection.autocommit = True
            logger.info(
                "Connected to Aurora PostgreSQL at %s:%s",
                self._config.host,
                self._config.port,
            )
        except Exception as exc:
            self._connection = None
            self._handle_connection_error(exc)

    def disconnect(self) -> None:
        """Close the current database connection if open."""
        if self._connection is not None:
            try:
                self._connection.close()
                logger.info(
                    "Disconnected from Aurora PostgreSQL at %s:%s",
                    self._config.host,
                    self._config.port,
                )
            except Exception:
                # Best-effort close — ignore errors on teardown
                pass
            finally:
                self._connection = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    @contextmanager
    def session(self) -> Generator[AuroraConnector, None, None]:
        """Context manager that handles connect/disconnect lifecycle.

        On entry the connector is connected; on exit (including on error)
        the connection is closed.  Connection errors raised during ``connect()``
        are propagated to the caller.

        Yields:
            This ``AuroraConnector`` instance (already connected).

        Example::

            with connector.session() as conn:
                unused = conn.get_unused_indexes()
        """
        try:
            self.connect()
            yield self
        except AuroraConnectionError:
            # Re-raise connection errors so callers can decide to skip
            raise
        except Exception:
            # Unexpected runtime error — still disconnect cleanly
            raise
        finally:
            self.disconnect()

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def execute_explain(self, query: str) -> ExplainResult:
        """Run EXPLAIN (ANALYZE, FORMAT TEXT) on *query* and return parsed results.

        Args:
            query: A SQL SELECT query to explain.  The query is wrapped in
                ``EXPLAIN (ANALYZE, FORMAT TEXT) ...`` before execution.

        Returns:
            An :class:`~index_scanner_mcp.pg.models.ExplainResult` with the
            plan text and extracted metrics.

        Raises:
            AuroraConnectionError: If not connected or the query fails.
        """
        self._assert_connected()

        explain_sql = f"EXPLAIN (ANALYZE, FORMAT TEXT) {query}"
        logger.debug("Running EXPLAIN ANALYZE on query: %.100s…", query)

        try:
            with self._connection.cursor() as cur:
                cur.execute(explain_sql)
                rows = cur.fetchall()
        except Exception as exc:
            raise AuroraConnectionError(
                f"EXPLAIN ANALYZE execution failed: {exc}"
            ) from exc

        # pg returns one row per plan line; join them into a single text block
        plan_text = "\n".join(row[0] for row in rows)

        return self._parse_explain_output(query, plan_text)

    def get_unused_indexes(self) -> list[UnusedIndex]:
        """Query pg_stat_user_indexes for indexes with zero scans.

        Returns:
            A list of :class:`~index_scanner_mcp.pg.models.UnusedIndex`
            objects, sorted by index size (largest first).

        Raises:
            AuroraConnectionError: If not connected or the query fails.
        """
        self._assert_connected()

        logger.debug("Querying pg_stat_user_indexes for unused indexes.")

        try:
            with self._connection.cursor() as cur:
                cur.execute(_UNUSED_INDEXES_SQL)
                rows = cur.fetchall()
        except Exception as exc:
            raise AuroraConnectionError(
                f"Failed to query pg_stat_user_indexes: {exc}"
            ) from exc

        unused: list[UnusedIndex] = []
        for row in rows:
            index_name, table_name, index_size, idx_scan, idx_tup_read = row
            unused.append(
                UnusedIndex(
                    index_name=index_name,
                    table_name=table_name,
                    index_size=index_size or "0 bytes",
                    idx_scan=idx_scan or 0,
                    idx_tup_read=idx_tup_read or 0,
                )
            )

        logger.info("Found %d unused indexes.", len(unused))
        return unused

    def get_index_sizes(self) -> list[IndexSize]:
        """Query pg_indexes for index size information.

        Returns:
            A list of :class:`~index_scanner_mcp.pg.models.IndexSize`
            objects, sorted by size (largest first).  Rows where the size
            cannot be computed (NULL size_bytes) are skipped.

        Raises:
            AuroraConnectionError: If not connected or the query fails.
        """
        self._assert_connected()

        logger.debug("Querying pg_indexes for index sizes.")

        try:
            with self._connection.cursor() as cur:
                cur.execute(_INDEX_SIZES_SQL)
                rows = cur.fetchall()
        except Exception as exc:
            raise AuroraConnectionError(
                f"Failed to query index sizes: {exc}"
            ) from exc

        sizes: list[IndexSize] = []
        for row in rows:
            index_name, table_name, size_bytes, size_human = row
            if size_bytes is None:
                # Index may live in a different schema or be unresolvable;
                # skip rather than fail.
                logger.debug(
                    "Skipping index '%s' on table '%s': size is NULL.",
                    index_name,
                    table_name,
                )
                continue
            sizes.append(
                IndexSize(
                    index_name=index_name,
                    table_name=table_name,
                    size_bytes=int(size_bytes),
                    size_human=size_human or "0 bytes",
                )
            )

        logger.info("Retrieved sizes for %d indexes.", len(sizes))
        return sizes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_connected(self) -> None:
        """Raise AuroraConnectionError if there is no active connection."""
        if self._connection is None:
            raise AuroraConnectionError(
                "Not connected. Call connect() or use the session() context manager "
                "before executing queries."
            )

    def _handle_connection_error(self, exc: Exception) -> None:
        """Translate psycopg2 / OS errors into AuroraConnectionError.

        Different error types produce different log levels:
        - Timeout / operational errors → warning + skip guidance
        - Authentication failures → error + credential guidance
        - SSL certificate errors → warning + SSL guidance
        - Other errors → error

        Raises:
            AuroraConnectionError: Always — wraps the original exception.
        """
        exc_str = str(exc).lower()
        exc_type = type(exc).__name__

        # Determine error category from message content or type name
        is_timeout = (
            "timeout" in exc_str
            or "timed out" in exc_str
            or "connection refused" in exc_str
            or "could not connect" in exc_str
            or "OperationalError" in exc_type
        )
        is_auth_failure = (
            "password authentication failed" in exc_str
            or "authentication failed" in exc_str
            or "role" in exc_str and "does not exist" in exc_str
        )
        is_ssl_error = (
            "ssl" in exc_str
            or "certificate" in exc_str
            or "sslmode" in exc_str
        )

        if is_auth_failure:
            logger.error(
                "Aurora authentication failed for user '%s' at %s:%s. "
                "Check that the '%s' environment variable contains the correct password "
                "and that the user has login privileges. Error: %s",
                self._config.username,
                self._config.host,
                self._config.port,
                self._config.password_env_var,
                exc,
            )
        elif is_ssl_error:
            logger.warning(
                "Aurora SSL/certificate error connecting to %s:%s. "
                "Runtime validation will be skipped. "
                "Verify ssl_mode='%s' matches the server certificate configuration. "
                "Error: %s",
                self._config.host,
                self._config.port,
                self._config.ssl_mode,
                exc,
            )
        elif is_timeout:
            logger.warning(
                "Aurora connection to %s:%s timed out. "
                "Runtime validation will be skipped. "
                "Verify the host is reachable and the security group allows port %s. "
                "Error: %s",
                self._config.host,
                self._config.port,
                self._config.port,
                exc,
            )
        else:
            logger.error(
                "Unexpected Aurora connection error to %s:%s: %s",
                self._config.host,
                self._config.port,
                exc,
            )

        raise AuroraConnectionError(str(exc)) from exc

    @staticmethod
    def _parse_explain_output(query: str, plan_text: str) -> ExplainResult:
        """Extract metrics from a TEXT-format EXPLAIN ANALYZE output.

        Args:
            query: The original SQL query (stored in the result).
            plan_text: The raw text output from EXPLAIN ANALYZE.

        Returns:
            An :class:`~index_scanner_mcp.pg.models.ExplainResult` with
            extracted execution time, scan counts, and row estimates.
        """
        # Execution time (ms)
        execution_time_ms = 0.0
        m = _RE_EXECUTION_TIME.search(plan_text)
        if m:
            try:
                execution_time_ms = float(m.group(1))
            except ValueError:
                pass

        # Count sequential scans and index scans in plan
        seq_scans = len(_RE_SEQ_SCAN.findall(plan_text))
        index_scans = len(_RE_INDEX_SCAN.findall(plan_text))

        # Estimate rows from first "rows=N" occurrence (top-level node)
        estimated_rows = 0
        m_rows = _RE_ROWS.search(plan_text)
        if m_rows:
            try:
                estimated_rows = int(m_rows.group(1))
            except ValueError:
                pass

        return ExplainResult(
            query=query,
            plan_text=plan_text,
            execution_time_ms=execution_time_ms,
            seq_scans=seq_scans,
            index_scans=index_scans,
            estimated_rows=estimated_rows,
        )


class AuroraConnectionError(RuntimeError):
    """Raised when an Aurora PostgreSQL connection or query fails.

    Callers that wish to skip runtime validations gracefully should catch
    this exception::

        try:
            with connector.session() as conn:
                unused = conn.get_unused_indexes()
        except AuroraConnectionError:
            logger.warning("Skipping runtime validation: %s", exc)
    """
