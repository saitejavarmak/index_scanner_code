"""Shared constants and compiled regex patterns for the index scanner."""

import re

# ---------------------------------------------------------------------------
# Directories to skip during recursive file discovery
# ---------------------------------------------------------------------------
SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    ".idea",
    ".vscode",
    "target",
    "build",
    "dist",
    ".gradle",
    "venv",
    ".env",
}

# ---------------------------------------------------------------------------
# File-extension → pattern-list mapping for index scanning
# ---------------------------------------------------------------------------

JAVA_INDEX_PATTERNS: list[tuple[str, str]] = [
    # MongoDB Spring Data annotations
    (r"@Indexed\b(?:\(([^)]*)\))?", "MongoDB @Indexed"),
    (r"@CompoundIndex\b(?:\(([^)]*)\))?", "MongoDB @CompoundIndex"),
    (r"@CompoundIndexes\b", "MongoDB @CompoundIndexes"),
    (r"@TextIndexed\b", "MongoDB @TextIndexed"),
    (r"@GeoSpatialIndexed\b", "MongoDB @GeoSpatialIndexed"),
    (r"@HashIndexed\b", "MongoDB @HashIndexed"),
    (r"@WildcardIndexed\b", "MongoDB @WildcardIndexed"),
    # Programmatic MongoDB index creation
    (r"\.createIndex\s*\(([^)]*)\)", "MongoDB createIndex()"),
    (r"\.ensureIndex\s*\(([^)]*)\)", "MongoDB ensureIndex()"),
    (r"Index\s*\(\s*\)\s*\.on\s*\(([^)]*)\)", "MongoDB Index().on()"),
    (r"new\s+Index\b", "MongoDB new Index"),
    (r"IndexOperations", "MongoDB IndexOperations"),
    # JPA / Hibernate
    (r"@Index\b(?:\(([^)]*)\))?", "JPA @Index"),
    (r"@Table\b[^)]*indexes\s*=", "JPA @Table indexes"),
    # SQL in code
    (r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", "SQL CREATE INDEX"),
    # Elasticsearch
    (r"@Setting\b.*index", "Elasticsearch @Setting"),
    (r"@Document\b.*index", "Elasticsearch @Document index"),
    (r"CreateIndexRequest", "Elasticsearch CreateIndexRequest"),
]

PYTHON_INDEX_PATTERNS: list[tuple[str, str]] = [
    (r"create_index\s*\(([^)]*)\)", "MongoDB create_index()"),
    (r"ensure_index\s*\(([^)]*)\)", "MongoDB ensure_index()"),
    (r"create_indexes\s*\(", "MongoDB create_indexes()"),
    (r"IndexModel\s*\(", "MongoDB IndexModel"),
    (r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", "SQL CREATE INDEX"),
    (r"db\..*\.createIndex\s*\(", "MongoDB shell createIndex"),
    (r"index\s*=\s*True", "ORM index=True"),
    (r"Index\s*\(", "Index definition"),
]

CONFIG_INDEX_PATTERNS: list[tuple[str, str]] = [
    (r"index[._-]", "Index config reference"),
    (r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", "SQL CREATE INDEX"),
    (r"ensureIndex", "MongoDB ensureIndex"),
    (r"createIndex", "MongoDB createIndex"),
]

SCAN_EXTENSIONS: dict[str, list[tuple[str, str]]] = {
    ".java": JAVA_INDEX_PATTERNS,
    ".kt": JAVA_INDEX_PATTERNS,
    ".py": PYTHON_INDEX_PATTERNS,
    ".sql": CONFIG_INDEX_PATTERNS,
    ".xml": CONFIG_INDEX_PATTERNS,
    ".yaml": CONFIG_INDEX_PATTERNS,
    ".yml": CONFIG_INDEX_PATTERNS,
    ".json": CONFIG_INDEX_PATTERNS,
    ".properties": CONFIG_INDEX_PATTERNS,
    ".conf": CONFIG_INDEX_PATTERNS,
    ".js": PYTHON_INDEX_PATTERNS,
    ".ts": PYTHON_INDEX_PATTERNS,
}

# ---------------------------------------------------------------------------
# Query field extraction patterns (used by QueryPatternAnalyzer)
# ---------------------------------------------------------------------------

QUERY_FIELD_PATTERNS_JAVA: list[tuple[str, str]] = [
    # --- Spring Data MongoDB Criteria (most common pattern) ---
    # Criteria.where("field").is(value) / .in(values) / .regex(...)
    (r'Criteria\.where\s*\(\s*"([^"]+)"', "filter_equality"),
    # .and("field") — additional criteria fields
    (r'\.and\s*\(\s*"([^"]+)"\s*\)', "filter_equality"),
    # Criteria with range operators
    (r'\.(?:gt|gte|lt|lte)\s*\(\s*"([^"]+)"', "filter_range"),
    # Sort.by("field") / Sort.by(Direction.ASC, "field")
    (r'Sort\.by\s*\([^)]*"([^"]+)"', "sort"),
    (r'Sort\.Order\.\w+\s*\(\s*"([^"]+)"', "sort"),
    # @Query annotation with field references
    (r"@Query\s*\(\s*[\"']\s*\{\s*['\"]([^'\"]+)['\"]", "filter"),

    # --- BasicDBObject / Document patterns ---
    # new BasicDBObject("fieldName", value)
    (r'new\s+BasicDBObject\s*\(\s*"([^"]+)"\s*,', "filter"),
    # new BasicDBObject(AppConstants.FIELD, value)
    (r"new\s+BasicDBObject\s*\(\s*AppConstants\.(\w+)\s*,", "filter_constant"),
    # new Document("fieldName", value)
    (r'new\s+Document\s*\(\s*"([^"]+)"\s*,', "filter"),
    # .append("fieldName", value)
    (r'\.append\s*\(\s*"([^"]+)"\s*,', "filter"),
    # .append(AppConstants.FIELD, value)
    (r"\.append\s*\(\s*AppConstants\.(\w+)\s*,", "filter_constant"),

    # --- Map.put patterns ---
    # queryMap.put("fieldName", value)
    (
        r'(?:queryMap|query|filterMap|filter|searchQuery|findQuery|criteria)\s*\.put\s*\(\s*"([^"]+)"\s*,',
        "filter",
    ),
    (
        r"(?:queryMap|query|filterMap|filter|searchQuery|findQuery|criteria)\s*\.put\s*\(\s*AppConstants\.(\w+)\s*,",
        "filter_constant",
    ),

    # --- Filters builder (MongoDB driver) ---
    # Filters.eq("field", val), Filters.in("field", val) — equality
    (r'Filters\.(?:eq|in|nin)\s*\(\s*"([^"]+)"', "filter_equality"),
    # Filters.gt/gte/lt/lte("field", val) — range
    (r'Filters\.(?:gt|gte|lt|lte)\s*\(\s*"([^"]+)"', "filter_range"),
    # Filters.regex, exists, etc. — general filter
    (r'Filters\.(?:regex|exists|ne|not|elemMatch|size|type|all)\s*\(\s*"([^"]+)"', "filter"),

    # --- Sort patterns ---
    # sort: new BasicDBObject("field", 1/-1) in sort context
    (r'\.sort\s*\(\s*new\s+BasicDBObject\s*\(\s*"([^"]+)"', "sort"),
    # Sorts.ascending/descending("field")
    (r'Sorts\.\w+\s*\(\s*"([^"]+)"', "sort"),

    # --- Aggregation markers ---
    (r"\$match", "agg_match_marker"),
    (r"\$sort", "agg_sort_marker"),
    (r"\$group", "agg_group_marker"),
]

QUERY_FIELD_PATTERNS_PYTHON: list[tuple[str, str]] = [
    # collection.find({"field": value})
    (r"[\"'](\w+)[\"']\s*:", "filter"),
    # create_index("field") or create_index([("field", 1)])
    (r"create_index\s*\(\s*[\"'](\w+)[\"']", "existing_index"),
    (r"create_index\s*\(\s*\[\s*\(\s*[\"'](\w+)[\"']", "existing_index"),
    # $match, $sort
    (r"\$match", "agg_match_marker"),
    (r"\$sort", "agg_sort_marker"),
]

# ---------------------------------------------------------------------------
# Operation detection patterns
# ---------------------------------------------------------------------------

OPERATION_PATTERNS_JAVA: list[tuple[str, str]] = [
    (r"\.find\s*\(", "find"),
    (r"\.findOne\s*\(", "find"),
    (r"\.findAll\s*\(", "find"),
    (r"mongoTemplate\.find\s*\(", "find"),
    (r"mongoTemplate\.findOne\s*\(", "find"),
    (r"mongoTemplate\.findAll\s*\(", "find"),
    (r"mongoTemplate\.remove\s*\(", "delete"),
    (r"mongoTemplate\.save\s*\(", "update"),
    (r"mongoTemplate\.upsert\s*\(", "update"),
    (r"mongoTemplate\.updateFirst\s*\(", "update"),
    (r"mongoTemplate\.updateMulti\s*\(", "update"),
    (r"mongoOperations\.find\s*\(", "find"),
    (r"mongoOperations\.findOne\s*\(", "find"),
    (r"\.aggregate\s*\(", "aggregate"),
    (r"\.updateOne\s*\(", "update"),
    (r"\.updateMany\s*\(", "update"),
    (r"\.UpdateOne\s*\(", "update"),
    (r"\.deleteMany\s*\(", "delete"),
    (r"\.deleteOne\s*\(", "delete"),
    (r"\.countDocuments\s*\(", "count"),
    (r"\.count\s*\(", "count"),
    (r"\.distinct\s*\(", "distinct"),
    (r"\.sort\s*\(", "sort"),
]

OPERATION_PATTERNS_PYTHON: list[tuple[str, str]] = [
    (r"\.find\s*\(", "find"),
    (r"\.find_one\s*\(", "find"),
    (r"\.aggregate\s*\(", "aggregate"),
    (r"\.update_one\s*\(", "update"),
    (r"\.update_many\s*\(", "update"),
    (r"\.delete_many\s*\(", "delete"),
    (r"\.delete_one\s*\(", "delete"),
    (r"\.count_documents\s*\(", "count"),
    (r"\.distinct\s*\(", "distinct"),
    (r"\.sort\s*\(", "sort"),
]

# ---------------------------------------------------------------------------
# Collection name detection patterns
# ---------------------------------------------------------------------------

COLLECTION_PATTERNS: list[tuple[str, str]] = [
    # Spring Data @Document annotation — collection name
    (r'@Document\s*\(\s*(?:collection\s*=\s*)?["\']([^"\']+)["\']', "direct"),
    # @Document with value parameter: @Document("collName")
    (r'@Document\s*\(\s*value\s*=\s*["\']([^"\']+)["\']', "direct"),
    # @Collection("name") — some frameworks
    (r'@Collection\s*\(\s*["\']([^"\']+)["\']', "direct"),
    # getCollection("name") or get_collection("name")
    (r'getCollection\s*\(\s*"([^"]+)"', "direct"),
    (r"getCollection\s*\(\s*AppConstants\.(\w+)", "constant"),
    (r"get_collection\s*\(\s*[\"'](\w+)[\"']", "direct"),
    # mongoTemplate.getCollection("name")
    (r'mongoTemplate\.getCollection\s*\(\s*"([^"]+)"', "direct"),
    # MongoTemplate with explicit collection: mongoTemplate.find(query, Entity.class, "collName")
    (r'mongoTemplate\.\w+\s*\([^)]*,\s*"([^"]+)"\s*\)', "direct"),
    # mongoTemplate.find(query, Entity.class) — extract entity class name as collection hint
    (r'mongoTemplate\.\w+\s*\([^,]+,\s*(\w+)\.class\s*\)', "entity_class"),
    # mongoOperations.find(query, Entity.class)
    (r'mongoOperations\.\w+\s*\([^,]+,\s*(\w+)\.class\s*\)', "entity_class"),
    # mongoOperations.getCollection("name")
    (r'mongoOperations\.getCollection\s*\(\s*"([^"]+)"', "direct"),
    # db["collection"] or db.collection
    (r"db\s*\[\s*[\"'](\w+)[\"']\s*\]", "direct"),
    (r"db\.(\w+)\.(?:find|aggregate|update|delete|insert|count)", "direct"),
    # MongoCollection variable assignments
    (r'MongoCollection.*=.*getCollection\s*\(\s*"([^"]+)"', "direct"),
    (r"MongoCollection.*=.*getCollection\s*\(\s*AppConstants\.(\w+)", "constant"),
    # MongoNamespace("db", "collection") — second arg is collection
    (r'MongoNamespace\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"', "direct"),
    # Spring Data repository — @RepositoryRestResource(collectionResourceRel = "name")
    (r'collectionResourceRel\s*=\s*"([^"]+)"', "direct"),
    # Groovy/JS: collection("name") or .collection("name")
    (r'\.?collection\s*\(\s*["\']([^"\']+)["\']', "direct"),
    # Python pymongo: db.collection_name.find() — single word after db.
    (r"db\.([a-z]\w+)\.", "direct"),
    # COLLECTION_NAME = "collName" constant pattern
    (r'(?:COLLECTION_NAME|COLLECTION|collectionName)\s*=\s*"([^"]+)"', "direct"),
    # private static final String COLLECTION = "name"
    (r'static\s+final\s+String\s+(?:\w*COLLECTION\w*|COLL_NAME)\s*=\s*"([^"]+)"', "direct"),
]

# ---------------------------------------------------------------------------
# Fields to ignore (MongoDB operators, common non-field keys)
# ---------------------------------------------------------------------------

IGNORE_FIELDS: set[str] = {
    "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$regex",
    "$and", "$or", "$not", "$nor", "$set", "$unset", "$inc", "$push", "$pull",
    "$addToSet", "$match", "$sort", "$group", "$project", "$lookup", "$unwind",
    "$limit", "$skip", "$count", "$options", "$elemMatch", "$size", "$type",
    "$all", "$text", "$search", "$meta", "$slice", "$each", "$position",
    "_id", "$eq", "$mod", "$where", "$geoNear", "$geoWithin",
    "$nearSphere", "$near", "$maxDistance", "$minDistance",
}

# ---------------------------------------------------------------------------
# Constant Resolver patterns
# ---------------------------------------------------------------------------

# Matches: public static final String FIELD_NAME = "value";
CONSTANT_FIELD_PATTERN: re.Pattern[str] = re.compile(
    r'public\s+static\s+final\s+String\s+(\w+)\s*=\s*"([^"]*)"'
)

# ---------------------------------------------------------------------------
# Annotation Parser patterns
# ---------------------------------------------------------------------------

# Matches: @Document(collection = "collectionName") with optional whitespace
DOCUMENT_ANNOTATION_PATTERN: re.Pattern[str] = re.compile(
    r'@Document\s*\(\s*(?:collection\s*=\s*)?["\']([^"\']+)["\']\s*\)'
)

# Matches: @CompoundIndex(...) capturing the full annotation body
COMPOUND_INDEX_PATTERN: re.Pattern[str] = re.compile(
    r"@CompoundIndex\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)", re.DOTALL
)

# Matches the def attribute inside @CompoundIndex, e.g. def = "{'field': 1, 'field2': -1}"
COMPOUND_INDEX_DEF_PATTERN: re.Pattern[str] = re.compile(
    r"""def\s*=\s*["']([^"']+)["']"""
)

# Matches individual field:direction pairs inside a compound index def string
# e.g. 'fieldName': 1  or  'fieldName': -1
COMPOUND_DEF_FIELD_PATTERN: re.Pattern[str] = re.compile(
    r"['\"](\w+)['\"]\s*:\s*(-?\d+)"
)

# Matches @Indexed annotation with optional parameters
INDEXED_FIELD_PATTERN: re.Pattern[str] = re.compile(
    r"@Indexed\b(?:\s*\(([^)]*)\))?"
)

# Matches @CompoundIndexes({ ... }) wrapping multiple @CompoundIndex
COMPOUND_INDEXES_PATTERN: re.Pattern[str] = re.compile(
    r"@CompoundIndexes\s*\(\s*\{(.*?)\}\s*\)", re.DOTALL
)

# Matches @TextIndexed annotation
TEXT_INDEXED_PATTERN: re.Pattern[str] = re.compile(r"@TextIndexed\b")

# Matches @GeoSpatialIndexed annotation
GEOSPATIAL_INDEXED_PATTERN: re.Pattern[str] = re.compile(r"@GeoSpatialIndexed\b")

# Matches @HashIndexed annotation
HASH_INDEXED_PATTERN: re.Pattern[str] = re.compile(r"@HashIndexed\b")

# Matches @WildcardIndexed annotation
WILDCARD_INDEXED_PATTERN: re.Pattern[str] = re.compile(r"@WildcardIndexed\b")

# Matches a Java field declaration to extract the field name following an annotation
# e.g. "private String fieldName;" or "protected List<String> fieldName;"
JAVA_FIELD_DECLARATION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:private|protected|public)?\s*(?:[\w<>,\s]+)\s+(\w+)\s*[;=]"
)

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns for prototype scanning (performance)
# ---------------------------------------------------------------------------

COMPILED_JAVA_INDEX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in JAVA_INDEX_PATTERNS
]

COMPILED_PYTHON_INDEX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in PYTHON_INDEX_PATTERNS
]

COMPILED_CONFIG_INDEX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in CONFIG_INDEX_PATTERNS
]

COMPILED_QUERY_FIELD_PATTERNS_JAVA: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in QUERY_FIELD_PATTERNS_JAVA
]

COMPILED_QUERY_FIELD_PATTERNS_PYTHON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in QUERY_FIELD_PATTERNS_PYTHON
]

COMPILED_OPERATION_PATTERNS_JAVA: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in OPERATION_PATTERNS_JAVA
]

COMPILED_OPERATION_PATTERNS_PYTHON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in OPERATION_PATTERNS_PYTHON
]

COMPILED_COLLECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in COLLECTION_PATTERNS
]

COMPILED_SCAN_EXTENSIONS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    ".java": COMPILED_JAVA_INDEX_PATTERNS,
    ".kt": COMPILED_JAVA_INDEX_PATTERNS,
    ".py": COMPILED_PYTHON_INDEX_PATTERNS,
    ".sql": COMPILED_CONFIG_INDEX_PATTERNS,
    ".xml": COMPILED_CONFIG_INDEX_PATTERNS,
    ".yaml": COMPILED_CONFIG_INDEX_PATTERNS,
    ".yml": COMPILED_CONFIG_INDEX_PATTERNS,
    ".json": COMPILED_CONFIG_INDEX_PATTERNS,
    ".properties": COMPILED_CONFIG_INDEX_PATTERNS,
    ".conf": COMPILED_CONFIG_INDEX_PATTERNS,
    ".js": COMPILED_PYTHON_INDEX_PATTERNS,
    ".ts": COMPILED_PYTHON_INDEX_PATTERNS,
}
