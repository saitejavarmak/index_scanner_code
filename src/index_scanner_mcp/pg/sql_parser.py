"""SQL parser for PostgreSQL migration files and queries.

This module provides a pure-Python SQL parser that tokenizes and classifies
SQL statements from migration files. It handles CREATE TABLE, ALTER TABLE,
DROP TABLE, CREATE INDEX, and DML statements (SELECT/INSERT/UPDATE/DELETE).
"""

from __future__ import annotations

import re
from pathlib import Path

from index_scanner_mcp.pg.models import (
    ColumnDef,
    ForeignKey,
    PgIndex,
    SQLQuery,
    SQLStatement,
    TableDefinition,
)


class SQLParser:
    """Parse SQL files into structured statement objects."""

    # Statement type patterns (order matters - more specific first)
    _STATEMENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        ("CREATE INDEX", re.compile(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX", re.IGNORECASE)),
        ("CREATE TABLE", re.compile(r"^\s*CREATE\s+TABLE", re.IGNORECASE)),
        ("ALTER TYPE", re.compile(r"^\s*ALTER\s+TYPE", re.IGNORECASE)),
        ("ALTER TABLE", re.compile(r"^\s*ALTER\s+TABLE", re.IGNORECASE)),
        ("DROP DATABASE", re.compile(r"^\s*DROP\s+DATABASE", re.IGNORECASE)),
        ("DROP TABLE", re.compile(r"^\s*DROP\s+TABLE", re.IGNORECASE)),
        ("TRUNCATE", re.compile(r"^\s*TRUNCATE", re.IGNORECASE)),
        ("VACUUM", re.compile(r"^\s*VACUUM", re.IGNORECASE)),
        ("CLUSTER", re.compile(r"^\s*CLUSTER", re.IGNORECASE)),
        ("REINDEX", re.compile(r"^\s*REINDEX", re.IGNORECASE)),
        ("ANALYZE", re.compile(r"^\s*ANALYZE", re.IGNORECASE)),
        ("SELECT", re.compile(r"^\s*SELECT", re.IGNORECASE)),
        ("INSERT", re.compile(r"^\s*INSERT", re.IGNORECASE)),
        ("UPDATE", re.compile(r"^\s*UPDATE", re.IGNORECASE)),
        ("DELETE", re.compile(r"^\s*DELETE", re.IGNORECASE)),
    ]

    def parse_file(self, filepath: str) -> list[SQLStatement]:
        """Parse a SQL file into structured statements."""
        content = Path(filepath).read_text(encoding="utf-8")
        return self.parse_sql(content, file_path=filepath)

    def parse_sql(self, sql: str, file_path: str = "<string>") -> list[SQLStatement]:
        """Parse a SQL string into structured statements."""
        statements: list[SQLStatement] = []
        for raw_sql, line_number in self._split_statements(sql):
            stmt_type = self._classify_statement(raw_sql)
            if not stmt_type:
                continue

            if stmt_type == "CREATE TABLE":
                stmt = self._parse_create_table(raw_sql, file_path, line_number)
            elif stmt_type == "CREATE INDEX":
                stmt = self._parse_create_index(raw_sql, file_path, line_number)
            elif stmt_type == "ALTER TABLE":
                stmt = self._parse_alter_table(raw_sql, file_path, line_number)
            elif stmt_type in ("DROP TABLE", "DROP DATABASE", "TRUNCATE",
                               "VACUUM", "CLUSTER", "REINDEX", "ANALYZE",
                               "ALTER TYPE"):
                stmt = self._parse_simple_statement(
                    raw_sql, file_path, line_number, stmt_type
                )
            else:
                # DML or unrecognized - store as generic statement
                stmt = SQLStatement(
                    statement_type=stmt_type,
                    raw_sql=raw_sql,
                    file_path=file_path,
                    line_number=line_number,
                    table_name=self._extract_dml_table(raw_sql, stmt_type),
                )
            statements.append(stmt)
        return statements

    def extract_tables(self, statements: list[SQLStatement]) -> list[TableDefinition]:
        """Extract table definitions from CREATE TABLE statements."""
        tables: list[TableDefinition] = []
        for stmt in statements:
            if stmt.statement_type != "CREATE TABLE" or not stmt.table_name:
                continue
            # Determine primary key columns
            pk_cols: list[str] = [
                col.name for col in stmt.columns if col.is_primary_key
            ]
            tables.append(
                TableDefinition(
                    name=stmt.table_name,
                    columns=stmt.columns,
                    primary_key=pk_cols,
                    foreign_keys=stmt.foreign_keys,
                    indexes=stmt.indexes,
                    file_path=stmt.file_path,
                    line_number=stmt.line_number,
                )
            )
        return tables

    def extract_queries(
        self, sql: str, file_path: str = "<string>"
    ) -> list[SQLQuery]:
        """Extract SELECT/INSERT/UPDATE/DELETE queries from SQL text."""
        queries: list[SQLQuery] = []
        for raw_sql, line_number in self._split_statements(sql):
            stmt_type = self._classify_statement(raw_sql)
            if stmt_type not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                continue
            query = self._parse_query(raw_sql, file_path, line_number, stmt_type)
            if query:
                queries.append(query)
        return queries

    # ─── Internal: Statement Splitting ───────────────────────────────────

    def _split_statements(self, sql: str) -> list[tuple[str, int]]:
        """Split SQL into individual statements, returning (sql, line_number) pairs.

        Respects string literals (single-quoted) and comments (-- and /* */).
        """
        statements: list[tuple[str, int]] = []
        current: list[str] = []
        # Track the line number where the current statement begins
        stmt_start_line = 1
        line_number = 1
        i = 0
        in_string = False
        in_line_comment = False
        in_block_comment = False
        found_stmt_start = False

        while i < len(sql):
            ch = sql[i]

            # Handle newlines for line counting
            if ch == "\n":
                line_number += 1
                if in_line_comment:
                    in_line_comment = False
                current.append(ch)
                i += 1
                continue

            # Inside a line comment - consume everything until newline
            if in_line_comment:
                current.append(ch)
                i += 1
                continue

            # Inside a block comment
            if in_block_comment:
                if ch == "*" and i + 1 < len(sql) and sql[i + 1] == "/":
                    in_block_comment = False
                    current.append("*/")
                    i += 2
                else:
                    current.append(ch)
                    i += 1
                continue

            # Inside a string literal
            if in_string:
                current.append(ch)
                if ch == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    # Escaped single quote
                    current.append("'")
                    i += 2
                elif ch == "'":
                    in_string = False
                    i += 1
                else:
                    i += 1
                continue

            # Check for start of string literal
            if ch == "'":
                in_string = True
                if not found_stmt_start:
                    stmt_start_line = line_number
                    found_stmt_start = True
                current.append(ch)
                i += 1
                continue

            # Check for line comment
            if ch == "-" and i + 1 < len(sql) and sql[i + 1] == "-":
                in_line_comment = True
                current.append("--")
                i += 2
                continue

            # Check for block comment
            if ch == "/" and i + 1 < len(sql) and sql[i + 1] == "*":
                in_block_comment = True
                current.append("/*")
                i += 2
                continue

            # Semicolon outside of strings/comments = statement boundary
            if ch == ";":
                stmt_text = "".join(current).strip()
                if stmt_text:
                    statements.append((stmt_text, stmt_start_line))
                current = []
                found_stmt_start = False
                stmt_start_line = line_number
                i += 1
                continue

            # Track where actual SQL content starts (not comments/whitespace)
            if not found_stmt_start and ch.strip():
                stmt_start_line = line_number
                found_stmt_start = True

            current.append(ch)
            i += 1

        # Handle final statement without trailing semicolon
        stmt_text = "".join(current).strip()
        if stmt_text:
            statements.append((stmt_text, stmt_start_line))

        return statements

    # ─── Internal: Statement Classification ──────────────────────────────

    def _classify_statement(self, sql: str) -> str:
        """Determine the statement type (CREATE TABLE, DROP TABLE, etc.)."""
        # Strip leading comments for classification
        cleaned = self._strip_leading_comments(sql)
        for stmt_type, pattern in self._STATEMENT_PATTERNS:
            if pattern.match(cleaned):
                return stmt_type
        return ""

    def _strip_leading_comments(self, sql: str) -> str:
        """Remove leading single-line and block comments from SQL."""
        result = sql.lstrip()
        while True:
            if result.startswith("--"):
                newline_idx = result.find("\n")
                if newline_idx == -1:
                    return ""
                result = result[newline_idx + 1:].lstrip()
            elif result.startswith("/*"):
                end_idx = result.find("*/")
                if end_idx == -1:
                    return ""
                result = result[end_idx + 2:].lstrip()
            else:
                break
        return result

    # ─── Internal: CREATE TABLE Parsing ──────────────────────────────────

    def _parse_create_table(
        self, sql: str, file_path: str, line_number: int
    ) -> SQLStatement:
        """Parse CREATE TABLE extracting columns, PKs, FKs, constraints."""
        # Extract table name
        table_match = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)",
            sql,
            re.IGNORECASE,
        )
        table_name = self._unquote(table_match.group(1)) if table_match else ""

        # Extract the parenthesized body
        body = self._extract_parenthesized_body(sql)

        columns: list[ColumnDef] = []
        foreign_keys: list[ForeignKey] = []
        indexes: list[PgIndex] = []
        pk_columns: list[str] = []

        if body:
            # Split body into top-level comma-separated elements
            elements = self._split_top_level_commas(body)
            for element in elements:
                elem = element.strip()
                if not elem:
                    continue
                elem_upper = elem.upper()

                # Table-level PRIMARY KEY constraint
                if re.match(
                    r"(CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY",
                    elem,
                    re.IGNORECASE,
                ):
                    pk_columns = self._extract_column_list(elem)
                # Table-level FOREIGN KEY constraint
                elif re.match(
                    r"(CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY",
                    elem,
                    re.IGNORECASE,
                ):
                    fk = self._parse_fk_constraint(elem, table_name)
                    if fk:
                        foreign_keys.append(fk)
                # Table-level UNIQUE constraint
                elif re.match(
                    r"(CONSTRAINT\s+\S+\s+)?UNIQUE", elem, re.IGNORECASE
                ):
                    # Treat as inline index
                    cols = self._extract_column_list(elem)
                    if cols:
                        idx_name = self._extract_constraint_name(elem)
                        if not idx_name:
                            idx_name = f"{table_name}_{'_'.join(cols)}_key"
                        indexes.append(
                            PgIndex(
                                name=idx_name,
                                table_name=table_name,
                                columns=cols,
                                unique=True,
                            )
                        )
                # CHECK constraint (table-level)
                elif re.match(
                    r"(CONSTRAINT\s+\S+\s+)?CHECK", elem, re.IGNORECASE
                ):
                    pass  # We note check constraints but don't model separately
                # EXCLUDE constraint
                elif "EXCLUDE" in elem_upper and re.match(
                    r"(CONSTRAINT\s+\S+\s+)?EXCLUDE", elem, re.IGNORECASE
                ):
                    pass
                else:
                    # It's a column definition
                    col = self._parse_column_def(elem)
                    if col:
                        columns.append(col)

        # Mark PK columns on column objects
        if pk_columns:
            for col in columns:
                if col.name.lower() in [pk.lower() for pk in pk_columns]:
                    col.is_primary_key = True

        # Also detect inline PK in column defs
        for col in columns:
            if col.is_primary_key and col.name.lower() not in [
                pk.lower() for pk in pk_columns
            ]:
                pk_columns.append(col.name)

        return SQLStatement(
            statement_type="CREATE TABLE",
            raw_sql=sql,
            file_path=file_path,
            line_number=line_number,
            table_name=table_name,
            columns=columns,
            indexes=indexes,
            foreign_keys=foreign_keys,
        )

    def _parse_column_def(self, col_sql: str) -> ColumnDef | None:
        """Parse a single column definition."""
        # Column format: name type [constraints...]
        # First token is the column name, then the data type, then constraints
        tokens = col_sql.split()
        if len(tokens) < 2:
            return None

        name = self._unquote(tokens[0])
        # Skip if name looks like a keyword (constraint, primary, foreign, etc.)
        if name.upper() in (
            "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
            "EXCLUDE", "INDEX",
        ):
            return None

        # Determine data type - could be multi-word (e.g., "TIMESTAMP WITH TIME ZONE")
        data_type, rest_start = self._extract_data_type(tokens[1:])
        if not data_type:
            return None

        # Parse remaining tokens for constraints
        rest = " ".join(tokens[1 + rest_start:])
        col_upper = col_sql.upper()

        is_pk = "PRIMARY KEY" in col_upper
        nullable = "NOT NULL" not in col_upper and not is_pk
        has_default = "DEFAULT" in col_upper
        is_unique = "UNIQUE" in col_upper and "PRIMARY" not in col_upper

        # Check for inline REFERENCES (inline FK)
        # We don't create a ForeignKey here - that's handled at table level
        check_constraint = self._extract_check_constraint(col_sql)

        return ColumnDef(
            name=name,
            data_type=data_type,
            nullable=nullable,
            has_default=has_default,
            is_primary_key=is_pk,
            is_unique=is_unique,
            check_constraint=check_constraint,
        )

    def _extract_data_type(self, tokens: list[str]) -> tuple[str, int]:
        """Extract the data type from tokens, handling multi-word types.

        Returns (data_type_string, number_of_tokens_consumed).
        """
        if not tokens:
            return ("", 0)

        first = tokens[0].upper()

        # Types with parenthesized params: VARCHAR(255), NUMERIC(10,2)
        # Check if first token contains or is followed by parenthesized portion
        type_parts = [tokens[0]]
        consumed = 1

        # Handle parenthesized type params like VARCHAR(255)
        if "(" in tokens[0] and ")" not in tokens[0]:
            # Paren spans multiple tokens
            for j in range(1, len(tokens)):
                type_parts.append(tokens[j])
                consumed += 1
                if ")" in tokens[j]:
                    break
            return (" ".join(type_parts), consumed)

        if "(" in tokens[0] and ")" in tokens[0]:
            return (tokens[0], 1)

        # Multi-word types
        multi_word_types = [
            ("DOUBLE", "PRECISION"),
            ("TIME", "ZONE"),
            ("CHARACTER", "VARYING"),
            ("BIT", "VARYING"),
        ]

        # TIMESTAMP/TIME WITH/WITHOUT TIME ZONE
        if first in ("TIMESTAMP", "TIME"):
            # Look ahead for WITH/WITHOUT TIME ZONE
            rest_upper = [t.upper() for t in tokens[1:]]
            if len(rest_upper) >= 3 and rest_upper[0] in ("WITH", "WITHOUT"):
                if rest_upper[1] == "TIME" and rest_upper[2] == "ZONE":
                    return (
                        " ".join(tokens[:4]),
                        4,
                    )
            return (tokens[0], 1)

        # DOUBLE PRECISION
        if first == "DOUBLE" and len(tokens) > 1:
            if tokens[1].upper() == "PRECISION":
                return ("DOUBLE PRECISION", 2)

        # CHARACTER VARYING(n)
        if first == "CHARACTER" and len(tokens) > 1:
            if tokens[1].upper().startswith("VARYING"):
                type_str = f"{tokens[0]} {tokens[1]}"
                consumed = 2
                if "(" in tokens[1] and ")" not in tokens[1]:
                    for j in range(2, len(tokens)):
                        type_str += f" {tokens[j]}"
                        consumed += 1
                        if ")" in tokens[j]:
                            break
                return (type_str, consumed)

        return (tokens[0], 1)

    # ─── Internal: CREATE INDEX Parsing ──────────────────────────────────

    def _parse_create_index(
        self, sql: str, file_path: str, line_number: int
    ) -> SQLStatement:
        """Parse CREATE INDEX extracting index definition."""
        is_unique = bool(
            re.search(r"CREATE\s+UNIQUE\s+INDEX", sql, re.IGNORECASE)
        )
        is_concurrent = bool(
            re.search(r"INDEX\s+CONCURRENTLY", sql, re.IGNORECASE)
        )

        # Extract index name
        # Pattern: CREATE [UNIQUE] INDEX [CONCURRENTLY] [IF NOT EXISTS] name ON ...
        name_match = re.search(
            r"INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+ON",
            sql,
            re.IGNORECASE,
        )
        index_name = self._unquote(name_match.group(1)) if name_match else ""

        # Extract table name
        table_match = re.search(
            r"\bON\s+(?:ONLY\s+)?(\S+)",
            sql,
            re.IGNORECASE,
        )
        table_name = self._unquote(table_match.group(1)) if table_match else ""

        # Extract USING method
        using_match = re.search(r"\bUSING\s+(\w+)", sql, re.IGNORECASE)
        index_type = using_match.group(1).lower() if using_match else "btree"

        # Extract column list from parentheses after table name (or after USING method)
        columns = self._extract_index_columns(sql)

        # Extract WHERE clause for partial index
        where_match = re.search(
            r"\bWHERE\s+(.+)$", sql, re.IGNORECASE
        )
        where_clause = where_match.group(1).strip() if where_match else None
        is_partial = where_clause is not None

        # Extract INCLUDE columns
        include_cols: list[str] = []
        include_match = re.search(
            r"\bINCLUDE\s*\(([^)]+)\)", sql, re.IGNORECASE
        )
        if include_match:
            include_cols = [
                self._unquote(c.strip())
                for c in include_match.group(1).split(",")
            ]

        # Build PgIndex object
        pg_index = None
        if index_name and table_name and columns:
            pg_index = PgIndex(
                name=index_name,
                table_name=table_name,
                columns=columns,
                unique=is_unique,
                index_type=index_type,
                is_partial=is_partial,
                where_clause=where_clause,
                include_columns=include_cols,
                concurrently=is_concurrent,
            )

        return SQLStatement(
            statement_type="CREATE INDEX",
            raw_sql=sql,
            file_path=file_path,
            line_number=line_number,
            table_name=table_name,
            indexes=[pg_index] if pg_index else [],
        )

    def _extract_index_columns(self, sql: str) -> list[str]:
        """Extract column names from CREATE INDEX column list."""
        # Find the columns parentheses - after ON table [USING method] (cols)
        # We need the first parenthesized group after ON table
        on_match = re.search(r"\bON\s+(?:ONLY\s+)?\S+", sql, re.IGNORECASE)
        if not on_match:
            return []

        after_on = sql[on_match.end():]
        # Skip optional USING clause
        using_match = re.match(r"\s*USING\s+\w+", after_on, re.IGNORECASE)
        if using_match:
            after_on = after_on[using_match.end():]

        # Find first parenthesized group
        paren_match = re.search(r"\(([^)]+)\)", after_on)
        if not paren_match:
            return []

        cols_str = paren_match.group(1)
        columns: list[str] = []
        for col in cols_str.split(","):
            col = col.strip()
            # Remove sort direction and NULLS FIRST/LAST
            col = re.sub(
                r"\s+(ASC|DESC|NULLS\s+(FIRST|LAST)).*$",
                "",
                col,
                flags=re.IGNORECASE,
            )
            col = col.strip()
            if col:
                columns.append(self._unquote(col))
        return columns

    # ─── Internal: ALTER TABLE Parsing ───────────────────────────────────

    def _parse_alter_table(
        self, sql: str, file_path: str, line_number: int
    ) -> SQLStatement:
        """Parse ALTER TABLE statements."""
        table_match = re.search(
            r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(\S+)",
            sql,
            re.IGNORECASE,
        )
        table_name = self._unquote(table_match.group(1)) if table_match else ""

        # Determine sub-type for more precise classification
        sql_upper = sql.upper()
        if "DROP COLUMN" in sql_upper:
            stmt_type = "DROP COLUMN"
        elif "ADD COLUMN" in sql_upper:
            stmt_type = "ADD COLUMN"
        elif "RENAME" in sql_upper:
            stmt_type = "ALTER TABLE RENAME"
        elif "SET DATA TYPE" in sql_upper or "ALTER COLUMN" in sql_upper and "TYPE" in sql_upper:
            stmt_type = "ALTER COLUMN TYPE"
        elif "ALTER TYPE" in sql_upper:
            stmt_type = "ALTER TYPE"
        else:
            stmt_type = "ALTER TABLE"

        return SQLStatement(
            statement_type=stmt_type,
            raw_sql=sql,
            file_path=file_path,
            line_number=line_number,
            table_name=table_name,
        )

    # ─── Internal: Simple Statement Parsing ──────────────────────────────

    def _parse_simple_statement(
        self, sql: str, file_path: str, line_number: int, stmt_type: str
    ) -> SQLStatement:
        """Parse DROP TABLE, DROP DATABASE, TRUNCATE, VACUUM, etc."""
        table_name: str | None = None

        if stmt_type == "DROP TABLE":
            m = re.search(
                r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\S+)",
                sql,
                re.IGNORECASE,
            )
            table_name = self._unquote(m.group(1)) if m else None
        elif stmt_type == "DROP DATABASE":
            m = re.search(
                r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?(\S+)",
                sql,
                re.IGNORECASE,
            )
            table_name = self._unquote(m.group(1)) if m else None
        elif stmt_type == "TRUNCATE":
            m = re.search(
                r"TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(\S+)",
                sql,
                re.IGNORECASE,
            )
            table_name = self._unquote(m.group(1)) if m else None

        return SQLStatement(
            statement_type=stmt_type,
            raw_sql=sql,
            file_path=file_path,
            line_number=line_number,
            table_name=table_name,
        )

    # ─── Internal: DML / Query Parsing ───────────────────────────────────

    def _extract_dml_table(self, sql: str, stmt_type: str) -> str | None:
        """Extract the primary table name from a DML statement."""
        if stmt_type == "SELECT":
            m = re.search(r"\bFROM\s+(\S+)", sql, re.IGNORECASE)
            return self._unquote(m.group(1)) if m else None
        elif stmt_type == "INSERT":
            m = re.search(r"\bINTO\s+(\S+)", sql, re.IGNORECASE)
            return self._unquote(m.group(1)) if m else None
        elif stmt_type == "UPDATE":
            m = re.search(r"\bUPDATE\s+(\S+)", sql, re.IGNORECASE)
            return self._unquote(m.group(1)) if m else None
        elif stmt_type == "DELETE":
            m = re.search(r"\bFROM\s+(\S+)", sql, re.IGNORECASE)
            return self._unquote(m.group(1)) if m else None
        return None

    def _parse_query(
        self, sql: str, file_path: str, line_number: int, query_type: str
    ) -> SQLQuery | None:
        """Parse a DML statement into a SQLQuery object."""
        tables = self._extract_query_tables(sql, query_type)
        where_columns = self._extract_where_columns(sql)
        join_conditions = self._extract_join_conditions(sql)
        order_by_columns = self._extract_order_by_columns(sql)

        has_where = bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
        has_limit = bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))

        offset_value: int | None = None
        offset_match = re.search(r"\bOFFSET\s+(\d+)", sql, re.IGNORECASE)
        if offset_match:
            offset_value = int(offset_match.group(1))

        return SQLQuery(
            raw_sql=sql,
            file_path=file_path,
            line_number=line_number,
            query_type=query_type,
            tables=tables,
            where_columns=where_columns,
            join_conditions=join_conditions,
            order_by_columns=order_by_columns,
            has_where=has_where,
            has_limit=has_limit,
            offset_value=offset_value,
        )

    def _extract_query_tables(self, sql: str, query_type: str) -> list[str]:
        """Extract all table names referenced in a query."""
        tables: list[str] = []

        if query_type == "INSERT":
            m = re.search(r"\bINTO\s+(\S+)", sql, re.IGNORECASE)
            if m:
                tables.append(self._unquote(m.group(1)))
            return tables

        if query_type == "UPDATE":
            m = re.search(r"\bUPDATE\s+(\S+)", sql, re.IGNORECASE)
            if m:
                tables.append(self._unquote(m.group(1)))
            # Also check FROM in UPDATE ... FROM
            for fm in re.finditer(r"\bFROM\s+(\S+)", sql, re.IGNORECASE):
                t = self._unquote(fm.group(1))
                if t.upper() not in ("SELECT", "WHERE", "(") and t not in tables:
                    tables.append(t)
            return tables

        # SELECT and DELETE - extract FROM and JOIN tables
        # FROM clause
        from_match = re.search(
            r"\bFROM\s+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|\bUNION\b|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if from_match:
            from_clause = from_match.group(1)
            # Remove JOIN parts from the from_clause for the base tables
            # Split on JOIN keywords
            parts = re.split(
                r"\b(?:INNER|LEFT|RIGHT|FULL|CROSS|NATURAL)?\s*JOIN\b",
                from_clause,
                flags=re.IGNORECASE,
            )
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # First word (possibly with alias) is the table name
                # Handle "table AS alias" or "table alias"
                # Also handle ON clause remnants
                part = re.split(r"\bON\b", part, flags=re.IGNORECASE)[0].strip()
                for tbl_part in part.split(","):
                    tbl_part = tbl_part.strip()
                    if not tbl_part:
                        continue
                    tbl_name = tbl_part.split()[0]
                    tbl_name = self._unquote(tbl_name)
                    if tbl_name.upper() not in (
                        "SELECT", "WHERE", "ON", "(", ")", ""
                    ) and tbl_name not in tables:
                        tables.append(tbl_name)

        return tables

    def _extract_where_columns(self, sql: str) -> list[str]:
        """Extract column names referenced in WHERE clause."""
        where_match = re.search(
            r"\bWHERE\s+(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|\bUNION\b|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not where_match:
            return []

        where_clause = where_match.group(1)
        columns: list[str] = []

        # Find patterns: column = value, column > value, column IN (...), etc.
        # Pattern: word before comparison operator
        col_pattern = re.finditer(
            r"(\w+(?:\.\w+)?)\s*(?:=|!=|<>|>=|<=|>|<|(?:NOT\s+)?IN\s*\(|(?:NOT\s+)?LIKE|IS\s+(?:NOT\s+)?NULL|BETWEEN)",
            where_clause,
            re.IGNORECASE,
        )
        for match in col_pattern:
            col = match.group(1)
            # Filter out SQL keywords and values
            if col.upper() not in (
                "AND", "OR", "NOT", "NULL", "TRUE", "FALSE",
                "EXISTS", "ANY", "ALL", "SOME",
            ):
                # Handle table.column format - take just column
                if "." in col:
                    col = col.split(".")[-1]
                if col not in columns:
                    columns.append(col)

        return columns

    def _extract_join_conditions(self, sql: str) -> list[str]:
        """Extract column references from JOIN ON conditions."""
        conditions: list[str] = []
        # Find all ON clauses after JOINs
        join_on_pattern = re.finditer(
            r"\bJOIN\s+\S+(?:\s+\S+)?\s+ON\s+(.+?)(?:\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        for match in join_on_pattern:
            on_clause = match.group(1).strip()
            # Extract column references (table.column = table.column)
            col_refs = re.findall(r"(\w+\.\w+)", on_clause)
            for ref in col_refs:
                col = ref.split(".")[-1]
                if col not in conditions:
                    conditions.append(col)
        return conditions

    def _extract_order_by_columns(self, sql: str) -> list[str]:
        """Extract column names from ORDER BY clause."""
        order_match = re.search(
            r"\bORDER\s+BY\s+(.+?)(?:\bLIMIT\b|\bOFFSET\b|\bFETCH\b|\bFOR\b|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not order_match:
            return []

        order_clause = order_match.group(1)
        columns: list[str] = []
        for part in order_clause.split(","):
            part = part.strip()
            # Remove ASC/DESC and NULLS FIRST/LAST
            part = re.sub(
                r"\s+(ASC|DESC|NULLS\s+(FIRST|LAST)).*$",
                "",
                part,
                flags=re.IGNORECASE,
            )
            part = part.strip()
            if part:
                # Handle table.column
                if "." in part:
                    part = part.split(".")[-1]
                # Skip if it looks like a function call or number
                if re.match(r"^\w+$", part) and not part.isdigit():
                    columns.append(part)
        return columns

    # ─── Internal: Utility Methods ───────────────────────────────────────

    def _extract_parenthesized_body(self, sql: str) -> str:
        """Extract the content between the outermost parentheses in SQL."""
        # Find the first opening paren
        depth = 0
        start = -1
        for i, ch in enumerate(sql):
            if ch == "(":
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and start != -1:
                    return sql[start:i]
        return ""

    def _split_top_level_commas(self, body: str) -> list[str]:
        """Split a parenthesized body by commas at the top level only.

        Does not split commas inside nested parentheses or strings.
        """
        elements: list[str] = []
        current: list[str] = []
        depth = 0
        in_string = False

        for ch in body:
            if in_string:
                current.append(ch)
                if ch == "'" :
                    in_string = False
                continue

            if ch == "'":
                in_string = True
                current.append(ch)
                continue

            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                elements.append("".join(current))
                current = []
            else:
                current.append(ch)

        if current:
            elements.append("".join(current))

        return elements

    def _extract_column_list(self, element: str) -> list[str]:
        """Extract column names from a parenthesized list in a constraint."""
        paren_match = re.search(r"\(([^)]+)\)", element)
        if not paren_match:
            return []
        return [
            self._unquote(c.strip())
            for c in paren_match.group(1).split(",")
            if c.strip()
        ]

    def _extract_constraint_name(self, element: str) -> str:
        """Extract constraint name from CONSTRAINT name ... pattern."""
        m = re.match(r"CONSTRAINT\s+(\S+)", element, re.IGNORECASE)
        return self._unquote(m.group(1)) if m else ""

    def _parse_fk_constraint(
        self, element: str, source_table: str
    ) -> ForeignKey | None:
        """Parse a FOREIGN KEY constraint element."""
        # Pattern: [CONSTRAINT name] FOREIGN KEY (cols) REFERENCES target(cols)
        fk_match = re.search(
            r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+(\S+)\s*\(([^)]+)\)",
            element,
            re.IGNORECASE,
        )
        if not fk_match:
            return None

        source_cols = [
            self._unquote(c.strip()) for c in fk_match.group(1).split(",")
        ]
        target_table = self._unquote(fk_match.group(2))
        target_cols = [
            self._unquote(c.strip()) for c in fk_match.group(3).split(",")
        ]
        constraint_name = self._extract_constraint_name(element) or None

        return ForeignKey(
            constraint_name=constraint_name,
            source_table=source_table,
            source_columns=source_cols,
            target_table=target_table,
            target_columns=target_cols,
        )

    def _unquote(self, identifier: str) -> str:
        """Remove surrounding quotes from a SQL identifier."""
        if not identifier:
            return identifier
        # Remove double quotes
        if identifier.startswith('"') and identifier.endswith('"'):
            return identifier[1:-1]
        # Remove schema prefix trailing comma/paren
        identifier = identifier.rstrip(",).;")
        return identifier

    def _extract_check_constraint(self, col_sql: str) -> str | None:
        """Extract CHECK constraint body, handling nested parentheses."""
        check_match = re.search(r"\bCHECK\s*\(", col_sql, re.IGNORECASE)
        if not check_match:
            return None
        start = check_match.end()
        depth = 1
        i = start
        while i < len(col_sql) and depth > 0:
            if col_sql[i] == "(":
                depth += 1
            elif col_sql[i] == ")":
                depth -= 1
            i += 1
        if depth == 0:
            return col_sql[start : i - 1]
        return None
