"""Planner routing evaluation harness.

Sends edit instructions through the planning LLM against various document types
and checks whether the routing decision (programmatic vs visual) is correct.

These are TEXT-ONLY Gemini Flash calls — no image generation. Cost is negligible.
Run this after every prompt change.

Usage:
    cd backend
    python -m tests.test_routing_eval           # run all cases
    python -m tests.test_routing_eval --quick   # run first 10 only
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.schemas import ExecutionPlan
from app.prompts.orchestrator_plan import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    ORCHESTRATOR_USER_TEMPLATE,
)
from app.services.model_provider import GeminiProvider
from app.services.orchestrator import _parse_plan_json


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

@dataclass
class RoutingTestCase:
    instruction: str
    doc_type: str
    expected_path: str  # "programmatic", "visual", or "hybrid"
    description: str


ROUTING_TEST_CASES: list[RoutingTestCase] = [
    # === CLEAR PROGRAMMATIC CASES ===
    RoutingTestCase(
        "Change 2024 to 2025",
        "resume", "programmatic", "Same-length year swap"),
    RoutingTestCase(
        "Change Q3 to Q4",
        "resume", "programmatic", "Same-length quarter swap"),
    RoutingTestCase(
        "Fix the typo: change 'managment' to 'management'",
        "resume", "programmatic", "Typo fix, nearly same length"),
    RoutingTestCase(
        "Replace 'John Smith' with 'Jane Smith'",
        "resume", "programmatic", "Name swap, same length"),
    RoutingTestCase(
        "Change the phone number from 555-1234 to 555-5678",
        "resume", "programmatic", "Number swap, same length"),
    RoutingTestCase(
        "Update the email to john.smith@newcompany.com",
        "resume", "programmatic", "Email replacement, similar length"),
    RoutingTestCase(
        "Change 'Python' to 'Golang' in the skills section",
        "resume", "programmatic", "Single word swap in a specific section"),
    RoutingTestCase(
        "Replace all instances of 2024 with 2025",
        "report", "programmatic", "Multi-occurrence same-length swap"),
    RoutingTestCase(
        "Change 'January' to 'February'",
        "invoice", "programmatic", "Month name swap, similar length"),
    RoutingTestCase(
        "Change the title from 'Q3 Report' to 'Q4 Report'",
        "report", "programmatic", "Title text swap, similar length"),

    # === CLEAR VISUAL CASES ===
    RoutingTestCase(
        "Add a company logo in the top right corner",
        "resume", "visual", "Adding a new visual element"),
    RoutingTestCase(
        "Change the pie chart to a bar chart",
        "presentation", "visual", "Chart type change"),
    RoutingTestCase(
        "Make the background gradient blue to purple",
        "presentation", "visual", "Background visual change"),
    RoutingTestCase(
        "Add a decorative border around the page",
        "report", "visual", "Adding decorative elements"),
    RoutingTestCase(
        "Replace the header image with a mountain landscape",
        "report", "visual", "Image replacement"),

    # === HYBRID CASES ===
    RoutingTestCase(
        "Change the title to 'Q4 Report' and add a blue accent line under it",
        "report", "hybrid", "Text change + visual decoration"),
    RoutingTestCase(
        "Update the year to 2025 and change the chart colors",
        "presentation", "hybrid", "Text swap + visual change"),

    # === EDGE CASES ===
    RoutingTestCase(
        "Make the title bold",
        "resume", "programmatic", "Style change, not visual"),
    RoutingTestCase(
        "Change the font color of the header to blue",
        "resume", "programmatic", "Color change is style_change, not visual"),
    RoutingTestCase(
        "Increase the font size of the name to 16pt",
        "resume", "programmatic", "Size change is style_change, not visual"),
    RoutingTestCase(
        "Add 'Intern' after 'Software Engineer'",
        "resume", "programmatic", "Slightly longer replacement with reflow"),
    RoutingTestCase(
        "Change 'Software Engineer' to 'Software Engineer Intern'",
        "resume", "programmatic", "Moderately longer replacement"),
    RoutingTestCase(
        "Change 'Revenue' to 'Net Revenue from Continuing Operations'",
        "report", "hybrid", "Overflow: low-conf programmatic + visual fallback"),
    RoutingTestCase(
        "Move the skills section above work experience",
        "resume", "visual", "Layout restructuring requires visual"),
    RoutingTestCase(
        "Change the date format from MM/DD/YYYY to DD-MM-YYYY",
        "invoice", "programmatic", "Same-length format change"),
    RoutingTestCase(
        "Delete the second bullet point",
        "resume", "visual", "Deletion with reflow requires visual"),
    RoutingTestCase(
        "Translate the title to Spanish",
        "report", "visual", "Translation changes length unpredictably"),
    RoutingTestCase(
        "Capitalize the section headers",
        "resume", "programmatic", "Text transform, same length"),
    RoutingTestCase(
        "Swap the order of the first two bullet points",
        "resume", "programmatic",
        "On text-heavy pages, planner cross-swaps bullet text via text_replace "
        "to avoid visual corruption of dense text"),
]


# ---------------------------------------------------------------------------
# Document contexts
# ---------------------------------------------------------------------------

DOCUMENT_CONTEXTS = {
    "resume": {
        "text_density": 0.72,
        "layout_complexity": "complex",
        "column_count": 1,
        "has_cid_fonts": False,
        "full_text": """\
John Smith
Fremont, CA \u2022 (831) 555-1234 \u2022 john.smith@gmail.com
Education
UC Santa Cruz - Computer Science, Expected Graduation: December 2024, Current GPA: 4.0
Work Experience
SOFTWARE ENGINEER, Acme Corp, Part-time (Q3 2024 - Present)
- Building AI-powered document managment system
- Designed microservice architecture for PDF processing pipeline
SOFTWARE ENGINEERING INTERN, Juniper Networks (May 2023 - Aug 2023)
- Built LangGraph-based AI agent system
Skills
Languages & Frameworks: Python, TypeScript, SQL, FastAPI, React, Node.js, LangGraph
Infrastructure & Data: Kubernetes, Docker, Linux, PostgreSQL, AWS, GCP""",
        "font_summary": "  Calibri: body text (sans-serif, standard)\n  Calibri-Bold: headers (sans-serif, standard, bold)",
        "visual_description": "Single-column resume layout with section headers in bold. No charts, images, or decorative elements. Dense text throughout.",
    },
    "presentation": {
        "text_density": 0.22,
        "layout_complexity": "moderate",
        "column_count": 1,
        "has_cid_fonts": False,
        "full_text": """\
Q3 2024 Revenue Report
Prepared by Finance Team
Revenue: $4.2M (+15% YoY)
Key Highlights""",
        "font_summary": "  Arial: body (sans-serif, standard)\n  Arial-Bold: title (sans-serif, standard, bold)",
        "visual_description": "Presentation slide with large title at top, subtitle below, a pie chart in the center-right showing revenue breakdown, and three bullet points in the bottom-left. Blue header bar across the top.",
    },
    "report": {
        "text_density": 0.48,
        "layout_complexity": "moderate",
        "column_count": 1,
        "has_cid_fonts": False,
        "full_text": """\
Q3 Financial Report
January - March 2024
Executive Summary
Total revenue for Q3 2024 reached $4.2 million, representing a 15% increase year-over-year.
Operating expenses remained stable at $2.8 million.""",
        "font_summary": "  Times New Roman: body (serif, standard)\n  Arial: headers (sans-serif, standard)",
        "visual_description": "Report page with header, two paragraphs of body text, a small bar chart in the lower half showing quarterly revenue comparison. Company logo in top-left corner.",
    },
    "invoice": {
        "text_density": 0.35,
        "layout_complexity": "moderate",
        "column_count": 1,
        "has_cid_fonts": False,
        "full_text": """\
INVOICE #1234
Date: 01/15/2025
Bill To: John Smith, Acme Corp
Item: Consulting Services \u2014 January 2025
Amount: $5,000.00
Total Due: $5,000.00""",
        "font_summary": "  Helvetica: all text (sans-serif, standard)",
        "visual_description": "Invoice with company name at top, billing details in a grid layout, line items in a simple table, total at bottom. Minimal decoration.",
    },
}


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_planner_prompt(tc: RoutingTestCase) -> str:
    """Format the user prompt exactly as the orchestrator does."""
    ctx = DOCUMENT_CONTEXTS[tc.doc_type]
    return ORCHESTRATOR_USER_TEMPLATE.format(
        user_instruction=tc.instruction,
        page_text=ctx["full_text"],
        text_blocks="[positional data omitted for eval]",
        page_width=612,
        page_height=792,
        visual_description=ctx["visual_description"],
        text_density=ctx["text_density"],
        layout_complexity=ctx["layout_complexity"],
        has_cid_fonts=ctx["has_cid_fonts"],
        font_summary_formatted=ctx["font_summary"],
        conversation_context="No previous edits on this page.",
        column_count=ctx["column_count"],
    )


def classify_plan(plan: ExecutionPlan) -> str:
    """Classify a plan as programmatic, visual, or hybrid."""
    op_types = [op.type for op in plan.operations]
    if not op_types:
        return "empty"
    elif all(t in ("text_replace", "style_change") for t in op_types):
        return "programmatic"
    elif all(t == "visual_regenerate" for t in op_types):
        return "visual"
    else:
        return "hybrid"


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

async def run_routing_eval(
    cases: list[RoutingTestCase] | None = None,
) -> dict:
    """Run all test cases through the planner and report results."""
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.model_timeout_seconds,
    )

    cases = cases or ROUTING_TEST_CASES
    results = {"pass": 0, "fail": 0, "errors": [], "details": []}
    t_start = time.monotonic()

    for i, tc in enumerate(cases):
        prompt = format_planner_prompt(tc)

        try:
            raw = await provider.plan_edit(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_message=prompt,
            )
            plan = _parse_plan_json(raw)
            actual_path = classify_plan(plan)

            passed = actual_path == tc.expected_path
            status = "\u2705" if passed else "\u274c"

            if passed:
                results["pass"] += 1
            else:
                results["fail"] += 1
                results["errors"].append({
                    "instruction": tc.instruction,
                    "doc_type": tc.doc_type,
                    "expected": tc.expected_path,
                    "actual": actual_path,
                    "description": tc.description,
                    "plan_summary": plan.summary,
                    "operations": [
                        (op.type, getattr(op, "confidence", None))
                        for op in plan.operations
                    ],
                })

            results["details"].append({
                "i": i,
                "passed": passed,
                "instruction": tc.instruction,
                "doc_type": tc.doc_type,
                "expected": tc.expected_path,
                "actual": actual_path,
            })

            print(f"  {status} [{tc.doc_type:14s}] {tc.instruction!r}")
            if not passed:
                print(f"     Expected: {tc.expected_path}, Got: {actual_path}")
                print(f"     Plan: {plan.summary}")
                ops = [
                    (op.type, getattr(op, "confidence", None))
                    for op in plan.operations
                ]
                print(f"     Ops: {ops}")
            print()

        except Exception as e:
            results["fail"] += 1
            results["errors"].append({
                "instruction": tc.instruction,
                "error": str(e),
            })
            results["details"].append({
                "i": i,
                "passed": False,
                "instruction": tc.instruction,
                "doc_type": tc.doc_type,
                "expected": tc.expected_path,
                "actual": f"ERROR: {e}",
            })
            print(f"  \U0001f4a5 [{tc.doc_type:14s}] {tc.instruction!r} \u2014 ERROR: {e}")
            print()

    elapsed = time.monotonic() - t_start
    total = results["pass"] + results["fail"]
    pct = results["pass"] / total * 100 if total else 0

    print(f"\n{'=' * 70}")
    print(f"ROUTING EVAL: {results['pass']}/{total} passed ({pct:.0f}%)  "
          f"[{elapsed:.1f}s]")
    print(f"{'=' * 70}")

    if results["errors"]:
        print(f"\nFAILURES ({len(results['errors'])}):")
        for err in results["errors"]:
            print(f"  - \"{err['instruction']}\" on {err.get('doc_type', '?')}")
            exp = err.get("expected", "?")
            got = err.get("actual", err.get("error", "?"))
            print(f"    Expected {exp}, got {got}")

    # Save results to file
    log_path = Path(__file__).parent / "eval_results"
    log_path.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    result_file = log_path / f"routing_eval_{ts}.json"
    with open(result_file, "w") as f:
        json.dump({
            "timestamp": ts,
            "total": total,
            "passed": results["pass"],
            "failed": results["fail"],
            "pct": round(pct, 1),
            "elapsed_s": round(elapsed, 1),
            "details": results["details"],
            "errors": results["errors"],
        }, f, indent=2)
    print(f"\nResults saved to: {result_file}")

    return results


async def main():
    quick = "--quick" in sys.argv
    cases = ROUTING_TEST_CASES[:10] if quick else ROUTING_TEST_CASES
    print(f"Running routing eval: {len(cases)} test cases "
          f"{'(quick mode)' if quick else ''}\n")
    await run_routing_eval(cases)


if __name__ == "__main__":
    asyncio.run(main())
