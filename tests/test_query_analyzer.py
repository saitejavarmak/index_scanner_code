"""Unit tests for the QueryPatternAnalyzer class."""

import os
import textwrap

import pytest

from index_scanner_mcp.query_analyzer import QueryPatternAnalyzer


@pytest.fixture
def analyzer() -> QueryPatternAnalyzer:
    return QueryPatternAnalyzer()


# ---------------------------------------------------------------------------
# Sample Java content helpers
# ---------------------------------------------------------------------------

BASIC_DB_OBJECT_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;

    public class CandidateDao {
        public void findByJobId(String jobId) {
            MongoCollection col = getCollection("candidates");
            col.find(new BasicDBObject("jobId", jobId));
        }
    }
""")

DOCUMENT_APPEND_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import org.bson.Document;

    public class CandidateAssociationDao {
        public void associate(String jobId, String candidateId) {
            MongoCollection col = getCollection("CandidateAssociatedEntities");
            Document query = new Document().append("jobIds", jobId);
            query.append("userId", candidateId);
            col.find(query);
        }
    }
""")

FILTERS_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.client.model.Filters;

    public class UserDao {
        public void findActiveUsers() {
            MongoCollection col = getCollection("users");
            col.find(Filters.eq("status", "active"));
            col.find(Filters.gt("age", 18));
        }
    }
""")

CONSTANT_REFERENCE_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;

    public class CandidateDao {
        public void findByJob(String jobId) {
            MongoCollection col = getCollection("candidates");
            col.find(new BasicDBObject(AppConstants.JOBIDS, jobId));
        }

        public void findByUser(String candidateId) {
            col.find(new BasicDBObject(AppConstants.USERID, candidateId));
        }
    }
""")

SORT_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;
    import com.mongodb.client.model.Sorts;

    public class OrderDao {
        public void findRecentOrders() {
            MongoCollection col = getCollection("orders");
            col.find(new BasicDBObject("status", "active"))
               .sort(new BasicDBObject("createdAt", -1));
        }

        public void findSorted() {
            col.find(new BasicDBObject("status", "pending"))
               .sort(new BasicDBObject("createdAt", -1));
        }
    }
""")

MULTIPLE_OPERATIONS_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;

    public class MultiOpDao {
        public void doStuff() {
            MongoCollection col = getCollection("items");
            col.find(new BasicDBObject("category", "electronics"));
            col.updateOne(new BasicDBObject("itemId", "123"), new BasicDBObject("$set", new BasicDBObject("price", 99)));
            col.deleteOne(new BasicDBObject("itemId", "456"));
            col.aggregate(new BasicDBObject("$match", new BasicDBObject("status", "active")));
        }
    }
""")

CANDIDATE_ASSOCIATION_PATTERN = textwrap.dedent("""\
    package com.example.dao;

    import org.bson.Document;

    public class CandidateAssociationServiceDao {
        public void associateCandidate(String refNum, String jobId, String candidateId) {
            MongoCollection col = getCollection(refNum + AppConstants.UN_CANDIDATES, AppConstants.COLL_CAND_ASSOCIATED_ENTITIES);
            Document query = new Document().append(AppConstants.JOBIDS, jobId);
            query.append(AppConstants.USERID, candidateId);
            col.find(query);
        }
    }
""")

NO_QUERY_PATTERNS = textwrap.dedent("""\
    package com.example.model;

    public class PlainClass {
        private String name;
        public String getName() { return name; }
    }
""")

HEAVY_USAGE_QUERY = textwrap.dedent("""\
    package com.example.dao;

    import com.mongodb.BasicDBObject;

    public class HeavyDao {
        public void method1() {
            MongoCollection col = getCollection("heavy");
            col.find(new BasicDBObject("hotField", "a"));
            col.find(new BasicDBObject("hotField", "b"));
            col.find(new BasicDBObject("hotField", "c"));
            col.find(new BasicDBObject("hotField", "d"));
            col.find(new BasicDBObject("hotField", "e"));
        }
    }
""")


# ---------------------------------------------------------------------------
# extract_query_fields
# ---------------------------------------------------------------------------


class TestExtractQueryFields:
    def test_basic_db_object(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        assert len(usages) == 1
        assert usages[0].field == "jobId"
        assert usages[0].usage_type == "filter_equality"

    def test_document_append(self, analyzer):
        usages = analyzer.extract_query_fields(DOCUMENT_APPEND_QUERY, {}, "Test.java")
        fields = {u.field for u in usages}
        assert "jobIds" in fields
        assert "userId" in fields

    def test_filters_patterns(self, analyzer):
        usages = analyzer.extract_query_fields(FILTERS_QUERY, {}, "Test.java")
        fields = {u.field for u in usages}
        assert "status" in fields
        assert "age" in fields

    def test_constant_resolution(self, analyzer):
        constant_map = {
            "AppConstants.JOBIDS": "jobIds",
            "JOBIDS": "jobIds",
            "AppConstants.USERID": "userId",
            "USERID": "userId",
        }
        usages = analyzer.extract_query_fields(
            CONSTANT_REFERENCE_QUERY, constant_map, "Test.java"
        )
        fields = {u.field for u in usages}
        assert "jobIds" in fields
        assert "userId" in fields

    def test_sort_usage_type(self, analyzer):
        usages = analyzer.extract_query_fields(SORT_QUERY, {}, "Test.java")
        sort_usages = [u for u in usages if u.usage_type == "sort"]
        assert len(sort_usages) >= 1
        assert any(u.field == "createdAt" for u in sort_usages)

    def test_no_query_patterns_returns_empty(self, analyzer):
        usages = analyzer.extract_query_fields(NO_QUERY_PATTERNS, {}, "Test.java")
        assert usages == []

    def test_empty_content(self, analyzer):
        usages = analyzer.extract_query_fields("", {}, "Test.java")
        assert usages == []

    def test_ignores_mongo_operators(self, analyzer):
        content = textwrap.dedent("""\
            col.find(new BasicDBObject("$set", value));
            col.find(new BasicDBObject("$gt", 5));
        """)
        usages = analyzer.extract_query_fields(content, {}, "Test.java")
        assert all(not u.field.startswith("$") for u in usages)

    def test_tracks_line_numbers(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        assert len(usages) == 1
        assert usages[0].line > 0

    def test_tracks_file_path(self, analyzer):
        usages = analyzer.extract_query_fields(
            BASIC_DB_OBJECT_QUERY, {}, "src/CandidateDao.java"
        )
        assert usages[0].file == "src/CandidateDao.java"

    def test_detects_collection_name(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        assert usages[0].collection == "candidates"

    def test_detects_operation_type(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        assert usages[0].operation == "find"

    def test_candidate_association_pattern(self, analyzer):
        constant_map = {
            "AppConstants.JOBIDS": "jobIds",
            "JOBIDS": "jobIds",
            "AppConstants.USERID": "userId",
            "USERID": "userId",
            "AppConstants.UN_CANDIDATES": "_candidates",
            "UN_CANDIDATES": "_candidates",
            "AppConstants.COLL_CAND_ASSOCIATED_ENTITIES": "CandidateAssociatedEntities",
            "COLL_CAND_ASSOCIATED_ENTITIES": "CandidateAssociatedEntities",
        }
        usages = analyzer.extract_query_fields(
            CANDIDATE_ASSOCIATION_PATTERN, constant_map, "Test.java"
        )
        fields = {u.field for u in usages}
        assert "jobIds" in fields
        assert "userId" in fields


# ---------------------------------------------------------------------------
# generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    def test_single_field_suggestion(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        assert len(suggestions) >= 1
        fields = [list(s.fields.keys()) for s in suggestions]
        assert any("jobId" in f for f in fields)

    def test_compound_suggestion_from_nearby_fields(self, analyzer):
        usages = analyzer.extract_query_fields(DOCUMENT_APPEND_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        compound = [s for s in suggestions if len(s.fields) >= 2]
        assert len(compound) >= 1

    def test_priority_high_for_many_refs(self, analyzer):
        usages = analyzer.extract_query_fields(HEAVY_USAGE_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        hot_field_suggestions = [
            s for s in suggestions if "hotField" in s.fields
        ]
        assert len(hot_field_suggestions) >= 1
        assert hot_field_suggestions[0].priority == "high"

    def test_priority_high_for_sort_fields(self, analyzer):
        usages = analyzer.extract_query_fields(SORT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        sort_suggestions = [
            s for s in suggestions if "createdAt" in s.fields and len(s.fields) == 1
        ]
        assert len(sort_suggestions) >= 1
        assert sort_suggestions[0].priority == "high"

    def test_priority_low_for_single_ref(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        single = [s for s in suggestions if len(s.fields) == 1]
        assert len(single) >= 1
        assert single[0].priority == "low"

    def test_suggestions_have_sample_locations(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        for s in suggestions:
            assert len(s.sample_locations) >= 1

    def test_suggestions_have_operations(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        for s in suggestions:
            assert len(s.operations) >= 1

    def test_suggestions_have_rationale(self, analyzer):
        usages = analyzer.extract_query_fields(BASIC_DB_OBJECT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        for s in suggestions:
            assert s.rationale

    def test_suggestions_sorted_by_priority(self, analyzer):
        usages = analyzer.extract_query_fields(SORT_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        if len(suggestions) >= 2:
            priority_order = {"high": 0, "medium": 1, "low": 2}
            for i in range(len(suggestions) - 1):
                assert priority_order[suggestions[i].priority] <= priority_order[
                    suggestions[i + 1].priority
                ]

    def test_empty_usages_returns_empty(self, analyzer):
        assert analyzer.generate_suggestions([]) == []

    def test_reference_count_tracked(self, analyzer):
        usages = analyzer.extract_query_fields(HEAVY_USAGE_QUERY, {}, "Test.java")
        suggestions = analyzer.generate_suggestions(usages)
        hot = [s for s in suggestions if "hotField" in s.fields and len(s.fields) == 1]
        assert len(hot) >= 1
        assert hot[0].reference_count == 5


# ---------------------------------------------------------------------------
# analyze_file (integration)
# ---------------------------------------------------------------------------


class TestAnalyzeFile:
    def test_returns_suggestions_for_query_file(self, analyzer, tmp_path):
        f = tmp_path / "CandidateDao.java"
        f.write_text(BASIC_DB_OBJECT_QUERY)
        suggestions = analyzer.analyze_file(str(f), {})
        assert len(suggestions) >= 1

    def test_returns_empty_for_no_patterns(self, analyzer, tmp_path):
        f = tmp_path / "PlainClass.java"
        f.write_text(NO_QUERY_PATTERNS)
        assert analyzer.analyze_file(str(f), {}) == []

    def test_nonexistent_file_returns_empty(self, analyzer):
        assert analyzer.analyze_file("/nonexistent/Foo.java", {}) == []

    def test_constant_resolution_end_to_end(self, analyzer, tmp_path):
        f = tmp_path / "CandidateDao.java"
        f.write_text(CONSTANT_REFERENCE_QUERY)
        constant_map = {
            "AppConstants.JOBIDS": "jobIds",
            "JOBIDS": "jobIds",
            "AppConstants.USERID": "userId",
            "USERID": "userId",
        }
        suggestions = analyzer.analyze_file(str(f), constant_map)
        all_fields = set()
        for s in suggestions:
            all_fields.update(s.fields.keys())
        assert "jobIds" in all_fields
        assert "userId" in all_fields

    def test_source_traceability(self, analyzer, tmp_path):
        f = tmp_path / "CandidateDao.java"
        f.write_text(BASIC_DB_OBJECT_QUERY)
        suggestions = analyzer.analyze_file(str(f), {})
        for s in suggestions:
            assert len(s.sample_locations) >= 1
            # sample_locations should contain filename:line format
            for loc in s.sample_locations:
                assert ":" in loc
