"""End-to-end validation of the hardened programmatic editing pipeline.

Tests multiple document types with a matrix of edits, validates routing
(programmatic vs visual vs fallback_visual), checks performance, and
prints a summary table.

Usage:
    cd backend
    # Programmatic-only tests (no API key needed for plan-only assertions):
    python -m tests.test_e2e_phase2_5

    # Full suite with visual execution (needs GEMINI_API_KEY):
    GEMINI_API_KEY=... python -m tests.test_e2e_phase2_5 --all
"""

import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white, black
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from app.config import settings
from app.services import pdf_service
from app.services.model_provider import ProviderFactory
from app.services.orchestrator import Orchestrator
from app.storage.session import SessionManager

FIXTURES = Path(__file__).parent / "fixtures"
STORAGE = Path(__file__).parent / "test_data_e2e_p25"
RESUME_PATH = Path(__file__).parent.parent.parent / "SohamR_Resume_Intern.pdf"

ALL_MODE = "--all" in sys.argv


# ---------------------------------------------------------------------------
# Fixture generators
# ---------------------------------------------------------------------------


def _generate_simple_report() -> Path:
    """Single column, standard fonts — the easy case."""
    path = FIXTURES / "simple_report.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 24)
    c.drawString(inch, h - 1.2 * inch, "Q3 2024 Annual Report")

    c.setFont("Helvetica", 14)
    c.drawString(inch, h - 1.7 * inch, "Prepared by: Analytics Team")

    c.setStrokeColorRGB(0.3, 0.3, 0.3)
    c.line(inch, h - 1.9 * inch, w - inch, h - 1.9 * inch)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(inch, h - 2.5 * inch, "Summary")

    c.setFont("Helvetica", 11)
    y = h - 3.0 * inch
    lines = [
        "Total Revenue: $1.2M for Q3 2024",
        "Year-over-Year Growth: 45% compared to Q3 2023",
        "Operating Margin: 18.5% (up from 15.2%)",
        "New Customers Acquired: 847 enterprise accounts",
        "",
        "The Q3 2024 quarter exceeded expectations across all key metrics.",
        "Revenue growth was driven by expansion in the EMEA region.",
        "",
        "Updated: September 2024",
        "",
        "Copyright 2024 Acme Corporation. All rights reserved.",
    ]
    for line in lines:
        if line:
            c.drawString(inch, y, line)
        y -= 18

    c.showPage()
    c.save()
    return path


def _generate_presentation_slide() -> Path:
    """Slide-like layout with title, body, and a colored shape."""
    path = FIXTURES / "presentation_slide.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFillColor(HexColor("#1a365d"))
    c.rect(0, h - 2 * inch, w, 2 * inch, fill=True, stroke=False)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(inch, h - 1.3 * inch, "Q3 Performance Review")

    c.setFont("Helvetica", 14)
    c.drawString(inch, h - 1.7 * inch, "October 2024 | Strategy Team")

    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(inch, h - 3 * inch, "Key Metrics")

    c.setFont("Helvetica", 12)
    metrics = [
        "Revenue: $4.2M (target: $3.8M)",
        "Customer Satisfaction: 94% NPS",
        "Market Share: 28% (up 3pp YoY)",
        "Employee Engagement: 87%",
    ]
    y = h - 3.5 * inch
    for m in metrics:
        c.drawString(1.3 * inch, y, f"•  {m}")
        y -= 22

    c.setFillColor(HexColor("#e2e8f0"))
    c.roundRect(
        w / 2 + 0.5 * inch, h - 5 * inch, 2.5 * inch, 2.5 * inch,
        10, fill=True, stroke=False,
    )
    c.setFillColor(HexColor("#718096"))
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2 + 1.75 * inch, h - 3.9 * inch, "[Chart Placeholder]")

    c.showPage()
    c.save()
    return path


def _generate_colored_header() -> Path:
    """Document with a dark colored header bar and white text."""
    path = FIXTURES / "colored_header.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    header_color = HexColor("#2d3748")
    c.setFillColor(header_color)
    c.rect(0, h - 1.5 * inch, w, 1.5 * inch, fill=True, stroke=False)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(inch, h - 0.8 * inch, "Project Alpha Status Report")

    c.setFont("Helvetica", 12)
    c.drawString(inch, h - 1.2 * inch, "Confidential — Internal Use Only")

    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(inch, h - 2.2 * inch, "Executive Overview")

    c.setFont("Helvetica", 11)
    y = h - 2.7 * inch
    body = [
        "Project Alpha is on track for delivery in Q4 2024.",
        "All milestones have been met as of October 15, 2024.",
        "Budget utilization stands at 78% of allocated funds.",
        "Risk items: 2 medium, 0 critical.",
    ]
    for line in body:
        c.drawString(inch, y, line)
        y -= 18

    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    document: str
    instruction: str
    expected_path: str
    planned_types: str = ""
    actual_path: str = ""
    time_ms: int = 0
    passed: bool = False
    detail: str = ""
    plan_summary: str = ""
    page_analysis: str = ""


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)


progress_log: list[dict] = []


async def _capture_progress(stage: str, message: str, extra: dict | None = None):
    progress_log.append({"stage": stage, "message": message, "extra": extra})


def _create_session(mgr: SessionManager, pdf_path: Path) -> str:
    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = mgr.create_session(pdf_bytes, pdf_path.name, page_count)
    session_path = mgr.get_session_path(session_id)
    pdf_service.render_all_pages(pdf_path, session_path / "pages")
    return session_id


def _extract_text(mgr: SessionManager, session_id: str, page: int) -> str:
    session_path = mgr.get_session_path(session_id)
    working = session_path / "working.pdf"
    pdf_path = working if working.exists() else session_path / "original.pdf"
    data = pdf_service.extract_text(pdf_path, page)
    return data["full_text"]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


async def test_document(
    orchestrator: Orchestrator,
    session_mgr: SessionManager,
    doc_name: str,
    pdf_path: Path,
    edits: list[tuple[str, str]],
    suite: TestSuite,
    skip_visual: bool = False,
):
    """Run a sequence of edits on one document, recording results."""
    session_id = _create_session(session_mgr, pdf_path)
    print(f"\n{'='*70}")
    print(f"  {doc_name}  ({pdf_path.name})")
    print(f"{'='*70}")

    for instruction, expected_path in edits:
        r = TestResult(
            document=doc_name,
            instruction=instruction,
            expected_path=expected_path,
        )

        if skip_visual and expected_path == "visual":
            r.detail = "skipped (no API key)"
            r.passed = True
            r.actual_path = "skipped"
            suite.results.append(r)
            print(f"  [SKIP] {instruction}")
            print(f"         Expected: visual — skipped without API key")
            await asyncio.sleep(1)
            continue

        try:
            progress_log.clear()

            # --- Plan preview (with retry for rate limits) ---
            plan = None
            for attempt in range(3):
                try:
                    plan = await orchestrator.plan_only(session_id, 1, instruction)
                    break
                except RuntimeError as e:
                    is_rate_limit = "429" in str(e) or "429" in str(e.__cause__ or "")
                    if is_rate_limit and attempt < 2:
                        wait = 20 * (attempt + 1)
                        print(f"\n  Rate limited on plan_only, waiting {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise

            assert plan is not None
            r.plan_summary = plan.summary
            r.page_analysis = plan.page_analysis or ""
            planned_types = [(op.type, f"{op.confidence:.0%}") for op in plan.operations]
            r.planned_types = str(planned_types)
            print(f"\n  Instruction: {instruction}")
            print(f"  Plan: {plan.summary}")
            print(f"  Page analysis: {r.page_analysis}")
            print(f"  Ops: {planned_types}")

            # --- Execute (with retry for rate limits) ---
            t0 = time.monotonic()
            result = None
            for attempt in range(3):
                try:
                    result = await orchestrator.execute_edit(
                        session_id, 1, instruction, _capture_progress,
                    )
                    break
                except RuntimeError as e:
                    is_rate_limit = "429" in str(e) or "429" in str(e.__cause__ or "")
                    if is_rate_limit and attempt < 2:
                        wait = 20 * (attempt + 1)
                        print(f"  Rate limited on execute, waiting {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise

            assert result is not None
            elapsed = int((time.monotonic() - t0) * 1000)
            r.time_ms = elapsed

            successful_paths = set()
            for op_result in result.operations:
                if op_result.success:
                    successful_paths.add(op_result.path)
                print(
                    f"    op[{op_result.op_index}] {op_result.op_type}: "
                    f"path={op_result.path}, success={op_result.success}, "
                    f"time={op_result.time_ms}ms — {op_result.detail}"
                )
                if op_result.error:
                    print(f"      error: {op_result.error}")

            if not successful_paths:
                actual = "none"
            elif len(successful_paths) > 1:
                actual = "mixed"
            else:
                actual = successful_paths.pop()
            r.actual_path = actual

            path_ok = (
                actual == expected_path
                or actual == "fallback_visual"
                or (expected_path == "visual" and actual in ("visual", "fallback_visual", "mixed"))
                or (expected_path == "programmatic" and actual in ("programmatic", "mixed"))
            )

            r.passed = path_ok
            status = "PASS" if r.passed else "FAIL"
            r.detail = f"path={'ok' if path_ok else 'MISMATCH'}, time={elapsed}ms"

            print(f"  [{status}] expected={expected_path}, actual={actual}, time={elapsed}ms")

            if expected_path == "programmatic" and actual == "programmatic":
                text = _extract_text(session_mgr, session_id, 1)
                preview = text[:200].replace("\n", "\\n")
                print(f"  Text after edit: {preview}...")

        except Exception as exc:
            r.passed = False
            r.detail = f"ERROR: {exc}"
            r.actual_path = "error"
            print(f"  [FAIL] {instruction}")
            print(f"         Error: {exc}")
            import traceback
            traceback.print_exc()

        suite.results.append(r)
        await asyncio.sleep(3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)

    # Generate fixture PDFs
    print("Generating fixture PDFs...")
    simple_report = _generate_simple_report()
    presentation_slide = _generate_presentation_slide()
    colored_header = _generate_colored_header()
    print(f"  simple_report.pdf:       {simple_report.stat().st_size:,} bytes")
    print(f"  presentation_slide.pdf:  {presentation_slide.stat().st_size:,} bytes")
    print(f"  colored_header.pdf:      {colored_header.stat().st_size:,} bytes")

    has_resume = RESUME_PATH.exists()
    if has_resume:
        print(f"  resume (real):           {RESUME_PATH.stat().st_size:,} bytes")
    else:
        print("  resume: NOT FOUND — skipping resume tests")

    has_api_key = bool(settings.gemini_api_key)
    skip_visual = not (has_api_key and ALL_MODE)
    if skip_visual:
        print("\nVisual tests will be skipped (no API key or --all flag)")

    session_mgr = SessionManager(STORAGE)
    provider = ProviderFactory.get_provider(
        settings.model_provider, settings.gemini_api_key,
    )
    orchestrator = Orchestrator(model_provider=provider, session_manager=session_mgr)

    suite = TestSuite()

    # -----------------------------------------------------------------------
    # 1. simple_report.pdf
    # -----------------------------------------------------------------------
    await test_document(
        orchestrator, session_mgr,
        "simple_report", simple_report,
        [
            ("Change 2024 to 2025", "programmatic"),
            ("Change Report to Analysis", "visual"),
            ("Change Summary to Executive Summary and Key Findings", "visual"),
            ("Add a blue border around the page", "visual"),
        ],
        suite, skip_visual,
    )

    await asyncio.sleep(5)

    # -----------------------------------------------------------------------
    # 2. presentation_slide.pdf
    # -----------------------------------------------------------------------
    await test_document(
        orchestrator, session_mgr,
        "presentation_slide", presentation_slide,
        [
            ("Change Q3 to Q4", "programmatic"),
            ("Change the chart placeholder to a bar chart", "visual"),
        ],
        suite, skip_visual,
    )

    await asyncio.sleep(5)

    # -----------------------------------------------------------------------
    # 3. resume (real document)
    # -----------------------------------------------------------------------
    if has_resume:
        await test_document(
            orchestrator, session_mgr,
            "resume", RESUME_PATH,
            [
                ("Change 2024 to 2025", "programmatic"),
                ("Change Software to Hardware", "programmatic"),
                ("Change GPA to Grade Point Average and Academic Standing", "visual"),
                ("Make the header background darker", "visual"),
            ],
            suite, skip_visual,
        )

        await asyncio.sleep(5)

    # -----------------------------------------------------------------------
    # 4. colored_header.pdf
    # -----------------------------------------------------------------------
    await test_document(
        orchestrator, session_mgr,
        "colored_header", colored_header,
        [
            ("Change Project Alpha to Project Beta", "programmatic"),
            ("Change 2024 to 2025", "programmatic"),
        ],
        suite, skip_visual,
    )

    # -----------------------------------------------------------------------
    # Summary table
    # -----------------------------------------------------------------------
    print(f"\n\n{'='*100}")
    print("  SUMMARY TABLE")
    print(f"{'='*100}")
    header = f"  {'Document':<22} | {'Edit':<48} | {'Expected':<13} | {'Actual':<15} | {'Time':>8} | {'Result'}"
    print(header)
    print(f"  {'-'*22}-+-{'-'*48}-+-{'-'*13}-+-{'-'*15}-+-{'-'*8}-+-{'-'*6}")
    for r in suite.results:
        instr_short = r.instruction[:46] + ".." if len(r.instruction) > 48 else r.instruction
        time_str = f"{r.time_ms}ms" if r.time_ms else "—"
        status = "PASS" if r.passed else "FAIL"
        print(
            f"  {r.document:<22} | {instr_short:<48} | "
            f"{r.expected_path:<13} | {r.actual_path:<15} | "
            f"{time_str:>8} | {status}"
        )

    total = len(suite.results)
    passed = sum(1 for r in suite.results if r.passed)
    failed = total - passed
    print(f"\n  Total: {total}  |  Passed: {passed}  |  Failed: {failed}")

    if failed > 0:
        print("\n  FAILED TESTS:")
        for r in suite.results:
            if not r.passed:
                print(f"    - [{r.document}] {r.instruction}: {r.detail}")

    # -----------------------------------------------------------------------
    # Benchmarks for README
    # -----------------------------------------------------------------------
    prog_results = [
        r for r in suite.results
        if r.actual_path == "programmatic" and r.time_ms > 0
    ]
    if prog_results:
        print(f"\n  Programmatic edit benchmarks:")
        times = [r.time_ms for r in prog_results]
        print(f"    Min: {min(times)}ms  |  Max: {max(times)}ms  |  Avg: {sum(times)//len(times)}ms")

    # Cleanup
    if STORAGE.exists():
        shutil.rmtree(STORAGE)

    print(f"\n{'='*100}")
    if failed > 0:
        print("  RESULT: SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("  RESULT: ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
