"""CLI entry point for index-scanner-mcp."""

from __future__ import annotations

import argparse
import json
import sys

from index_scanner_mcp.scanner_engine import ScannerEngine
from index_scanner_mcp.script_generator import ScriptGenerator
from index_scanner_mcp.report_generator import ReportGenerator


def _format_table_scan(result) -> str:
    lines = [f"Scanned: {result.project_path}", f"Files: {result.files_scanned}", ""]
    if result.indexes:
        lines.append(f"Indexes found: {len(result.indexes)}")
        lines.append(f"{'Collection':<30} {'Fields':<40} {'Type':<12} {'Source'}")
        lines.append("-" * 100)
        for idx in result.indexes:
            fields = ", ".join(f"{k}:{v}" for k, v in idx.fields.items())
            src = f"{idx.source.file}:{idx.source.line}" if idx.source else ""
            lines.append(f"{idx.collection:<30} {fields:<40} {idx.index_type:<12} {src}")
    else:
        lines.append("No indexes found.")
    if result.errors:
        lines.append(f"\nErrors: {len(result.errors)}")
    return "\n".join(lines)


def _format_table_suggest(result) -> str:
    lines = [f"Scanned: {result.project_path}", ""]
    if result.suggestions:
        lines.append(f"Suggestions: {len(result.suggestions)}")
        lines.append(f"{'Priority':<10} {'Collection':<25} {'Fields':<35} {'Refs':<6} {'Rationale'}")
        lines.append("-" * 110)
        for s in result.suggestions:
            fields = ", ".join(f"{k}:{v}" for k, v in s.fields.items())
            lines.append(f"{s.priority:<10} {s.collection:<25} {fields:<35} {s.reference_count:<6} {s.rationale[:40]}")
    else:
        lines.append("No suggestions.")
    return "\n".join(lines)


def cmd_scan(args):
    engine = ScannerEngine()
    result = engine.scan_project(args.path)
    if result.errors and not result.indexes and not result.suggestions:
        print(f"Error: {result.errors[0]}", file=sys.stderr)
        sys.exit(1)
    if args.format == "json":
        rg = ReportGenerator()
        print(json.dumps(rg.generate_report(result), indent=2))
    else:
        print(_format_table_scan(result))


def cmd_export(args):
    engine = ScannerEngine()
    result = engine.scan_project(args.path)
    if result.errors and not result.indexes:
        print(f"Error: {result.errors[0]}", file=sys.stderr)
        sys.exit(1)
    if not result.indexes:
        print("No indexes found to export.", file=sys.stderr)
        sys.exit(1)
    sg = ScriptGenerator()
    if args.format == "mongo_shell":
        script = sg.generate_mongo_shell(result.indexes, db_name=args.db_name)
    else:
        script = sg.generate_pymongo(result.indexes, db_name=args.db_name)
    if args.output:
        with open(args.output, "w") as f:
            f.write(script)
        print(f"Script written to {args.output}")
    else:
        print(script)


def cmd_suggest(args):
    engine = ScannerEngine()
    result = engine.scan_project(args.path)
    if result.errors and not result.indexes and not result.suggestions:
        print(f"Error: {result.errors[0]}", file=sys.stderr)
        sys.exit(1)
    if args.format == "json":
        rg = ReportGenerator()
        report = rg.generate_report(result)
        print(json.dumps({"suggestions": report["suggestions"]}, indent=2))
    else:
        print(_format_table_suggest(result))


def cmd_serve(args):
    from index_scanner_mcp.server import mcp as mcp_server
    transport = getattr(args, "transport", "stdio")
    if transport == "sse":
        port = getattr(args, "port", 8000)
        mcp_server.run(transport="sse", port=port)
    else:
        mcp_server.run()


def main():
    parser = argparse.ArgumentParser(prog="index-scanner-mcp", description="Scan codebases for MongoDB index definitions")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Discover index definitions")
    p_scan.add_argument("path", help="Project directory to scan")
    p_scan.add_argument("--format", choices=["json", "table"], default="table")
    p_scan.set_defaults(func=cmd_scan)

    # export
    p_export = sub.add_parser("export", help="Generate executable scripts")
    p_export.add_argument("path", help="Project directory to scan")
    p_export.add_argument("--format", choices=["mongo_shell", "pymongo"], required=True)
    p_export.add_argument("--db-name", default=None)
    p_export.add_argument("--output", default=None)
    p_export.set_defaults(func=cmd_export)

    # suggest
    p_suggest = sub.add_parser("suggest", help="Get index suggestions")
    p_suggest.add_argument("path", help="Project directory to scan")
    p_suggest.add_argument("--format", choices=["json", "table"], default="table")
    p_suggest.set_defaults(func=cmd_suggest)

    # serve
    p_serve = sub.add_parser("serve", help="Start MCP server")
    p_serve.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
