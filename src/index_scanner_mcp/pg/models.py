"""Data models for the PostgreSQL Guardrails system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    """Severity levels for guardrail violations."""

    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"


class Action(Enum):
    """Actions to take when a violation is detected."""

    BLOCK_PR = "Block PR"
    WARN = "Warn"
    AUTO_FIX = "Auto-Fix"


class ViolationCategory(Enum):
    """Categories of guardrail violations."""

    MIGRATION = "Migration"
    SCHEMA = "Schema"
    INDEX = "Index"
    PERFORMANCE = "Performance"
    APPLICATION_CODE = "Application Code"
    BLOCKED_OPERATIONS = "Blocked Operations"


# Valid index types for PostgreSQL
VALID_INDEX_TYPES = {"btree", "gin", "gist", "brin", "hash"}

# Valid SQL query types
VALID_QUERY_TYPES = {"SELECT", "INSERT", "UPDATE", "DELETE"}


@dataclass
class Violation:
    """A single guardrail violation detected during analysis."""

    rule_id: str
    category: ViolationCategory
    severity: Severity
    action: Action
    file_path: str
    line_number: int
    description: str
    remediation: str
    auto_fix_sql: str | None = None
    explain_output: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError("Violation.rule_id must be non-empty")
        if not self.file_path:
            raise ValueError("Violation.file_path must be non-empty")
        if self.line_number < 0:
            raise ValueError("Violation.line_number must be non-negative")
        if not isinstance(self.category, ViolationCategory):
            raise ValueError(
                "Violation.category must be a ViolationCategory enum member"
            )
        if not isinstance(self.severity, Severity):
            raise ValueError("Violation.severity must be a Severity enum member")
        if not isinstance(self.action, Action):
            raise ValueError("Violation.action must be an Action enum member")
        if not self.description:
            raise ValueError("Violation.description must be non-empty")
        if not self.remediation:
            raise ValueError("Violation.remediation must be non-empty")


@dataclass
class GateDecision:
    """The final pass/fail outcome of a guardrail scan."""

    passed: bool
    total_violations: int
    critical_count: int
    high_count: int
    medium_count: int
    blocking_violations: list[Violation] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.total_violations < 0:
            raise ValueError("GateDecision.total_violations must be non-negative")
        if self.critical_count < 0:
            raise ValueError("GateDecision.critical_count must be non-negative")
        if self.high_count < 0:
            raise ValueError("GateDecision.high_count must be non-negative")
        if self.medium_count < 0:
            raise ValueError("GateDecision.medium_count must be non-negative")
        if (self.critical_count + self.high_count + self.medium_count) > self.total_violations:
            raise ValueError(
                "GateDecision severity counts must not exceed total_violations"
            )


@dataclass
class GuardrailResult:
    """Aggregated results from a complete guardrail analysis run."""

    project_path: str
    violations: list[Violation] = field(default_factory=list)
    gate_decision: GateDecision | None = None
    files_scanned: int = 0
    migration_files_scanned: int = 0
    java_files_scanned: int = 0
    runtime_checks_performed: bool = False
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.project_path:
            raise ValueError("GuardrailResult.project_path must be non-empty")
        if self.files_scanned < 0:
            raise ValueError("GuardrailResult.files_scanned must be non-negative")
        if self.migration_files_scanned < 0:
            raise ValueError(
                "GuardrailResult.migration_files_scanned must be non-negative"
            )
        if self.java_files_scanned < 0:
            raise ValueError(
                "GuardrailResult.java_files_scanned must be non-negative"
            )


@dataclass
class ColumnDef:
    """A column definition within a CREATE TABLE."""

    name: str
    data_type: str
    nullable: bool = True
    has_default: bool = False
    is_primary_key: bool = False
    is_unique: bool = False
    check_constraint: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ColumnDef.name must be non-empty")
        if not self.data_type:
            raise ValueError("ColumnDef.data_type must be non-empty")


@dataclass
class PgIndex:
    """A PostgreSQL index definition."""

    name: str
    table_name: str
    columns: list[str]
    unique: bool = False
    index_type: str = "btree"
    is_partial: bool = False
    where_clause: str | None = None
    include_columns: list[str] = field(default_factory=list)
    concurrently: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PgIndex.name must be non-empty")
        if not self.table_name:
            raise ValueError("PgIndex.table_name must be non-empty")
        if not self.columns:
            raise ValueError("PgIndex.columns must have at least one entry")
        if self.index_type not in VALID_INDEX_TYPES:
            raise ValueError(
                f"PgIndex.index_type must be one of {VALID_INDEX_TYPES}, "
                f"got '{self.index_type}'"
            )


@dataclass
class ForeignKey:
    """A foreign key constraint definition."""

    constraint_name: str | None
    source_table: str
    source_columns: list[str]
    target_table: str
    target_columns: list[str]

    def __post_init__(self) -> None:
        if not self.source_table:
            raise ValueError("ForeignKey.source_table must be non-empty")
        if not self.source_columns:
            raise ValueError(
                "ForeignKey.source_columns must have at least one entry"
            )
        if not self.target_table:
            raise ValueError("ForeignKey.target_table must be non-empty")
        if not self.target_columns:
            raise ValueError(
                "ForeignKey.target_columns must have at least one entry"
            )


@dataclass
class SQLStatement:
    """A parsed SQL statement from a migration file."""

    statement_type: str  # CREATE TABLE, ALTER TABLE, DROP TABLE, etc.
    raw_sql: str
    file_path: str
    line_number: int
    table_name: str | None = None
    columns: list[ColumnDef] = field(default_factory=list)
    indexes: list[PgIndex] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.statement_type:
            raise ValueError("SQLStatement.statement_type must be non-empty")
        if not self.raw_sql:
            raise ValueError("SQLStatement.raw_sql must be non-empty")
        if not self.file_path:
            raise ValueError("SQLStatement.file_path must be non-empty")
        if self.line_number < 0:
            raise ValueError("SQLStatement.line_number must be non-negative")


@dataclass
class TableDefinition:
    """A complete table schema parsed from SQL."""

    name: str
    columns: list[ColumnDef] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    indexes: list[PgIndex] = field(default_factory=list)
    file_path: str = ""
    line_number: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("TableDefinition.name must be non-empty")
        if self.line_number < 0:
            raise ValueError("TableDefinition.line_number must be non-negative")


@dataclass
class ExplainResult:
    """Result of an EXPLAIN ANALYZE query."""

    query: str
    plan_text: str
    execution_time_ms: float
    seq_scans: int = 0
    index_scans: int = 0
    estimated_rows: int = 0

    def __post_init__(self) -> None:
        if not self.query:
            raise ValueError("ExplainResult.query must be non-empty")
        if not self.plan_text:
            raise ValueError("ExplainResult.plan_text must be non-empty")
        if self.execution_time_ms < 0:
            raise ValueError(
                "ExplainResult.execution_time_ms must be non-negative"
            )
        if self.seq_scans < 0:
            raise ValueError("ExplainResult.seq_scans must be non-negative")
        if self.index_scans < 0:
            raise ValueError("ExplainResult.index_scans must be non-negative")
        if self.estimated_rows < 0:
            raise ValueError("ExplainResult.estimated_rows must be non-negative")


@dataclass
class UnusedIndex:
    """An index detected as unused via pg_stat_user_indexes."""

    index_name: str
    table_name: str
    index_size: str
    idx_scan: int = 0
    idx_tup_read: int = 0

    def __post_init__(self) -> None:
        if not self.index_name:
            raise ValueError("UnusedIndex.index_name must be non-empty")
        if not self.table_name:
            raise ValueError("UnusedIndex.table_name must be non-empty")
        if not self.index_size:
            raise ValueError("UnusedIndex.index_size must be non-empty")
        if self.idx_scan < 0:
            raise ValueError("UnusedIndex.idx_scan must be non-negative")
        if self.idx_tup_read < 0:
            raise ValueError("UnusedIndex.idx_tup_read must be non-negative")


@dataclass
class IndexSize:
    """Size information for an index."""

    index_name: str
    table_name: str
    size_bytes: int
    size_human: str

    def __post_init__(self) -> None:
        if not self.index_name:
            raise ValueError("IndexSize.index_name must be non-empty")
        if not self.table_name:
            raise ValueError("IndexSize.table_name must be non-empty")
        if self.size_bytes < 0:
            raise ValueError("IndexSize.size_bytes must be non-negative")
        if not self.size_human:
            raise ValueError("IndexSize.size_human must be non-empty")


@dataclass
class SQLQuery:
    """A parsed SQL query for performance analysis."""

    raw_sql: str
    file_path: str
    line_number: int
    query_type: str  # SELECT, INSERT, UPDATE, DELETE
    tables: list[str] = field(default_factory=list)
    where_columns: list[str] = field(default_factory=list)
    join_conditions: list[str] = field(default_factory=list)
    order_by_columns: list[str] = field(default_factory=list)
    has_where: bool = True
    has_limit: bool = False
    offset_value: int | None = None

    def __post_init__(self) -> None:
        if not self.raw_sql:
            raise ValueError("SQLQuery.raw_sql must be non-empty")
        if not self.file_path:
            raise ValueError("SQLQuery.file_path must be non-empty")
        if self.line_number < 0:
            raise ValueError("SQLQuery.line_number must be non-negative")
        if self.query_type not in VALID_QUERY_TYPES:
            raise ValueError(
                f"SQLQuery.query_type must be one of {VALID_QUERY_TYPES}, "
                f"got '{self.query_type}'"
            )
        if self.offset_value is not None and self.offset_value < 0:
            raise ValueError("SQLQuery.offset_value must be non-negative")
