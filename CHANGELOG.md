# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-08-10

### Added

**MongoDB index discovery**
- Spring Data annotation parsing: `@Indexed`, `@CompoundIndex`, `@CompoundIndexes`,
  `@TextIndexed`, `@GeoSpatialIndexed`, `@HashIndexed`, `@WildcardIndexed`
- Programmatic index detection: `createIndex()`, `ensureIndex()`, `IndexOperations`, `IndexModel`
- JPA/Hibernate `@Index` and `@Table(indexes=...)` support
- SQL DDL and Elasticsearch `CreateIndexRequest` detection
- Java constant resolution so constant-referenced field names resolve to real fields
- Multi-language file discovery: Java, Kotlin, Python, JavaScript, TypeScript, SQL, XML, YAML, JSON, properties

**Index suggestions**
- Query pattern analysis across `.find()`, `.aggregate()`, `.updateOne()`, `.deleteMany()`
- Filter and sort field extraction, including `Filters.*` and `Sorts.*` builders
- Single-field and compound index suggestions with high/medium/low priority

**PostgreSQL guardrails**
- Migration scanner: destructive DDL, non-concurrent index builds, missing rollbacks
- Schema analyzer: missing PK/FK, circular references, problematic data types
- Index analyzer: duplicates, prefix overlaps, unindexed foreign keys, naming violations
- Performance scanner: `SELECT *`, missing `WHERE`, leading-wildcard `LIKE`, cartesian joins
- Application code scanner: SQL injection risk patterns
- Combined full scan with pass/fail gate decision for CI
- Configurable rules and severities via `.guardrails.yml`

**Interfaces**
- MCP server exposing 15 tools over stdio and SSE transports
- CLI with `scan`, `suggest`, `export`, `serve` commands plus `pg-guardrails`
- Python library API (`ScannerEngine`, `ScriptGenerator`, `ReportGenerator`)
- React + Flask web UI for scanning, comparing, and exporting

**Output**
- Executable script generation: MongoDB shell and pymongo
- Structured JSON reports
- HTML reports for PostgreSQL guardrail findings

**Team-wide scanning**
- Service catalog driven discovery: scan every service owned by a team
- Helm values parsing for database context
- Bitbucket repo cloning with configurable org and service-name prefixes

**Packaging**
- Docker images for the scanner and the UI
- Kubernetes deployment manifest
- 326 tests
