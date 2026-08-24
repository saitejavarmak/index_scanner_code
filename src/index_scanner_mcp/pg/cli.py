"""CLI entry point for PostgreSQL Guardrails Scanner.

Provides the `pg_guardrails` console command used by Jenkins pipelines
and local developer invocations. Parses arguments, loads configuration,
runs the guardrail engine, generates reports, and exits with appropriate codes.

Exit codes:
    0 - All checks passed (no blocking violations)
    1 - Blocking violations found (or unexpected error)
    2 - Invalid arguments
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from index_scanner_mcp.pg.config_loader import AuroraConnectionConfig, ConfigLoader
from index_scanner_mcp.pg.engine import PostgresGuardrailEngine
from index_scanner_mcp.pg.html_report_generator import HTMLReportGenerator
from index_scanner_mcp.pg.json_report_generator import JSONReportGenerator
from index_scanner_mcp.pg.team_scanner import TeamScanResult, TeamScanner


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the pg_guardrails CLI."""
    parser = argparse.ArgumentParser(
        prog="pg_guardrails",
        description="PostgreSQL Guardrails Scanner - Detect migration, schema, index, performance, and application code violations.",
    )

    parser.add_argument(
        "project_path",
        nargs="?",
        default=None,
        help="Path to the project root directory to scan (not required when --team and --catalog are provided)",
    )

    parser.add_argument(
        "--team",
        default=None,
        help="Team name to scan all services from catalog",
    )

    parser.add_argument(
        "--catalog",
        default=None,
        help="Path to service catalog CSV file (required with --team)",
    )

    parser.add_argument(
        "--config",
        default=".guardrails.yml",
        help="Path to the guardrails configuration file (default: .guardrails.yml)",
    )

    parser.add_argument(
        "--output-dir",
        default="guardrail-reports",
        help="Output directory for generated reports (default: guardrail-reports)",
    )

    parser.add_argument(
        "--fail-on-block",
        action="store_true",
        default=True,
        help="Exit with code 1 when blocking violations are found (default: True)",
    )

    # Aurora runtime connection options
    parser.add_argument(
        "--aurora-host",
        default=None,
        help="Aurora PostgreSQL host for optional runtime checks",
    )

    parser.add_argument(
        "--aurora-port",
        type=int,
        default=5432,
        help="Aurora PostgreSQL port (default: 5432)",
    )

    parser.add_argument(
        "--aurora-db",
        default=None,
        help="Aurora database name for runtime checks",
    )

    parser.add_argument(
        "--aurora-user",
        default=None,
        help="Aurora username for runtime checks",
    )

    return parser


def main() -> None:
    """Entry point for the pg_guardrails console script."""
    parser = _build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Team / catalog scan mode
    # ------------------------------------------------------------------
    if args.team and args.catalog:
        catalog_path = Path(args.catalog)
        if not catalog_path.exists():
            print(
                f"Error: Catalog file does not exist: {args.catalog}",
                file=sys.stderr,
            )
            sys.exit(2)

        # Load configuration (resolve relative to cwd)
        config_loader = ConfigLoader()
        cfg_path: str | None = args.config
        if cfg_path and not os.path.isabs(cfg_path) and not Path(cfg_path).exists():
            cfg_path = None  # fall back to defaults
        config = config_loader.load(cfg_path)

        # Run team scan
        try:
            team_scanner = TeamScanner(args.team, str(catalog_path), config)
            team_result = team_scanner.scan()
        except Exception as e:
            print(f"Error: Team scan failed: {e}", file=sys.stderr)
            sys.exit(1)

        # Create output directory
        output_dir = Path(args.output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(
                f"Error: Cannot create output directory '{args.output_dir}': {e}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Write per-team summary JSON report
        team_name_safe = args.team.replace(" ", "_").replace("/", "_")
        team_report_path = output_dir / f"team_{team_name_safe}_report.json"
        team_report_data = {
            "team_name": team_result.team_name,
            "total_services": team_result.total_services,
            "scanned_services": team_result.scanned_services,
            "skipped_services": team_result.skipped_services,
            "postgres_services": team_result.postgres_services,
            "mongodb_services": team_result.mongodb_services,
            "total_violations": len(team_result.all_violations),
            "gate_decision": (
                {
                    "passed": team_result.gate_decision.passed,
                    "total_violations": team_result.gate_decision.total_violations,
                    "critical_count": team_result.gate_decision.critical_count,
                    "high_count": team_result.gate_decision.high_count,
                    "medium_count": team_result.gate_decision.medium_count,
                }
                if team_result.gate_decision
                else None
            ),
            "violations_by_service": {
                svc: [
                    {
                        "rule_id": v.rule_id,
                        "category": v.category.value,
                        "severity": v.severity.value,
                        "action": v.action.value,
                        "file_path": v.file_path,
                        "line_number": v.line_number,
                        "description": v.description,
                        "remediation": v.remediation,
                    }
                    for v in viols
                ]
                for svc, viols in team_result.violations_by_service.items()
            },
            "errors": team_result.errors,
        }
        team_report_path.write_text(
            json.dumps(team_report_data, indent=2), encoding="utf-8"
        )

        # Generate per-service HTML reports
        html_generator = HTMLReportGenerator()
        json_generator = JSONReportGenerator()

        from index_scanner_mcp.pg.gate_decision import GateDecisionEvaluator
        from index_scanner_mcp.pg.models import GuardrailResult

        gate_evaluator = GateDecisionEvaluator()

        for service_name, service_violations in team_result.violations_by_service.items():
            # Build a minimal GuardrailResult-like object for report generation
            try:
                svc_result = GuardrailResult(
                    project_path=service_name,
                    violations=service_violations,
                    gate_decision=gate_evaluator.evaluate(service_violations)
                    if service_violations
                    else None,
                )
                safe_name = service_name.replace(" ", "_").replace("/", "_")
                svc_html = html_generator.generate(svc_result)
                (output_dir / f"service_{safe_name}_report.html").write_text(
                    svc_html, encoding="utf-8"
                )
                svc_json = json_generator.generate(svc_result)
                (output_dir / f"service_{safe_name}_report.json").write_text(
                    svc_json, encoding="utf-8"
                )
            except Exception as e:
                print(
                    f"Warning: Could not generate report for service '{service_name}': {e}",
                    file=sys.stderr,
                )

        # Print team summary to stdout
        _print_team_summary(team_result)

        # Exit with appropriate code
        if (
            team_result.gate_decision
            and not team_result.gate_decision.passed
            and args.fail_on_block
        ):
            sys.exit(1)
        sys.exit(0)

    if args.team and not args.catalog:
        print("Error: --catalog is required when using --team", file=sys.stderr)
        sys.exit(2)

    # ------------------------------------------------------------------
    # Single-project scan mode (existing behaviour)
    # ------------------------------------------------------------------
    if args.project_path is None:
        print(
            "Error: project_path is required when --team and --catalog are not provided",
            file=sys.stderr,
        )
        sys.exit(2)

    # Validate project path exists
    project_path = Path(args.project_path)
    if not project_path.exists():
        print(f"Error: Project path does not exist: {args.project_path}", file=sys.stderr)
        sys.exit(2)

    if not project_path.is_dir():
        print(f"Error: Project path is not a directory: {args.project_path}", file=sys.stderr)
        sys.exit(2)

    # Load configuration
    config_loader = ConfigLoader()
    config_path = args.config

    # Resolve config path relative to project if not absolute
    if not os.path.isabs(config_path):
        resolved_config = project_path / config_path
        if resolved_config.exists():
            config_path = str(resolved_config)
        else:
            # Fall back to the literal path provided
            config_path = config_path if Path(config_path).exists() else None

    config = config_loader.load(config_path)

    # Update Aurora connection from CLI args if provided
    if args.aurora_host:
        config.aurora_connection = AuroraConnectionConfig(
            host=args.aurora_host,
            port=args.aurora_port,
            database=args.aurora_db or "",
            username=args.aurora_user or "",
        )

    # Create and run the engine
    try:
        engine = PostgresGuardrailEngine(config)
        result = engine.run_analysis(str(project_path))
    except Exception as e:
        print(f"Error: Engine execution failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    output_dir = Path(args.output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Error: Cannot create output directory '{args.output_dir}': {e}", file=sys.stderr)
        sys.exit(1)

    # Generate HTML report
    html_generator = HTMLReportGenerator()
    html_content = html_generator.generate(result)
    html_path = output_dir / "guardrails_report.html"
    html_path.write_text(html_content, encoding="utf-8")

    # Generate JSON report
    json_generator = JSONReportGenerator()
    json_content = json_generator.generate(result)
    json_path = output_dir / "guardrails_report.json"
    json_path.write_text(json_content, encoding="utf-8")

    # Print summary to stdout
    _print_summary(result)

    # Determine exit code
    if result.gate_decision and not result.gate_decision.passed and args.fail_on_block:
        sys.exit(1)

    sys.exit(0)


def _print_summary(result) -> None:
    """Print a concise summary of the guardrail analysis to stdout."""
    print("\n" + "=" * 60)
    print("PostgreSQL Guardrails - Analysis Summary")
    print("=" * 60)
    print(f"Project: {result.project_path}")
    print(f"Files scanned: {result.files_scanned}")
    print(f"  Migration files: {result.migration_files_scanned}")
    print(f"  Java files: {result.java_files_scanned}")
    print(f"Runtime checks: {'Yes' if result.runtime_checks_performed else 'No'}")
    print()

    total = len(result.violations)
    critical = sum(1 for v in result.violations if v.severity.value == "Critical")
    high = sum(1 for v in result.violations if v.severity.value == "High")
    medium = sum(1 for v in result.violations if v.severity.value == "Medium")

    print(f"Violations: {total} total")
    print(f"  Critical: {critical}")
    print(f"  High:     {high}")
    print(f"  Medium:   {medium}")
    print()

    if result.gate_decision:
        status = "PASSED ✓" if result.gate_decision.passed else "FAILED ✗"
        print(f"Gate Decision: {status}")
        if not result.gate_decision.passed:
            print(f"  Blocking violations: {len(result.gate_decision.blocking_violations)}")
    else:
        print("Gate Decision: N/A")

    if result.errors:
        print(f"\nWarnings/Errors: {len(result.errors)}")
        for err in result.errors[:5]:
            print(f"  - {err}")
        if len(result.errors) > 5:
            print(f"  ... and {len(result.errors) - 5} more")

    print("=" * 60)


def _print_team_summary(result: TeamScanResult) -> None:
    """Print a concise team-level summary of the guardrail analysis to stdout."""
    print("\n" + "=" * 60)
    print(f"PostgreSQL Guardrails - Team Scan Summary: {result.team_name}")
    print("=" * 60)
    print(f"Total services: {result.total_services}")
    print(f"Scanned services: {result.scanned_services}")
    print(f"Skipped services: {result.skipped_services}")
    print(f"PostgreSQL services: {len(result.postgres_services)}")
    print(f"MongoDB services: {len(result.mongodb_services)}")
    print()
    print(f"Total violations: {len(result.all_violations)}")
    if result.gate_decision:
        status = "PASSED ✓" if result.gate_decision.passed else "FAILED ✗"
        print(f"Gate Decision: {status}")
    if result.errors:
        print(f"\nErrors: {len(result.errors)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
