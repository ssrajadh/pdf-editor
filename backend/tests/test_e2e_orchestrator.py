"""End-to-end orchestrator tests.

Tests the full pipeline: upload → plan → execute → verify.

Usage:
    # Fixture-only tests (no API key needed for programmatic-only):
    python -m tests.test_e2e_orchestrator

    # Include visual/hybrid tests (needs GEMINI_API_KEY):
    GEMINI_API_KEY=... python -m tests.test_e2e_orchestrator --all

    # Test with a real PDF:
    TEST_PDF_PATH=/path/to/resume.pdf python -m tests.test_e2e_orchestrator --real
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.services import pdf_service
from app.services.pdf_editor import PdfEditor
from app.services.model_provider import ProviderFactory
from app.services.orchestrator import Orchestrator
from app.storage.session import SessionManager
from app.config import settings

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "test_document.pdf"
REAL_PDF = Path(os.environ.get("TEST_PDF_PATH", ""))
STORAGE = Path(__file__).parent / "test_data_e2e"

results: list[tuple[str, bool, str]] = []
benchmarks: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_session(session_mgr: SessionManager, pdf_path: Path) -> str:
    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = session_mgr.create_session(pdf_bytes, pdf_path.name, page_count)
    session_path = session_mgr.get_session_path(session_id)
    pdf_service.render_all_pages(pdf_path, session_path / "pages")
    return session_id


progress_log: list[dict] = []


async def _capture_progress(stage: str, message: str, extra: dict | None = None):
    progress_log.append({"stage": stage, "message": message, "extra": extra})


def _extract_text_from_working(session_mgr: SessionManager, session_id: str, page: int) -> str:
    session_path = session_mgr.get_session_path(session_id)
    working = session_path / "working.pdf"
    pdf_path = working if working.exists() else session_path / "original.pdf"
    data = pdf_service.extract_text(pdf_path, page)
    return data["full_text"]


def _extract_text_from_original(session_mgr: SessionManager, session_id: str, page: int) -> str:
    session_path = session_mgr.get_session_path(session_id)
    data = pdf_service.extract_text(session_path / "original.pdf", page)
    return data["full_text"]


def _check_fonts(pdf_path: Path, page_num: int = 1) -> dict:
    """Check what font types are in a PDF."""
    import fitz
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    fonts_raw = page.get_fonts()
    info = {}
    for font in fonts_raw:
        xref, ext, ftype, name, ref_name, encoding = font[:6] if len(font) >= 6 else (font + (None,) * (6 - len(font)))
        info[ref_name or f"F{xref}"] = {
            "subtype": ftype or "",
            "encoding": encoding or "",
            "base_font": name or "",
        }
    has_cid = any("Type0" in str(f.get("subtype", "")) or "CID" in str(f.get("subtype", ""))
                   for f in info.values())
    doc.close()
    return info


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"    [{status}] {detail}" if detail else f"    [{status}]")


# ---------------------------------------------------------------------------
# Test: PDF structure analysis
# ---------------------------------------------------------------------------


def test_pdf_structure(pdf_path: Path, label: str):
    print(f"\n{'='*70}")
    print(f"STRUCTURE ANALYSIS: {label}")
    print(f"{'='*70}")

    fonts = _check_fonts(pdf_path)
    has_cid = any(f["subtype"] == "Type0" for f in fonts.values())
    has_type1 = any(f["subtype"] == "Type1" for f in fonts.values())

    print(f"  Fonts ({len(fonts)}):")
    for name, info in fonts.items():
        print(f"    {name}: {info['subtype']} / {info['encoding']} / {info['base_font']}")

    if has_cid:
        print("\n  ℹ️  CID (Type0) fonts detected — PyMuPDF redact-and-overlay handles these")
    if has_type1:
        print("\n  ✅ Type1 fonts detected — programmatic text editing should work")

    data = pdf_service.extract_text(pdf_path, 1)
    print(f"\n  Text extracted: {len(data['full_text'])} chars, {len(data['blocks'])} blocks")
    print(f"  First 200 chars: {data['full_text'][:200]!r}")

    record(
        f"{label}: structure analysis",
        len(data["full_text"]) > 0,
        f"Text extraction works ({len(data['full_text'])} chars, {len(fonts)} fonts, CID={has_cid})",
    )

    return {"has_cid": has_cid, "has_type1": has_type1, "text": data["full_text"]}


# ---------------------------------------------------------------------------
# Test: Pure text replacement (fixture only — needs Type1 fonts)
# ---------------------------------------------------------------------------


async def test_pure_text_replace(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Pure text replacement (Q3 → Q4)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)
    progress_log.clear()

    t0 = time.monotonic()
    result = await orchestrator.execute_edit(
        session_id, 1, "Change Q3 to Q4", _capture_progress,
    )
    elapsed = time.monotonic() - t0
    elapsed_ms = int(elapsed * 1000)
    benchmarks["pure_text_replace_ms"] = elapsed_ms

    print(f"  Time: {elapsed_ms}ms")
    print(f"  Plan summary: {result.plan_summary}")
    print(f"  Operations: {len(result.operations)}")
    for op in result.operations:
        print(f"    - {op.op_type.value}: path={op.path}, success={op.success}, {op.time_ms}ms")
        print(f"      detail: {op.detail}")
    print(f"  Text layer: {result.text_layer_source}")
    print(f"  Programmatic: {result.programmatic_count}, Visual: {result.visual_count}")

    # Check plan had text_replace
    has_text_replace = any(op.op_type.value == "text_replace" for op in result.operations)
    record("Pure text: plan has text_replace op", has_text_replace,
           f"Operations: {[op.op_type.value for op in result.operations]}")

    # Check all programmatic
    all_prog = result.visual_count == 0 and result.programmatic_count > 0
    record("Pure text: all programmatic", all_prog,
           f"prog={result.programmatic_count}, vis={result.visual_count}")

    # Check speed (planning LLM adds ~9s, actual edits are ~200ms each)
    fast = elapsed_ms < 30000
    record("Pure text: fast (<30s incl planning)", fast, f"{elapsed_ms}ms")

    # Verify text changed in working PDF
    working_text = _extract_text_from_working(session_mgr, session_id, 1)
    has_q4 = "Q4" in working_text
    no_q3 = "Q3" not in working_text
    record("Pure text: working PDF has Q4", has_q4,
           f"'Q4' {'found' if has_q4 else 'NOT found'} in working PDF")
    record("Pure text: working PDF no Q3", no_q3,
           f"'Q3' {'absent' if no_q3 else 'STILL present'} in working PDF")

    # Verify original untouched
    orig_text = _extract_text_from_original(session_mgr, session_id, 1)
    orig_has_q3 = "Q3" in orig_text
    record("Pure text: original still has Q3", orig_has_q3,
           f"'Q3' {'found' if orig_has_q3 else 'NOT found'} in original")

    # Verify text layer preserved
    record("Pure text: text layer = programmatic_edit",
           result.text_layer_source == "programmatic_edit",
           f"text_layer_source={result.text_layer_source}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Multiple text replacements
# ---------------------------------------------------------------------------


async def test_multiple_text_replace(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Multiple text replacements (2025→2026 + John Smith→Jane Doe)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)
    progress_log.clear()

    t0 = time.monotonic()
    result = await orchestrator.execute_edit(
        session_id, 1,
        "Change all instances of 2025 to 2026 and change John Smith to Jane Doe",
        _capture_progress,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    benchmarks["multi_text_replace_ms"] = elapsed_ms

    print(f"  Time: {elapsed_ms}ms")
    print(f"  Plan summary: {result.plan_summary}")
    print(f"  Operations ({len(result.operations)}):")
    for op in result.operations:
        print(f"    - {op.op_type.value}: path={op.path}, success={op.success}, {op.time_ms}ms")
        print(f"      detail: {op.detail}")

    # Check multiple ops
    op_count = len(result.operations)
    record("Multi text: has multiple operations", op_count >= 2,
           f"{op_count} operations")

    # Check text changes
    working_text = _extract_text_from_working(session_mgr, session_id, 1)
    has_2026 = "2026" in working_text
    has_jane = "Jane Doe" in working_text
    record("Multi text: has 2026", has_2026,
           f"'2026' {'found' if has_2026 else 'NOT found'}")
    record("Multi text: has Jane Doe", has_jane,
           f"'Jane Doe' {'found' if has_jane else 'NOT found'}")

    # Check all programmatic
    all_prog = result.visual_count == 0
    record("Multi text: all programmatic", all_prog,
           f"prog={result.programmatic_count}, vis={result.visual_count}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Overflow escalation
# ---------------------------------------------------------------------------


async def test_overflow_escalation(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Overflow escalation (short → long text)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)
    progress_log.clear()

    # The planner should either route directly to visual or the programmatic
    # path should detect the overflow and escalate
    result = await orchestrator.execute_edit(
        session_id, 1,
        "Change 'Q3' to 'Third Quarter Financial Performance Review'",
        _capture_progress,
    )

    print(f"  Plan summary: {result.plan_summary}")
    for op in result.operations:
        print(f"    - {op.op_type.value}: path={op.path}, success={op.success}")
        print(f"      detail: {op.detail}")

    # Either the planner routes to visual, or text_replace escalates
    has_visual_or_fallback = any(
        op.path in ("visual", "fallback_visual") for op in result.operations
    )
    has_low_conf_or_visual_op = any(
        op.op_type.value == "visual_regenerate" or op.path == "fallback_visual"
        for op in result.operations
    )

    record("Overflow: handled via visual path", has_visual_or_fallback or has_low_conf_or_visual_op,
           f"Paths used: {[op.path for op in result.operations]}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Plan preview (no execution)
# ---------------------------------------------------------------------------


async def test_plan_preview(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Plan preview (no execution)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)

    t0 = time.monotonic()
    plan = await orchestrator.plan_only(session_id, 1, "Change Q3 to Q4")
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    benchmarks["plan_preview_ms"] = elapsed_ms

    print(f"  Time: {elapsed_ms}ms")
    print(f"  Summary: {plan.summary}")
    print(f"  All programmatic: {plan.all_programmatic}")
    print(f"  Operations ({len(plan.operations)}):")
    for i, op in enumerate(plan.operations):
        print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
        print(f"        reasoning: {op.reasoning}")

    record("Plan preview: returns plan", len(plan.operations) > 0,
           f"{len(plan.operations)} operations")
    record("Plan preview: has text_replace",
           any(op.type == "text_replace" for op in plan.operations),
           f"Types: {[op.type for op in plan.operations]}")

    # Verify no side effects — working PDF should not exist
    session_path = session_mgr.get_session_path(session_id)
    no_working = not (session_path / "working.pdf").exists()
    record("Plan preview: no working.pdf created", no_working,
           "Plan preview is read-only")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Sequential edits — compound degradation prevention
# ---------------------------------------------------------------------------


async def test_sequential_edits(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Sequential edits (compound degradation prevention)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)
    session_path = session_mgr.get_session_path(session_id)

    # Step 1: Programmatic edit
    print("\n  Step 1: Change Q3 → Q4 (programmatic)")
    progress_log.clear()
    r1 = await orchestrator.execute_edit(
        session_id, 1, "Change Q3 to Q4", _capture_progress,
    )
    print(f"    Result: prog={r1.programmatic_count}, vis={r1.visual_count}, v={r1.version}")
    working_exists_1 = (session_path / "working.pdf").exists()
    record("Sequential: step 1 creates working.pdf", working_exists_1)

    text_after_1 = _extract_text_from_working(session_mgr, session_id, 1)
    record("Sequential: step 1 has Q4", "Q4" in text_after_1)

    # Step 2: Another programmatic edit
    print("\n  Step 2: Change John Smith → Jane Doe (programmatic)")
    progress_log.clear()
    r2 = await orchestrator.execute_edit(
        session_id, 1, "Change John Smith to Jane Doe", _capture_progress,
    )
    print(f"    Result: prog={r2.programmatic_count}, vis={r2.visual_count}, v={r2.version}")

    text_after_2 = _extract_text_from_working(session_mgr, session_id, 1)
    record("Sequential: step 2 has Q4 + Jane Doe",
           "Q4" in text_after_2 and "Jane Doe" in text_after_2,
           f"Q4={'Q4' in text_after_2}, Jane Doe={'Jane Doe' in text_after_2}")

    # Verify original untouched through all edits
    orig_text = _extract_text_from_original(session_mgr, session_id, 1)
    record("Sequential: original untouched",
           "Q3" in orig_text and "John Smith" in orig_text,
           "Original still has Q3 and John Smith")

    # Check edit history
    history_path = session_path / "edits" / "page_1_history.json"
    if history_path.exists():
        history = json.loads(history_path.read_text())
        print(f"\n  Edit history ({len(history)} entries):")
        for entry in history:
            print(f"    v{entry['version']}: {entry['prompt']}")
            print(f"      plan: {entry.get('plan_summary', 'N/A')}")
            print(f"      working_pdf_modified: {entry.get('working_pdf_modified', 'N/A')}")
            print(f"      text_layer_source: {entry.get('text_layer_source', 'N/A')}")
        record("Sequential: rich history saved", len(history) >= 2,
               f"{len(history)} entries with plan_summary and base_source")
    else:
        record("Sequential: edit history exists", False, "history file not found")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Real PDF analysis
# ---------------------------------------------------------------------------


async def test_real_pdf_analysis(session_mgr: SessionManager, orchestrator: Orchestrator, pdf_path: Path):
    print(f"\n{'='*70}")
    print(f"REAL PDF TESTS: {pdf_path.name}")
    print(f"{'='*70}")

    info = test_pdf_structure(pdf_path, f"Real PDF ({pdf_path.name})")

    session_id = _create_session(session_mgr, pdf_path)
    session_path = session_mgr.get_session_path(session_id)

    # Plan preview to see what the planner does
    print(f"\n  --- Plan preview: 'Change the name to Test Name' ---")
    t0 = time.monotonic()
    plan = await orchestrator.plan_only(session_id, 1, "Change the name to Test Name")
    plan_ms = int((time.monotonic() - t0) * 1000)
    benchmarks["real_pdf_plan_ms"] = plan_ms

    print(f"  Planning time: {plan_ms}ms")
    print(f"  Summary: {plan.summary}")
    print(f"  All programmatic: {plan.all_programmatic}")
    for i, op in enumerate(plan.operations):
        print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
        print(f"        reasoning: {op.reasoning}")
        if hasattr(op, "original_text"):
            print(f"        from: {getattr(op, 'original_text', '')!r}")
            print(f"        to: {getattr(op, 'replacement_text', '')!r}")
        if hasattr(op, "prompt") and op.type == "visual_regenerate":
            print(f"        prompt: {op.prompt!r}")

    # With PyMuPDF redact-and-overlay, even CID font PDFs can use text_replace
    has_text_replace = any(op.type == "text_replace" for op in plan.operations)
    record("Real PDF: planner routes to text_replace",
           has_text_replace,
           f"types={[op.type for op in plan.operations]}")

    # Try executing the edit to see the full pipeline
    print(f"\n  --- Executing edit on real PDF ---")
    progress_log.clear()
    t0 = time.monotonic()
    result = await orchestrator.execute_edit(
        session_id, 1, "Change the name to Test Name", _capture_progress,
    )
    exec_ms = int((time.monotonic() - t0) * 1000)
    benchmarks["real_pdf_edit_ms"] = exec_ms

    print(f"  Execution time: {exec_ms}ms")
    print(f"  Plan summary: {result.plan_summary}")
    print(f"  Operations ({len(result.operations)}):")
    for op in result.operations:
        print(f"    - {op.op_type.value}: path={op.path}, success={op.success}, {op.time_ms}ms")
        print(f"      detail: {op.detail}")
        if op.error:
            print(f"      error: {op.error}")
    print(f"  Text layer: {result.text_layer_source}")

    record("Real PDF: edit completed successfully",
           any(op.success for op in result.operations),
           f"prog={result.programmatic_count}, vis={result.visual_count}, time={exec_ms}ms")

    # Check progress events
    stages = [p["stage"] for p in progress_log]
    record("Real PDF: progress events received",
           "planning" in stages and "planned" in stages,
           f"Stages: {stages}")

    # Check if plan data was sent in progress
    planned_events = [p for p in progress_log if p["stage"] == "planned"]
    has_plan_data = planned_events and planned_events[0].get("extra", {}).get("plan")
    record("Real PDF: plan data in progress event", bool(has_plan_data))

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    import shutil
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True)

    session_mgr = SessionManager(STORAGE)
    provider = ProviderFactory.get_provider(
        settings.model_provider, settings.gemini_api_key,
    )
    orchestrator = Orchestrator(
        model_provider=provider, session_manager=session_mgr,
    )

    has_api_key = bool(settings.gemini_api_key)
    run_all = "--all" in sys.argv
    run_real = "--real" in sys.argv or REAL_PDF.exists()

    # Always run structure analysis
    print("\n" + "=" * 70)
    print("FIXTURE PDF ANALYSIS")
    print("=" * 70)
    test_pdf_structure(FIXTURE_PDF, "Test fixture (reportlab)")

    if REAL_PDF.exists():
        test_pdf_structure(REAL_PDF, f"Real PDF ({REAL_PDF.name})")
    elif run_real:
        print(f"\n⚠️  TEST_PDF_PATH not set or file not found: {REAL_PDF}")

    # Programmatic-only tests (need API key for planning LLM)
    if has_api_key:
        await test_plan_preview(session_mgr, orchestrator)
        await test_pure_text_replace(session_mgr, orchestrator)
        await test_multiple_text_replace(session_mgr, orchestrator)
        await test_sequential_edits(session_mgr, orchestrator)

        if run_all:
            await test_overflow_escalation(session_mgr, orchestrator)

        if run_real and REAL_PDF.exists():
            await test_real_pdf_analysis(session_mgr, orchestrator, REAL_PDF)
    else:
        print("\n⚠️  GEMINI_API_KEY not set — skipping orchestrator tests")
        print("   Set it in .env or environment to run full tests")

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for name, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    if benchmarks:
        print(f"\n  --- Performance Benchmarks ---")
        for k, v in benchmarks.items():
            print(f"    {k}: {v}ms")

    print(f"\n  {passed}/{passed + failed} tests passed")

    if STORAGE.exists():
        shutil.rmtree(STORAGE)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
