"""Unit tests for the AnnotationParser class."""

import os
import textwrap

import pytest

from index_scanner_mcp.annotation_parser import AnnotationParser


@pytest.fixture
def parser() -> AnnotationParser:
    return AnnotationParser()


# ---------------------------------------------------------------------------
# Sample Java content helpers
# ---------------------------------------------------------------------------

USERS_ENTITY = textwrap.dedent("""\
    package com.example.model;

    import org.springframework.data.mongodb.core.index.Indexed;
    import org.springframework.data.mongodb.core.index.CompoundIndex;
    import org.springframework.data.mongodb.core.index.CompoundIndexes;
    import org.springframework.data.mongodb.core.mapping.Document;

    @Document(collection = "users")
    @CompoundIndexes({
        @CompoundIndex(name = "email_tenant", def = "{'email': 1, 'tenantId': 1}", unique = true)
    })
    public class UserEntity {
        @Indexed
        private String email;

        @Indexed(unique = true)
        private String username;

        @TextIndexed
        private String bio;

        private String name;
    }
""")

NO_DOCUMENT_ENTITY = textwrap.dedent("""\
    package com.example.model;

    public class PlainClass {
        @Indexed
        private String email;
    }
""")

MULTI_COMPOUND_ENTITY = textwrap.dedent("""\
    package com.example.model;

    @Document(collection = "orders")
    @CompoundIndex(name = "user_date", def = "{'userId': 1, 'orderDate': -1}")
    @CompoundIndex(name = "status_idx", def = "{'status': 1}", unique = true)
    public class OrderEntity {
        private String userId;
        private String status;
    }
""")

SPECIAL_INDEXES_ENTITY = textwrap.dedent("""\
    package com.example.model;

    @Document(collection = "products")
    public class ProductEntity {
        @TextIndexed
        private String description;

        @GeoSpatialIndexed
        private double[] location;

        @HashIndexed
        private String sku;

        @WildcardIndexed
        private Object metadata;

        private String name;
    }
""")

TTL_ENTITY = textwrap.dedent("""\
    package com.example.model;

    @Document(collection = "sessions")
    public class SessionEntity {
        @Indexed(expireAfterSeconds = 3600)
        private java.util.Date createdAt;

        @Indexed(sparse = true)
        private String optionalField;
    }
""")


# ---------------------------------------------------------------------------
# parse_document_annotation
# ---------------------------------------------------------------------------


class TestParseDocumentAnnotation:
    def test_extracts_collection_name(self, parser):
        assert parser.parse_document_annotation(USERS_ENTITY) == "users"

    def test_returns_none_without_document(self, parser):
        assert parser.parse_document_annotation(NO_DOCUMENT_ENTITY) is None

    def test_single_quotes(self, parser):
        content = "@Document(collection = 'myCol')\npublic class Foo {}"
        assert parser.parse_document_annotation(content) == "myCol"

    def test_no_collection_keyword(self, parser):
        content = '@Document("directName")\npublic class Foo {}'
        assert parser.parse_document_annotation(content) == "directName"


# ---------------------------------------------------------------------------
# parse_compound_index
# ---------------------------------------------------------------------------


class TestParseCompoundIndex:
    def test_single_field(self, parser):
        result = parser.parse_compound_index("{'email': 1}")
        assert result == {"email": 1}

    def test_multiple_fields(self, parser):
        result = parser.parse_compound_index("{'email': 1, 'tenantId': -1}")
        assert result == {"email": 1, "tenantId": -1}

    def test_preserves_field_order(self, parser):
        result = parser.parse_compound_index("{'z': 1, 'a': -1, 'm': 1}")
        assert list(result.keys()) == ["z", "a", "m"]

    def test_empty_on_invalid_input(self, parser):
        assert parser.parse_compound_index("not valid") == {}

    def test_empty_string(self, parser):
        assert parser.parse_compound_index("") == {}


# ---------------------------------------------------------------------------
# parse_file – full file parsing
# ---------------------------------------------------------------------------


class TestParseFile:
    def test_users_entity(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)

        indexes = parser.parse_file(str(f), {})
        # 1 compound index + 2 @Indexed + 1 @TextIndexed = 4
        assert len(indexes) == 4

        collections = {idx.collection for idx in indexes}
        assert collections == {"users"}

    def test_no_document_returns_empty(self, parser, tmp_path):
        f = tmp_path / "PlainClass.java"
        f.write_text(NO_DOCUMENT_ENTITY)
        assert parser.parse_file(str(f), {}) == []

    def test_nonexistent_file_returns_empty(self, parser):
        assert parser.parse_file("/nonexistent/Foo.java", {}) == []

    def test_compound_index_fields(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)
        indexes = parser.parse_file(str(f), {})

        compound = [i for i in indexes if i.source and i.source.annotation == "@CompoundIndex"]
        assert len(compound) == 1
        assert compound[0].fields == {"email": 1, "tenantId": 1}
        assert compound[0].unique is True
        assert compound[0].name == "email_tenant"

    def test_indexed_fields(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)
        indexes = parser.parse_file(str(f), {})

        indexed = [i for i in indexes if i.source and i.source.annotation == "@Indexed"]
        assert len(indexed) == 2

        field_names = {list(i.fields.keys())[0] for i in indexed}
        assert "email" in field_names
        assert "username" in field_names

        unique_idx = [i for i in indexed if i.unique]
        assert len(unique_idx) == 1
        assert list(unique_idx[0].fields.keys())[0] == "username"

    def test_text_indexed(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)
        indexes = parser.parse_file(str(f), {})

        text = [i for i in indexes if i.index_type == "text"]
        assert len(text) == 1
        assert list(text[0].fields.keys())[0] == "bio"
        assert text[0].fields["bio"] == "text"

    def test_standalone_compound_indexes(self, parser, tmp_path):
        f = tmp_path / "OrderEntity.java"
        f.write_text(MULTI_COMPOUND_ENTITY)
        indexes = parser.parse_file(str(f), {})

        compound = [i for i in indexes if i.source and i.source.annotation == "@CompoundIndex"]
        assert len(compound) == 2

        names = {i.name for i in compound}
        assert names == {"user_date", "status_idx"}

        status_idx = [i for i in compound if i.name == "status_idx"][0]
        assert status_idx.unique is True

    def test_special_index_types(self, parser, tmp_path):
        f = tmp_path / "ProductEntity.java"
        f.write_text(SPECIAL_INDEXES_ENTITY)
        indexes = parser.parse_file(str(f), {})

        assert len(indexes) == 4

        types = {i.index_type for i in indexes}
        assert types == {"text", "geospatial", "hashed", "wildcard"}

        text_idx = [i for i in indexes if i.index_type == "text"][0]
        assert list(text_idx.fields.keys())[0] == "description"

        hash_idx = [i for i in indexes if i.index_type == "hashed"][0]
        assert list(hash_idx.fields.keys())[0] == "sku"
        assert hash_idx.fields["sku"] == "hashed"

    def test_ttl_and_sparse(self, parser, tmp_path):
        f = tmp_path / "SessionEntity.java"
        f.write_text(TTL_ENTITY)
        indexes = parser.parse_file(str(f), {})

        assert len(indexes) == 2

        ttl_idx = [i for i in indexes if i.expire_after_seconds is not None][0]
        assert ttl_idx.expire_after_seconds == 3600
        assert list(ttl_idx.fields.keys())[0] == "createdAt"

        sparse_idx = [i for i in indexes if i.sparse][0]
        assert list(sparse_idx.fields.keys())[0] == "optionalField"


# ---------------------------------------------------------------------------
# Source traceability (Req 10.1)
# ---------------------------------------------------------------------------


class TestSourceTraceability:
    def test_every_index_has_source(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)
        indexes = parser.parse_file(str(f), {})

        for idx in indexes:
            assert idx.source is not None
            assert idx.source.file == str(f)
            assert idx.source.line > 0
            assert idx.source.source_type == "annotation"

    def test_line_numbers_are_reasonable(self, parser, tmp_path):
        f = tmp_path / "UserEntity.java"
        f.write_text(USERS_ENTITY)
        indexes = parser.parse_file(str(f), {})

        lines = [idx.source.line for idx in indexes]
        # All line numbers should be within the file
        total_lines = USERS_ENTITY.count("\n") + 1
        for line in lines:
            assert 1 <= line <= total_lines


# ---------------------------------------------------------------------------
# Constant resolution (Req 3.9)
# ---------------------------------------------------------------------------


class TestConstantResolution:
    def test_resolves_field_names(self, parser, tmp_path):
        content = textwrap.dedent("""\
            @Document(collection = "items")
            public class ItemEntity {
                @Indexed
                private String userId;
            }
        """)
        f = tmp_path / "ItemEntity.java"
        f.write_text(content)

        constant_map = {"userId": "user_id_resolved"}
        indexes = parser.parse_file(str(f), constant_map)

        assert len(indexes) == 1
        assert list(indexes[0].fields.keys())[0] == "user_id_resolved"

    def test_unresolved_uses_raw_name(self, parser, tmp_path):
        content = textwrap.dedent("""\
            @Document(collection = "items")
            public class ItemEntity {
                @Indexed
                private String someField;
            }
        """)
        f = tmp_path / "ItemEntity.java"
        f.write_text(content)

        indexes = parser.parse_file(str(f), {})
        assert list(indexes[0].fields.keys())[0] == "someField"

    def test_compound_index_constant_resolution(self, parser, tmp_path):
        content = textwrap.dedent("""\
            @Document(collection = "items")
            @CompoundIndex(def = "{'userId': 1, 'tenantId': -1}")
            public class ItemEntity {
                private String userId;
            }
        """)
        f = tmp_path / "ItemEntity.java"
        f.write_text(content)

        constant_map = {"userId": "user_id", "tenantId": "tenant_id"}
        indexes = parser.parse_file(str(f), constant_map)

        compound = [i for i in indexes if i.source.annotation == "@CompoundIndex"]
        assert len(compound) == 1
        assert compound[0].fields == {"user_id": 1, "tenant_id": -1}


# ---------------------------------------------------------------------------
# Test with real SampleEntity.java
# ---------------------------------------------------------------------------


class TestSampleEntity:
    """Integration test against the test_sample/SampleEntity.java file."""

    SAMPLE_PATH = os.path.join(
        os.path.dirname(__file__), "..", "test_sample", "SampleEntity.java"
    )

    @pytest.fixture(autouse=True)
    def _skip_if_missing(self):
        if not os.path.isfile(self.SAMPLE_PATH):
            pytest.skip("test_sample/SampleEntity.java not found")

    def test_parses_sample_entity(self, parser):
        indexes = parser.parse_file(self.SAMPLE_PATH, {})

        # SampleEntity has: 2 @CompoundIndex, 2 @Indexed, 1 @TextIndexed = 5
        assert len(indexes) == 5

        collections = {idx.collection for idx in indexes}
        assert collections == {"users"}

    def test_compound_indexes_from_sample(self, parser):
        indexes = parser.parse_file(self.SAMPLE_PATH, {})
        compound = [i for i in indexes if i.source and i.source.annotation == "@CompoundIndex"]
        assert len(compound) == 2

        names = {i.name for i in compound}
        assert "email_tenant_idx" in names
        assert "status_created_idx" in names

    def test_indexed_fields_from_sample(self, parser):
        indexes = parser.parse_file(self.SAMPLE_PATH, {})
        indexed = [i for i in indexes if i.source and i.source.annotation == "@Indexed"]
        assert len(indexed) == 2

        unique_indexed = [i for i in indexed if i.unique]
        assert len(unique_indexed) == 1
        assert list(unique_indexed[0].fields.keys())[0] == "email"

    def test_text_indexed_from_sample(self, parser):
        indexes = parser.parse_file(self.SAMPLE_PATH, {})
        text = [i for i in indexes if i.index_type == "text"]
        assert len(text) == 1
        assert list(text[0].fields.keys())[0] == "description"
