"""Unit tests for the ScriptGenerator class."""

import pytest

from index_scanner_mcp.models import IndexDefinition, IndexSource
from index_scanner_mcp.script_generator import ScriptGenerator


@pytest.fixture
def generator() -> ScriptGenerator:
    return ScriptGenerator()


def _make_index(
    collection: str = "users",
    fields: dict | None = None,
    unique: bool = False,
    sparse: bool = False,
    expire_after_seconds: int | None = None,
    index_type: str = "standard",
) -> IndexDefinition:
    """Helper to build an IndexDefinition with sensible defaults."""
    return IndexDefinition(
        collection=collection,
        fields=fields or {"email": 1},
        unique=unique,
        sparse=sparse,
        expire_after_seconds=expire_after_seconds,
        index_type=index_type,
        source=IndexSource(file="Test.java", line=1, source_type="annotation"),
    )


# ---------------------------------------------------------------------------
# generate_mongo_shell
# ---------------------------------------------------------------------------


class TestGenerateMongoShell:
    def test_basic_single_index(self, generator):
        indexes = [_make_index(fields={"email": 1})]
        result = generator.generate_mongo_shell(indexes)

        assert 'db.users.createIndex({"email": 1});' in result

    def test_includes_use_db_when_provided(self, generator):
        indexes = [_make_index()]
        result = generator.generate_mongo_shell(indexes, db_name="mydb")

        assert "use mydb;" in result

    def test_no_use_db_when_not_provided(self, generator):
        indexes = [_make_index()]
        result = generator.generate_mongo_shell(indexes)

        assert "use " not in result

    def test_unique_option(self, generator):
        indexes = [_make_index(unique=True)]
        result = generator.generate_mongo_shell(indexes)

        assert '{"unique": true}' in result

    def test_sparse_option(self, generator):
        indexes = [_make_index(sparse=True)]
        result = generator.generate_mongo_shell(indexes)

        assert '{"sparse": true}' in result

    def test_ttl_option(self, generator):
        indexes = [_make_index(expire_after_seconds=3600)]
        result = generator.generate_mongo_shell(indexes)

        assert '{"expireAfterSeconds": 3600}' in result

    def test_combined_options(self, generator):
        indexes = [_make_index(unique=True, expire_after_seconds=7200)]
        result = generator.generate_mongo_shell(indexes)

        assert '"unique": true' in result
        assert '"expireAfterSeconds": 7200' in result


    def test_compound_index_fields(self, generator):
        indexes = [_make_index(fields={"email": 1, "tenantId": -1})]
        result = generator.generate_mongo_shell(indexes)

        assert '{"email": 1, "tenantId": -1}' in result

    def test_groups_by_collection(self, generator):
        indexes = [
            _make_index(collection="users", fields={"email": 1}),
            _make_index(collection="orders", fields={"orderId": 1}),
            _make_index(collection="users", fields={"name": 1}),
        ]
        result = generator.generate_mongo_shell(indexes)

        # Both users indexes should appear under the same collection header
        lines = result.split("\n")
        users_header = next(
            i for i, l in enumerate(lines) if "Collection: users" in l
        )
        orders_header = next(
            i for i, l in enumerate(lines) if "Collection: orders" in l
        )

        # All users createIndex calls should be between users header and orders header
        # (collections are sorted alphabetically, orders < users)
        users_lines = [
            l for l in lines[users_header:] if "db.users.createIndex" in l
        ]
        assert len(users_lines) == 2

    def test_no_options_when_none_set(self, generator):
        indexes = [_make_index()]
        result = generator.generate_mongo_shell(indexes)

        # Should have createIndex with only the fields arg
        for line in result.split("\n"):
            if "createIndex" in line:
                # Count the arguments — only one set of braces for fields
                assert line.count("createIndex(") == 1
                # No second argument
                assert ", {" not in line or '{"email": 1}' in line.split(", {")[0]

    def test_header_comment(self, generator):
        indexes = [_make_index()]
        result = generator.generate_mongo_shell(indexes)

        assert "// Auto-generated MongoDB index creation script" in result

    def test_one_create_index_per_definition(self, generator):
        indexes = [
            _make_index(fields={"a": 1}),
            _make_index(fields={"b": 1}),
            _make_index(collection="orders", fields={"c": -1}),
        ]
        result = generator.generate_mongo_shell(indexes)

        assert result.count("createIndex(") == 3


# ---------------------------------------------------------------------------
# generate_pymongo
# ---------------------------------------------------------------------------


class TestGeneratePymongo:
    def test_basic_single_index(self, generator):
        indexes = [_make_index(fields={"email": 1})]
        result = generator.generate_pymongo(indexes, db_name="mydb")

        assert 'db["users"].create_index([("email", ASCENDING)])' in result

    def test_includes_db_name(self, generator):
        indexes = [_make_index()]
        result = generator.generate_pymongo(indexes, db_name="testdb")

        assert 'db = client["testdb"]' in result

    def test_default_db_name(self, generator):
        indexes = [_make_index()]
        result = generator.generate_pymongo(indexes)

        assert 'db = client["mydb"]' in result

    def test_descending_direction(self, generator):
        indexes = [_make_index(fields={"createdAt": -1})]
        result = generator.generate_pymongo(indexes)

        assert '("createdAt", DESCENDING)' in result

    def test_unique_option(self, generator):
        indexes = [_make_index(unique=True)]
        result = generator.generate_pymongo(indexes)

        assert "unique=True" in result

    def test_sparse_option(self, generator):
        indexes = [_make_index(sparse=True)]
        result = generator.generate_pymongo(indexes)

        assert "sparse=True" in result

    def test_ttl_option(self, generator):
        indexes = [_make_index(expire_after_seconds=3600)]
        result = generator.generate_pymongo(indexes)

        assert "expireAfterSeconds=3600" in result

    def test_compound_index(self, generator):
        indexes = [_make_index(fields={"email": 1, "tenantId": -1})]
        result = generator.generate_pymongo(indexes)

        assert '("email", ASCENDING)' in result
        assert '("tenantId", DESCENDING)' in result

    def test_imports_needed_constants(self, generator):
        indexes = [_make_index(fields={"a": 1, "b": -1})]
        result = generator.generate_pymongo(indexes)

        # Import line is after shebang, docstring, and blank line
        import_line = [l for l in result.split("\n") if l.startswith("from pymongo")][0]
        assert "ASCENDING" in import_line
        assert "DESCENDING" in import_line

    def test_groups_by_collection(self, generator):
        indexes = [
            _make_index(collection="users", fields={"email": 1}),
            _make_index(collection="orders", fields={"orderId": 1}),
            _make_index(collection="users", fields={"name": 1}),
        ]
        result = generator.generate_pymongo(indexes)

        assert result.count("# Collection: users") == 1
        assert result.count("# Collection: orders") == 1

    def test_one_create_index_per_definition(self, generator):
        indexes = [
            _make_index(fields={"a": 1}),
            _make_index(fields={"b": 1}),
            _make_index(collection="orders", fields={"c": -1}),
        ]
        result = generator.generate_pymongo(indexes)

        assert result.count("create_index(") == 3

    def test_header_comment(self, generator):
        indexes = [_make_index()]
        result = generator.generate_pymongo(indexes)

        assert "#!/usr/bin/env python3" in result
        assert '"""Auto-generated pymongo index creation script."""' in result


# ---------------------------------------------------------------------------
# generate_verification_script
# ---------------------------------------------------------------------------


class TestGenerateVerificationScript:
    def test_basic_verification(self, generator):
        indexes = [_make_index(fields={"email": 1})]
        result = generator.generate_verification_script(indexes)

        assert "var missing = [];" in result
        assert "getIndexes()" in result
        assert "missing.push(" in result

    def test_includes_use_db(self, generator):
        indexes = [_make_index()]
        result = generator.generate_verification_script(indexes, db_name="mydb")

        assert "use mydb;" in result

    def test_no_use_db_when_not_provided(self, generator):
        indexes = [_make_index()]
        result = generator.generate_verification_script(indexes)

        assert "use " not in result

    def test_reports_missing_with_collection_and_fields(self, generator):
        indexes = [_make_index(collection="users", fields={"email": 1})]
        result = generator.generate_verification_script(indexes)

        # The missing.push should include collection name and field spec
        assert "users:" in result
        assert "email: 1" in result

    def test_reports_success_when_all_present(self, generator):
        indexes = [_make_index()]
        result = generator.generate_verification_script(indexes)

        assert "All indexes present" in result

    def test_reports_missing_indexes(self, generator):
        indexes = [_make_index()]
        result = generator.generate_verification_script(indexes)

        assert "Missing indexes:" in result

    def test_checks_each_index(self, generator):
        indexes = [
            _make_index(collection="users", fields={"email": 1}),
            _make_index(collection="users", fields={"name": 1}),
            _make_index(collection="orders", fields={"orderId": 1}),
        ]
        result = generator.generate_verification_script(indexes)

        assert result.count("missing.push(") == 3

    def test_compound_index_key_format(self, generator):
        indexes = [_make_index(fields={"email": 1, "tenantId": -1})]
        result = generator.generate_verification_script(indexes)

        # The key JSON uses escaped quotes for indexOf comparison
        assert "email" in result
        assert "tenantId" in result
        # The push message should contain human-readable field spec
        assert "email: 1, tenantId: -1" in result

    def test_header_comment(self, generator):
        indexes = [_make_index()]
        result = generator.generate_verification_script(indexes)

        assert "// Auto-generated index verification script" in result

    def test_groups_checks_by_collection(self, generator):
        indexes = [
            _make_index(collection="users", fields={"email": 1}),
            _make_index(collection="orders", fields={"orderId": 1}),
        ]
        result = generator.generate_verification_script(indexes)

        assert "// Check indexes for: users" in result
        assert "// Check indexes for: orders" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_text_index_type_in_shell(self, generator):
        indexes = [_make_index(fields={"description": "text"}, index_type="text")]
        result = generator.generate_mongo_shell(indexes)

        assert '"description": "text"' in result

    def test_hashed_index_type_in_shell(self, generator):
        indexes = [_make_index(fields={"sku": "hashed"}, index_type="hashed")]
        result = generator.generate_mongo_shell(indexes)

        assert '"sku": "hashed"' in result

    def test_text_index_in_pymongo(self, generator):
        indexes = [_make_index(fields={"description": "text"}, index_type="text")]
        result = generator.generate_pymongo(indexes)

        assert "TEXT" in result

    def test_hashed_index_in_pymongo(self, generator):
        indexes = [_make_index(fields={"sku": "hashed"}, index_type="hashed")]
        result = generator.generate_pymongo(indexes)

        assert "HASHED" in result

    def test_empty_index_list_shell(self, generator):
        result = generator.generate_mongo_shell([])

        assert "createIndex" not in result
        assert "// Auto-generated" in result

    def test_empty_index_list_pymongo(self, generator):
        result = generator.generate_pymongo([])

        assert "create_index" not in result

    def test_empty_index_list_verification(self, generator):
        result = generator.generate_verification_script([])

        assert "missing.push" not in result
        assert "All indexes present" in result
