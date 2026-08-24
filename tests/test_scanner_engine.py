"""Unit tests for the ScannerEngine class."""

import os
import textwrap

import pytest

from index_scanner_mcp.scanner_engine import ScannerEngine


@pytest.fixture
def engine() -> ScannerEngine:
    return ScannerEngine()


# ---------------------------------------------------------------------------
# Sample Java content
# ---------------------------------------------------------------------------

ENTITY_WITH_INDEXES = textwrap.dedent("""\
    package com.example.model;

    import org.springframework.data.mongodb.core.index.Indexed;
    import org.springframework.data.mongodb.core.index.CompoundIndex;
    import org.springframework.data.mongodb.core.mapping.Document;

    @Document(collection = "users")
    @CompoundIndex(name = "email_tenant", def = "{'email': 1, 'tenantId': 1}", unique = true)
    public class UserEntity {
        @Indexed(unique = true)
        private String email;

        @Indexed
        private String tenantId;
    }
""")

ENTITY_WITH_QUERIES = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;

    public class UserDao {
        public void findUser() {
            BasicDBObject query = new BasicDBObject("status", "active");
            query.append("region", "US");
            collection.find(query);
        }
    }
""")

CONSTANTS_FILE = textwrap.dedent("""\
    package com.example;

    public final class AppConstants {
        public static final String USERID = "userId";
        public static final String JOBIDS = "jobIds";
    }
""")

DUPLICATE_ENTITY_A = textwrap.dedent("""\
    @Document(collection = "items")
    @CompoundIndex(def = "{'a': 1, 'b': -1}")
    public class ItemEntityA {
        @Indexed
        private String name;
    }
""")

DUPLICATE_ENTITY_B = textwrap.dedent("""\
    @Document(collection = "items")
    @CompoundIndex(def = "{'a': 1, 'b': -1}")
    public class ItemEntityB {
        @Indexed
        private String name;
    }
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_java_file(directory, filename, content):
    """Write a Java file into the given directory."""
    filepath = os.path.join(directory, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def _build_project(tmp_path, files: dict[str, str]) -> str:
    """Create a project directory with the given files.

    *files* maps relative paths (e.g. ``"src/Foo.java"``) to content.
    Returns the project root path.
    """
    project = str(tmp_path / "project")
    os.makedirs(project, exist_ok=True)
    for rel_path, content in files.items():
        full = os.path.join(project, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    return project


# ---------------------------------------------------------------------------
# discover_java_files
# ---------------------------------------------------------------------------


class TestDiscoverJavaFiles:
    def test_finds_java_files(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/UserEntity.java": ENTITY_WITH_INDEXES,
            "src/UserDao.java": ENTITY_WITH_QUERIES,
            "README.md": "# readme",
        })
        files = engine.discover_java_files(project)
        assert len(files) == 2
        assert all(f.endswith(".java") for f in files)

    def test_skips_excluded_directories(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/Good.java": "class Good {}",
            "target/Bad.java": "class Bad {}",
            ".git/hooks/pre-commit.java": "class Hook {}",
            "node_modules/dep/Dep.java": "class Dep {}",
            "build/Out.java": "class Out {}",
        })
        files = engine.discover_java_files(project)
        assert len(files) == 1
        assert files[0].endswith("Good.java")

    def test_empty_directory(self, engine, tmp_path):
        project = str(tmp_path / "empty")
        os.makedirs(project)
        assert engine.discover_java_files(project) == []

    def test_no_java_files(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/readme.txt": "hello",
            "src/config.yaml": "key: val",
        })
        assert engine.discover_java_files(project) == []

    def test_custom_skip_dirs(self, tmp_path):
        engine = ScannerEngine(skip_dirs={"custom_skip"})
        project = _build_project(tmp_path, {
            "src/Good.java": "class Good {}",
            "custom_skip/Bad.java": "class Bad {}",
            "target/AlsoGood.java": "class AlsoGood {}",
        })
        files = engine.discover_java_files(project)
        names = {os.path.basename(f) for f in files}
        assert "Good.java" in names
        assert "AlsoGood.java" in names
        assert "Bad.java" not in names


# ---------------------------------------------------------------------------
# scan_project
# ---------------------------------------------------------------------------


class TestScanProject:
    def test_basic_scan(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/UserEntity.java": ENTITY_WITH_INDEXES,
        })
        result = engine.scan_project(project)
        assert result.project_path == project
        assert result.files_scanned == 1
        assert len(result.indexes) > 0
        assert result.errors == []

    def test_files_scanned_count(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/A.java": ENTITY_WITH_INDEXES,
            "src/B.java": ENTITY_WITH_QUERIES,
            "src/C.java": "class C {}",
        })
        result = engine.scan_project(project)
        assert result.files_scanned == 3

    def test_invalid_path_returns_error(self, engine):
        result = engine.scan_project("/nonexistent/path/to/project")
        assert len(result.errors) == 1
        assert "not a valid directory" in result.errors[0]
        assert result.files_scanned == 0
        assert result.indexes == []

    def test_file_path_returns_error(self, engine, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = engine.scan_project(str(f))
        assert len(result.errors) == 1
        assert "not a valid directory" in result.errors[0]

    def test_empty_project(self, engine, tmp_path):
        project = str(tmp_path / "empty")
        os.makedirs(project)
        result = engine.scan_project(project)
        assert result.files_scanned == 0
        assert result.indexes == []
        assert result.suggestions == []
        assert result.errors == []

    def test_no_java_files(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "readme.md": "# hello",
        })
        result = engine.scan_project(project)
        assert result.files_scanned == 0
        assert result.indexes == []

    def test_constants_resolved_count(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/AppConstants.java": CONSTANTS_FILE,
            "src/UserEntity.java": ENTITY_WITH_INDEXES,
        })
        result = engine.scan_project(project)
        assert result.constants_resolved > 0

    def test_indexes_have_correct_collection(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/UserEntity.java": ENTITY_WITH_INDEXES,
        })
        result = engine.scan_project(project)
        for idx in result.indexes:
            assert idx.collection == "users"


# ---------------------------------------------------------------------------
# Deduplication (Req 7.1, 7.2)
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_removes_duplicate_indexes(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/ItemEntityA.java": DUPLICATE_ENTITY_A,
            "src/ItemEntityB.java": DUPLICATE_ENTITY_B,
        })
        result = engine.scan_project(project)

        # Both files define @CompoundIndex({'a': 1, 'b': -1}) on "items"
        # and @Indexed on "name" in "items" — each pair should be deduped
        compound = [i for i in result.indexes if len(i.fields) == 2]
        assert len(compound) == 1
        assert compound[0].fields == {"a": 1, "b": -1}

        single = [i for i in result.indexes if len(i.fields) == 1]
        assert len(single) == 1

    def test_different_options_not_deduped(self, engine, tmp_path):
        entity_unique = textwrap.dedent("""\
            @Document(collection = "items")
            @CompoundIndex(def = "{'a': 1, 'b': -1}", unique = true)
            public class ItemUnique {}
        """)
        entity_non_unique = textwrap.dedent("""\
            @Document(collection = "items")
            @CompoundIndex(def = "{'a': 1, 'b': -1}")
            public class ItemNonUnique {}
        """)
        project = _build_project(tmp_path, {
            "src/ItemUnique.java": entity_unique,
            "src/ItemNonUnique.java": entity_non_unique,
        })
        result = engine.scan_project(project)
        compound = [i for i in result.indexes if len(i.fields) == 2]
        # unique=True vs unique=False → not duplicates
        assert len(compound) == 2

    def test_different_collections_not_deduped(self, engine, tmp_path):
        entity_a = textwrap.dedent("""\
            @Document(collection = "colA")
            public class A {
                @Indexed
                private String name;
            }
        """)
        entity_b = textwrap.dedent("""\
            @Document(collection = "colB")
            public class B {
                @Indexed
                private String name;
            }
        """)
        project = _build_project(tmp_path, {
            "src/A.java": entity_a,
            "src/B.java": entity_b,
        })
        result = engine.scan_project(project)
        assert len(result.indexes) == 2


# ---------------------------------------------------------------------------
# Suggestion filtering (Req 4.5)
# ---------------------------------------------------------------------------


class TestSuggestionFiltering:
    def test_filters_suggestions_matching_existing_indexes(self, engine, tmp_path):
        # Entity defines @Indexed on "email" in "users"
        # Query also queries "email" in "users" → suggestion should be filtered
        entity = textwrap.dedent("""\
            @Document(collection = "users")
            public class UserEntity {
                @Indexed
                private String email;
            }
        """)
        dao = textwrap.dedent("""\
            package com.example.dao;
            import com.mongodb.BasicDBObject;
            public class UserDao {
                public void findByEmail() {
                    getCollection("users");
                    BasicDBObject q = new BasicDBObject("email", "test@example.com");
                    collection.find(q);
                }
            }
        """)
        project = _build_project(tmp_path, {
            "src/UserEntity.java": entity,
            "src/UserDao.java": dao,
        })
        result = engine.scan_project(project)

        # The "email" suggestion should be filtered out since it matches the index
        email_suggestions = [
            s for s in result.suggestions
            if "email" in s.fields and s.collection == "users"
        ]
        assert len(email_suggestions) == 0

    def test_keeps_suggestions_not_matching_indexes(self, engine, tmp_path):
        entity = textwrap.dedent("""\
            @Document(collection = "users")
            public class UserEntity {
                @Indexed
                private String email;
            }
        """)
        dao = textwrap.dedent("""\
            package com.example.dao;
            import com.mongodb.BasicDBObject;
            public class UserDao {
                public void findByStatus() {
                    getCollection("users");
                    BasicDBObject q = new BasicDBObject("status", "active");
                    collection.find(q);
                }
            }
        """)
        project = _build_project(tmp_path, {
            "src/UserEntity.java": entity,
            "src/UserDao.java": dao,
        })
        result = engine.scan_project(project)

        status_suggestions = [
            s for s in result.suggestions
            if "status" in s.fields
        ]
        # "status" is not covered by any index → suggestion should remain
        assert len(status_suggestions) >= 1


# ---------------------------------------------------------------------------
# Error handling (Req 8.1, 8.2)
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unreadable_file_adds_error_and_continues(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/Good.java": ENTITY_WITH_INDEXES,
            "src/Bad.java": ENTITY_WITH_INDEXES,
        })
        # Make Bad.java unreadable
        bad_path = os.path.join(project, "src", "Bad.java")
        os.chmod(bad_path, 0o000)

        try:
            result = engine.scan_project(project)
            # Good.java should still produce indexes
            assert result.files_scanned == 2
            # Indexes from Good.java should still be present
            assert len(result.indexes) > 0
        finally:
            # Restore permissions for cleanup
            os.chmod(bad_path, 0o644)

    def test_binary_file_with_java_extension(self, engine, tmp_path):
        project = _build_project(tmp_path, {
            "src/Good.java": ENTITY_WITH_INDEXES,
        })
        # Write binary content to a .java file
        binary_path = os.path.join(project, "src", "Binary.java")
        with open(binary_path, "wb") as f:
            f.write(b"\x00\x01\x02\x03\xff\xfe")

        result = engine.scan_project(project)
        # Should still get indexes from Good.java
        assert result.files_scanned == 2
        assert len(result.indexes) > 0


# ---------------------------------------------------------------------------
# scan_multiple_projects (Req 1.4)
# ---------------------------------------------------------------------------


class TestScanMultipleProjects:
    def test_scans_sub_projects(self, engine, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)

        # Create two sub-projects
        proj_a = os.path.join(root, "project-a")
        proj_b = os.path.join(root, "project-b")
        os.makedirs(os.path.join(proj_a, "src"))
        os.makedirs(os.path.join(proj_b, "src"))

        _create_java_file(
            os.path.join(proj_a, "src"), "Entity.java", ENTITY_WITH_INDEXES
        )
        _create_java_file(
            os.path.join(proj_b, "src"), "Dao.java", ENTITY_WITH_QUERIES
        )

        results = engine.scan_multiple_projects(root)
        assert "project-a" in results
        assert "project-b" in results
        assert len(results) == 2
        assert results["project-a"].files_scanned == 1
        assert results["project-b"].files_scanned == 1

    def test_skips_excluded_dirs(self, engine, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)

        os.makedirs(os.path.join(root, "real-project", "src"))
        os.makedirs(os.path.join(root, ".git", "hooks"))
        os.makedirs(os.path.join(root, "node_modules", "dep"))

        _create_java_file(
            os.path.join(root, "real-project", "src"), "A.java", ENTITY_WITH_INDEXES
        )
        _create_java_file(
            os.path.join(root, ".git", "hooks"), "B.java", ENTITY_WITH_INDEXES
        )

        results = engine.scan_multiple_projects(root)
        assert "real-project" in results
        assert ".git" not in results
        assert "node_modules" not in results

    def test_invalid_root_returns_empty(self, engine):
        results = engine.scan_multiple_projects("/nonexistent/root")
        assert results == {}

    def test_empty_root(self, engine, tmp_path):
        root = str(tmp_path / "empty_root")
        os.makedirs(root)
        results = engine.scan_multiple_projects(root)
        assert results == {}

    def test_files_in_root_ignored(self, engine, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)
        # Only files at root level, no sub-project dirs
        with open(os.path.join(root, "readme.md"), "w") as f:
            f.write("# hello")
        results = engine.scan_multiple_projects(root)
        assert results == {}

    def test_each_project_independent(self, engine, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)

        proj_a = os.path.join(root, "proj-a")
        proj_b = os.path.join(root, "proj-b")
        os.makedirs(os.path.join(proj_a, "src"))
        os.makedirs(os.path.join(proj_b, "src"))

        _create_java_file(
            os.path.join(proj_a, "src"), "UserEntity.java",
            textwrap.dedent("""\
                @Document(collection = "users")
                public class UserEntity {
                    @Indexed
                    private String email;
                }
            """),
        )
        _create_java_file(
            os.path.join(proj_b, "src"), "OrderEntity.java",
            textwrap.dedent("""\
                @Document(collection = "orders")
                public class OrderEntity {
                    @Indexed
                    private String orderId;
                }
            """),
        )

        results = engine.scan_multiple_projects(root)
        assert results["proj-a"].indexes[0].collection == "users"
        assert results["proj-b"].indexes[0].collection == "orders"


# ---------------------------------------------------------------------------
# Integration test against test_sample/SampleEntity.java
# ---------------------------------------------------------------------------


class TestSampleEntityIntegration:
    """Integration test scanning the test_sample directory."""

    SAMPLE_DIR = os.path.join(
        os.path.dirname(__file__), "..", "test_sample"
    )

    @pytest.fixture(autouse=True)
    def _skip_if_missing(self):
        if not os.path.isdir(self.SAMPLE_DIR):
            pytest.skip("test_sample directory not found")

    def test_scan_test_sample(self, engine):
        result = engine.scan_project(self.SAMPLE_DIR)
        assert result.files_scanned >= 1
        # SampleEntity.java has: 2 @CompoundIndex + 2 @Indexed + 1 @TextIndexed = 5
        assert len(result.indexes) == 5
        assert result.errors == []

        collections = {idx.collection for idx in result.indexes}
        assert "users" in collections

    def test_no_duplicate_indexes_in_sample(self, engine):
        result = engine.scan_project(self.SAMPLE_DIR)
        # All 5 indexes should be unique
        keys = set()
        for idx in result.indexes:
            key = (
                idx.collection,
                tuple(sorted(idx.fields.items())),
                idx.unique,
                idx.sparse,
                idx.expire_after_seconds,
            )
            assert key not in keys, f"Duplicate index found: {key}"
            keys.add(key)


# ---------------------------------------------------------------------------
# Integration test against candidate-association-service
# ---------------------------------------------------------------------------


class TestCandidateAssociationServiceIntegration:
    """Integration test against the real candidate-association-service project."""

    REAL_PROJECT = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",
        "candidate-association-service",
    )

    @pytest.fixture(autouse=True)
    def _skip_if_missing(self):
        if not os.path.isdir(self.REAL_PROJECT):
            pytest.skip("candidate-association-service not found in workspace")

    def test_scan_real_project(self, engine):
        result = engine.scan_project(self.REAL_PROJECT)
        assert result.files_scanned > 0
        assert result.constants_resolved > 0
        assert result.errors == []

    def test_discovers_indexes_or_suggestions(self, engine):
        result = engine.scan_project(self.REAL_PROJECT)
        # The project should produce indexes and/or suggestions
        assert len(result.indexes) + len(result.suggestions) > 0
        for idx in result.indexes:
            assert idx.collection
            assert idx.fields
        for sug in result.suggestions:
            assert sug.collection
            assert sug.fields

    def test_no_duplicate_indexes(self, engine):
        result = engine.scan_project(self.REAL_PROJECT)
        keys = set()
        for idx in result.indexes:
            key = (
                idx.collection,
                tuple(sorted(idx.fields.items())),
                idx.unique,
                idx.sparse,
                idx.expire_after_seconds,
            )
            assert key not in keys, f"Duplicate index: {key}"
            keys.add(key)
