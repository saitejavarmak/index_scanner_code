"""Data models for the Database Index Code Exporter."""

from __future__ import annotations

from dataclasses import dataclass, field

# Valid direction values for index fields
VALID_DIRECTIONS = {1, -1}
VALID_SPECIAL_DIRECTIONS = {"text", "hashed"}
VALID_SOURCE_TYPES = {"annotation", "programmatic", "query_suggestion"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_USAGE_TYPES = {"filter", "filter_equality", "filter_range", "sort", "projection"}


@dataclass
class IndexSource:
    """Tracks where an index definition was found in source code."""

    file: str
    line: int
    source_type: str  # "annotation", "programmatic", "query_suggestion"
    annotation: str | None = None
    context: str = ""

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("IndexSource.file must be non-empty")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"IndexSource.source_type must be one of {VALID_SOURCE_TYPES}, "
                f"got '{self.source_type}'"
            )


@dataclass
class IndexDefinition:
    """Represents a single MongoDB index extracted from source code."""

    collection: str
    fields: dict[str, int]
    name: str | None = None
    unique: bool = False
    sparse: bool = False
    expire_after_seconds: int | None = None
    index_type: str = "standard"
    source: IndexSource | None = None
    database: str | None = None

    def __post_init__(self) -> None:
        if not self.collection:
            raise ValueError("IndexDefinition.collection must be non-empty")
        if not self.fields:
            raise ValueError("IndexDefinition.fields must have at least one entry")
        for field_name, direction in self.fields.items():
            if isinstance(direction, str):
                if direction not in VALID_SPECIAL_DIRECTIONS:
                    raise ValueError(
                        f"Invalid direction '{direction}' for field '{field_name}'. "
                        f"String directions must be one of {VALID_SPECIAL_DIRECTIONS}"
                    )
            elif direction not in VALID_DIRECTIONS:
                raise ValueError(
                    f"Invalid direction {direction} for field '{field_name}'. "
                    f"Must be 1 (ascending) or -1 (descending)"
                )


@dataclass
class IndexSuggestion:
    """Represents a suggested index derived from query pattern analysis."""

    collection: str
    fields: dict[str, int]
    priority: str  # "high", "medium", "low"
    rationale: str
    operations: list[str] = field(default_factory=list)
    reference_count: int = 0
    sample_locations: list[str] = field(default_factory=list)
    database: str | None = None

    def __post_init__(self) -> None:
        if not self.collection:
            raise ValueError("IndexSuggestion.collection must be non-empty")
        if not self.fields:
            raise ValueError("IndexSuggestion.fields must have at least one entry")
        if self.priority not in VALID_PRIORITIES:
            raise ValueError(
                f"IndexSuggestion.priority must be one of {VALID_PRIORITIES}, "
                f"got '{self.priority}'"
            )


@dataclass
class ScanResult:
    """Aggregates all results from scanning a project."""

    project_path: str
    indexes: list[IndexDefinition] = field(default_factory=list)
    suggestions: list[IndexSuggestion] = field(default_factory=list)
    constants_resolved: int = 0
    files_scanned: int = 0
    errors: list[str] = field(default_factory=list)
    database_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.project_path:
            raise ValueError("ScanResult.project_path must be non-empty")


@dataclass
class FieldUsage:
    """Tracks how a field is used in a MongoDB query pattern."""

    field: str
    collection: str
    usage_type: str  # "filter", "sort", "projection"
    operation: str
    file: str
    line: int

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("FieldUsage.field must be non-empty")
        if not self.collection:
            raise ValueError("FieldUsage.collection must be non-empty")
        if self.usage_type not in VALID_USAGE_TYPES:
            raise ValueError(
                f"FieldUsage.usage_type must be one of {VALID_USAGE_TYPES}, "
                f"got '{self.usage_type}'"
            )
