"""HTML report generator for PostgreSQL Guardrails.

Produces a standalone HTML file with all violations grouped by category,
severity color coding, summary counts, and gate decision indicator.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape

from .models import (
    GateDecision,
    GuardrailResult,
    Severity,
    Violation,
    ViolationCategory,
)


class HTMLReportGenerator:
    """Generates a standalone HTML report from guardrail analysis results."""

    # Severity color mapping
    _SEVERITY_COLORS: dict[Severity, str] = {
        Severity.CRITICAL: "#dc3545",  # red
        Severity.HIGH: "#fd7e14",  # orange
        Severity.MEDIUM: "#ffc107",  # yellow
    }

    _SEVERITY_TEXT_COLORS: dict[Severity, str] = {
        Severity.CRITICAL: "#ffffff",
        Severity.HIGH: "#ffffff",
        Severity.MEDIUM: "#212529",
    }

    def generate(self, result: GuardrailResult) -> str:
        """Produce a standalone HTML file with all violations and gate decision.

        Args:
            result: The aggregated guardrail analysis result.

        Returns:
            A complete HTML document string.
        """
        summary_html = self._render_summary(result)
        gate_html = self._render_gate_decision(result.gate_decision)
        categories_html = self._render_categories(result.violations)

        return self._wrap_document(summary_html, gate_html, categories_html, result)

    def _render_summary(self, result: GuardrailResult) -> str:
        """Render the summary section with violation counts by severity."""
        critical_count = sum(
            1 for v in result.violations if v.severity == Severity.CRITICAL
        )
        high_count = sum(
            1 for v in result.violations if v.severity == Severity.HIGH
        )
        medium_count = sum(
            1 for v in result.violations if v.severity == Severity.MEDIUM
        )
        total = len(result.violations)

        return f"""
        <div class="summary-section">
            <h2>Summary</h2>
            <div class="summary-grid">
                <div class="summary-card total">
                    <div class="summary-number">{total}</div>
                    <div class="summary-label">Total Violations</div>
                </div>
                <div class="summary-card critical">
                    <div class="summary-number">{critical_count}</div>
                    <div class="summary-label">Critical</div>
                </div>
                <div class="summary-card high">
                    <div class="summary-number">{high_count}</div>
                    <div class="summary-label">High</div>
                </div>
                <div class="summary-card medium">
                    <div class="summary-number">{medium_count}</div>
                    <div class="summary-label">Medium</div>
                </div>
            </div>
            <div class="scan-info">
                <span>Files scanned: {result.files_scanned}</span>
                <span>Migration files: {result.migration_files_scanned}</span>
                <span>Java files: {result.java_files_scanned}</span>
            </div>
        </div>
        """

    def _render_gate_decision(self, decision: GateDecision | None) -> str:
        """Render the gate decision section (pass/fail indicator)."""
        if decision is None:
            return """
            <div class="gate-section gate-unknown">
                <h2>Gate Decision</h2>
                <div class="gate-indicator">⚠ No gate decision available</div>
            </div>
            """

        if decision.passed:
            status_class = "gate-pass"
            status_icon = "✓"
            status_text = "PASSED"
        else:
            status_class = "gate-fail"
            status_icon = "✗"
            status_text = "FAILED"

        blocking_html = ""
        if decision.blocking_violations:
            blocking_items = "".join(
                f"<li>{escape(v.rule_id)}: {escape(v.description)}</li>"
                for v in decision.blocking_violations
            )
            blocking_html = f"""
            <div class="blocking-violations">
                <h3>Blocking Violations</h3>
                <ul>{blocking_items}</ul>
            </div>
            """

        return f"""
        <div class="gate-section {status_class}">
            <h2>Gate Decision</h2>
            <div class="gate-indicator">
                <span class="gate-icon">{status_icon}</span>
                <span class="gate-text">{status_text}</span>
            </div>
            <div class="gate-details">
                <span>Total: {decision.total_violations}</span>
                <span>Critical: {decision.critical_count}</span>
                <span>High: {decision.high_count}</span>
                <span>Medium: {decision.medium_count}</span>
            </div>
            {blocking_html}
        </div>
        """

    def _render_categories(self, violations: list[Violation]) -> str:
        """Render all violations grouped by category."""
        grouped: dict[ViolationCategory, list[Violation]] = defaultdict(list)
        for v in violations:
            grouped[v.category].append(v)

        sections = []
        for category in ViolationCategory:
            category_violations = grouped.get(category, [])
            if category_violations:
                sections.append(
                    self._render_category_section(
                        category.value, category_violations
                    )
                )

        if not sections:
            return """
            <div class="no-violations">
                <p>No violations detected. All checks passed.</p>
            </div>
            """

        return "\n".join(sections)

    def _render_category_section(
        self, category: str, violations: list[Violation]
    ) -> str:
        """Render a section for a single violation category."""
        cards = "\n".join(
            self._render_violation_card(v) for v in violations
        )
        return f"""
        <div class="category-section">
            <h2 class="category-title">{escape(category)}
                <span class="category-count">({len(violations)})</span>
            </h2>
            <div class="violation-cards">
                {cards}
            </div>
        </div>
        """

    def _render_violation_card(self, violation: Violation) -> str:
        """Render a single violation card."""
        severity_color = self._SEVERITY_COLORS[violation.severity]
        text_color = self._SEVERITY_TEXT_COLORS[violation.severity]

        auto_fix_html = ""
        if violation.auto_fix_sql:
            auto_fix_html = f"""
            <div class="auto-fix">
                <h4>Auto-Fix SQL</h4>
                <pre><code>{escape(violation.auto_fix_sql)}</code></pre>
            </div>
            """

        explain_html = ""
        if violation.explain_output:
            explain_html = f"""
            <div class="explain-output">
                <h4>EXPLAIN Plan</h4>
                <pre><code>{escape(violation.explain_output)}</code></pre>
            </div>
            """

        return f"""
        <div class="violation-card">
            <div class="violation-header">
                <span class="severity-badge" style="background-color: {severity_color}; color: {text_color};">
                    {escape(violation.severity.value)}
                </span>
                <span class="rule-id">{escape(violation.rule_id)}</span>
                <span class="action-badge">{escape(violation.action.value)}</span>
            </div>
            <div class="violation-location">
                <span class="file-path">{escape(violation.file_path)}</span>
                <span class="line-number">Line {violation.line_number}</span>
            </div>
            <div class="violation-description">
                <p>{escape(violation.description)}</p>
            </div>
            <div class="violation-remediation">
                <h4>Remediation</h4>
                <p>{escape(violation.remediation)}</p>
            </div>
            {auto_fix_html}
            {explain_html}
        </div>
        """

    def _wrap_document(
        self,
        summary_html: str,
        gate_html: str,
        categories_html: str,
        result: GuardrailResult,
    ) -> str:
        """Wrap content in a complete HTML document with embedded CSS."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PostgreSQL Guardrails Report - {escape(result.project_path)}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #212529;
            background-color: #f8f9fa;
            padding: 2rem;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        h1 {{
            color: #212529;
            margin-bottom: 1.5rem;
            font-size: 1.8rem;
            border-bottom: 2px solid #dee2e6;
            padding-bottom: 0.5rem;
        }}

        h2 {{
            color: #495057;
            margin-bottom: 1rem;
            font-size: 1.4rem;
        }}

        h3 {{
            color: #495057;
            margin-bottom: 0.5rem;
            font-size: 1.1rem;
        }}

        h4 {{
            color: #6c757d;
            margin-bottom: 0.3rem;
            font-size: 0.95rem;
        }}

        /* Summary Section */
        .summary-section {{
            background: #ffffff;
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
        }}

        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }}

        .summary-card {{
            text-align: center;
            padding: 1rem;
            border-radius: 6px;
            border: 1px solid #dee2e6;
        }}

        .summary-card.total {{
            background-color: #e9ecef;
            border-color: #adb5bd;
        }}

        .summary-card.critical {{
            background-color: #f8d7da;
            border-color: #f5c6cb;
        }}

        .summary-card.high {{
            background-color: #fff3cd;
            border-color: #ffc107;
        }}

        .summary-card.medium {{
            background-color: #fff9e6;
            border-color: #ffecb5;
        }}

        .summary-number {{
            font-size: 2rem;
            font-weight: bold;
            color: #212529;
        }}

        .summary-label {{
            font-size: 0.85rem;
            color: #6c757d;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .scan-info {{
            display: flex;
            gap: 1.5rem;
            color: #6c757d;
            font-size: 0.9rem;
            border-top: 1px solid #dee2e6;
            padding-top: 0.75rem;
        }}

        /* Gate Decision Section */
        .gate-section {{
            background: #ffffff;
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
            border-left: 4px solid #6c757d;
        }}

        .gate-section.gate-pass {{
            border-left-color: #28a745;
        }}

        .gate-section.gate-fail {{
            border-left-color: #dc3545;
        }}

        .gate-section.gate-unknown {{
            border-left-color: #ffc107;
        }}

        .gate-indicator {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 1.5rem;
            font-weight: bold;
            margin-bottom: 0.75rem;
        }}

        .gate-pass .gate-icon {{
            color: #28a745;
        }}

        .gate-fail .gate-icon {{
            color: #dc3545;
        }}

        .gate-pass .gate-text {{
            color: #28a745;
        }}

        .gate-fail .gate-text {{
            color: #dc3545;
        }}

        .gate-details {{
            display: flex;
            gap: 1.5rem;
            color: #6c757d;
            font-size: 0.9rem;
        }}

        .blocking-violations {{
            margin-top: 1rem;
            padding-top: 0.75rem;
            border-top: 1px solid #dee2e6;
        }}

        .blocking-violations ul {{
            list-style-type: disc;
            padding-left: 1.5rem;
            color: #dc3545;
        }}

        .blocking-violations li {{
            margin-bottom: 0.25rem;
        }}

        /* Category Sections */
        .category-section {{
            margin-bottom: 2rem;
        }}

        .category-title {{
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
        }}

        .category-count {{
            font-size: 1rem;
            color: #6c757d;
            font-weight: normal;
        }}

        /* Violation Cards */
        .violation-cards {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .violation-card {{
            background: #ffffff;
            border-radius: 8px;
            padding: 1.25rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
            border: 1px solid #dee2e6;
        }}

        .violation-header {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 0.75rem;
        }}

        .severity-badge {{
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            font-size: 0.8rem;
            font-weight: bold;
            text-transform: uppercase;
        }}

        .rule-id {{
            font-family: monospace;
            font-size: 0.9rem;
            color: #495057;
        }}

        .action-badge {{
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            background-color: #e9ecef;
            color: #495057;
            margin-left: auto;
        }}

        .violation-location {{
            display: flex;
            gap: 1rem;
            margin-bottom: 0.75rem;
            font-size: 0.85rem;
        }}

        .file-path {{
            font-family: monospace;
            color: #0d6efd;
        }}

        .line-number {{
            color: #6c757d;
        }}

        .violation-description {{
            margin-bottom: 0.75rem;
        }}

        .violation-description p {{
            color: #212529;
        }}

        .violation-remediation {{
            background-color: #f8f9fa;
            border-radius: 4px;
            padding: 0.75rem;
            margin-bottom: 0.5rem;
        }}

        .violation-remediation p {{
            color: #495057;
            font-size: 0.9rem;
        }}

        .auto-fix, .explain-output {{
            margin-top: 0.75rem;
        }}

        .auto-fix pre, .explain-output pre {{
            background-color: #282c34;
            color: #abb2bf;
            border-radius: 4px;
            padding: 1rem;
            overflow-x: auto;
            font-size: 0.85rem;
        }}

        .no-violations {{
            background: #d4edda;
            border-radius: 8px;
            padding: 2rem;
            text-align: center;
            color: #155724;
            font-size: 1.1rem;
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            body {{
                padding: 1rem;
            }}

            .summary-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}

            .scan-info, .gate-details {{
                flex-direction: column;
                gap: 0.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>PostgreSQL Guardrails Report</h1>
        {gate_html}
        {summary_html}
        {categories_html}
    </div>
</body>
</html>"""
