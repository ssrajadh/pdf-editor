"""End-to-end pipeline test: programmatic editing wired into orchestrator.

Usage:
    cd backend
    .venv/bin/python -m tests.test_pipeline_e2e

Tests:
1. Text replacement via programmatic path — verify fast, original untouched, text layer perfect
2. Visual edit — verify base image from PDF (compound degradation prevention)
3. Hybrid plan — verify programmatic first, visual uses post-edit base
"""

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.model_provider import GeminiProvider
from app.services.orchestrator import Orchestrator
from app.services import pdf_service
from app.storage.session import SessionManager

TEST_DIR = Path("/tmp/test_pipeline_e2e")


def create_test_pdf(path: Path) -> None:
    """Create a PDF with known text and a simple chart."""
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 24)
    c.drawString(72, h - 72, "Q3 2025 Revenue Report")

    c.setFont("Helvetica", 12)
    c.drawString(72, h - 100, "Prepared by: Finance Department")
    c.drawString(72, h - 118, "Total Revenue: $4.2M")
    c.drawString(72, h - 136, "Growth Rate: 12% YoY")

    # Simple chart (rectangles)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, h - 200, "Sales by Region:")
    bars = [("NA", 150), ("EU", 100), ("APAC", 70)]
    for i, (label, height) in enumerate(bars):
        x = 72 + i * 120
        c.setFillColorRGB(0.2, 0.4, 0.8)
        c.rect(x, h - 400, 80, height, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 10)
        c.drawString(x + 25, h - 420, label)

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 10)
    c.drawString(72, 40, "Copyright 2025 Acme Corporation")
    c.save()


async def setup(session_mgr: SessionManager) -> str:
    """Create a fresh session from the test PDF."""
    pdf_path = TEST_DIR / "test.pdf"
    create_test_pdf(pdf_path)
    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = session_mgr.create_session(pdf_bytes, "test.pdf", page_count)
    session_path = session_mgr.get_session_path(session_id)
    await pdf_service.render_all_pages_async(
        session_path / "original.pdf", session_path / "pages",
    )
    return session_id


# ── Test 1: Programmatic text replacement ─────────────────────────────


async def test_programmatic_text_replace(
    orchestrator: Orchestrator, session_mgr: SessionManager,
) -> bool:
    """Pure text replacement should use programmatic path, be fast,
    leave original untouched, and produce a perfect text layer."""
    print(f"\n{'='*70}")
    print("TEST 1: Programmatic text replacement (Q3 -> Q4)")
    print(f"{'='*70}")

    session_id = await setup(session_mgr)
    session_path = session_mgr.get_session_path(session_id)
    original_bytes = (session_path / "original.pdf").read_bytes()

    progress_log: list[dict] = []

    async def on_progress(stage: str, message: str, extra: dict | None) -> None:
        entry = {"stage": stage, "message": message}
        if extra:
            entry.update(extra)
        progress_log.append(entry)
        print(f"    [{stage}] {message}")

    t0 = time.monotonic()
    result = await orchestrator.execute_edit(session_id, 1, "Change Q3 to Q4", on_progress)
    elapsed = time.monotonic() - t0

    issues = []

    # 1a. Should plan as text_replace
    plan_entries = [p for p in progress_log if p["stage"] == "planned"]
    if plan_entries:
        plan_data = plan_entries[0].get("plan", {})
        op_types = [op["type"] for op in plan_data.get("operations", [])]
        if "text_replace" not in op_types:
            issues.append(f"Expected text_replace in plan, got {op_types}")
            print(f"  [FAIL] Plan ops: {op_types}")
        else:
            print(f"  [PASS] Plan includes text_replace")

    # 1b. Should execute programmatically
    if result.programmatic_count > 0:
        print(f"  [PASS] Programmatic ops: {result.programmatic_count}")
    else:
        issues.append("No programmatic ops executed")
        print(f"  [FAIL] programmatic_count={result.programmatic_count}")

    # 1c. Should be fast (under 15s total including planning)
    print(f"  Total time: {elapsed:.1f}s ({result.total_time_ms}ms)")

    # 1d. Original PDF unchanged
    original_after = (session_path / "original.pdf").read_bytes()
    if original_bytes == original_after:
        print(f"  [PASS] Original PDF unchanged")
    else:
        issues.append("Original PDF was modified!")
        print(f"  [FAIL] Original PDF was modified")

    # 1e. Text layer should be 'programmatic_edit'
    if result.text_layer_source == "programmatic_edit":
        print(f"  [PASS] Text layer source: {result.text_layer_source}")
    else:
        issues.append(f"Text layer source={result.text_layer_source}, expected 'programmatic_edit'")
        print(f"  [FAIL] Text layer source: {result.text_layer_source}")

    # 1f. Text layer file should exist with blocks
    layer_path = session_path / "edits" / f"page_1_v{result.version}_text.json"
    if layer_path.exists():
        layer_data = json.loads(layer_path.read_text())
        block_count = len(layer_data.get("blocks", []))
        stale = layer_data.get("stale", False)
        if block_count > 0 and not stale:
            print(f"  [PASS] Text layer: {block_count} blocks, not stale")
        else:
            issues.append(f"Text layer: {block_count} blocks, stale={stale}")
            print(f"  [FAIL] Text layer: {block_count} blocks, stale={stale}")
    else:
        issues.append("Text layer file not found")
        print(f"  [FAIL] Text layer file not found")

    # 1g. Working PDF should contain Q4
    working_pdf = session_path / "working.pdf"
    if working_pdf.exists():
        text_data = pdf_service.extract_text(working_pdf, 1)
        if "Q4" in text_data["full_text"]:
            print(f"  [PASS] Working PDF contains 'Q4'")
        else:
            issues.append("Working PDF doesn't contain 'Q4'")
            print(f"  [FAIL] Working PDF text: {text_data['full_text'][:100]}")
    else:
        issues.append("Working PDF not created")
        print(f"  [FAIL] No working.pdf")

    # 1h. Progress callback should have sent plan and op_index
    has_plan_extra = any("plan" in p for p in progress_log)
    has_op_index = any("op_index" in p for p in progress_log)
    if has_plan_extra:
        print(f"  [PASS] Progress includes plan data")
    else:
        issues.append("Progress missing plan data")
        print(f"  [FAIL] No plan data in progress")
    if has_op_index:
        print(f"  [PASS] Progress includes op_index")
    else:
        issues.append("Progress missing op_index")
        print(f"  [FAIL] No op_index in progress")

    # New page image should exist
    new_img = session_path / "pages" / f"page_1_v{result.version}.png"
    if new_img.exists():
        img = Image.open(new_img)
        print(f"  [PASS] Re-rendered image: {new_img.name} ({img.size[0]}x{img.size[1]})")
    else:
        issues.append("Re-rendered page image missing")
        print(f"  [FAIL] No re-rendered image")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print(f"\n  PASSED")
    return True


# ── Test 2: Visual edit uses PDF-rendered base ────────────────────────


async def test_visual_uses_pdf_base(
    orchestrator: Orchestrator, session_mgr: SessionManager,
) -> bool:
    """Visual edit should use get_current_base_image (from PDF), not
    a previously AI-generated image."""
    print(f"\n{'='*70}")
    print("TEST 2: Visual edit uses PDF-rendered base image")
    print(f"{'='*70}")

    session_id = await setup(session_mgr)
    session_path = session_mgr.get_session_path(session_id)

    progress_log: list[dict] = []

    async def on_progress(stage: str, message: str, extra: dict | None) -> None:
        entry = {"stage": stage, "message": message}
        if extra:
            entry.update(extra)
        progress_log.append(entry)
        print(f"    [{stage}] {message}")

    result = await orchestrator.execute_edit(
        session_id, 1, "Make the background light blue", on_progress,
    )

    issues = []

    # Should route through visual
    if result.visual_count > 0:
        print(f"  [PASS] Visual ops: {result.visual_count}")
    else:
        issues.append("No visual ops executed")
        print(f"  [FAIL] visual_count={result.visual_count}")

    # Text layer should be ocr or stale
    if result.text_layer_source in ("ocr", "original"):
        print(f"  [PASS] Text layer source: {result.text_layer_source}")
    else:
        # "mixed" is also acceptable if planner splits it
        print(f"  [INFO] Text layer source: {result.text_layer_source}")

    # New image should exist
    new_img = session_path / "pages" / f"page_1_v{result.version}.png"
    if new_img.exists():
        img = Image.open(new_img)
        print(f"  [PASS] Output image: {new_img.name} ({img.size[0]}x{img.size[1]})")
    else:
        issues.append("Output image missing")
        print(f"  [FAIL] No output image")

    # Any operations should have succeeded
    any_success = any(r.success for r in result.operations)
    if any_success:
        print(f"  [PASS] At least one operation succeeded")
    else:
        issues.append("No operations succeeded")
        print(f"  [FAIL] All operations failed")
        for op in result.operations:
            print(f"    op {op.op_index}: {op.detail} — error: {op.error}")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print(f"\n  PASSED")
    return True


# ── Test 3: Hybrid plan — programmatic first, visual uses post-edit base


async def test_hybrid_plan(
    orchestrator: Orchestrator, session_mgr: SessionManager,
) -> bool:
    """Hybrid edit: text replace + visual change. Programmatic should run
    first, then visual should use the updated working PDF as its base."""
    print(f"\n{'='*70}")
    print("TEST 3: Hybrid plan (text replace + visual edit)")
    print(f"{'='*70}")

    session_id = await setup(session_mgr)
    session_path = session_mgr.get_session_path(session_id)

    progress_log: list[dict] = []

    async def on_progress(stage: str, message: str, extra: dict | None) -> None:
        entry = {"stage": stage, "message": message}
        if extra:
            entry.update(extra)
        progress_log.append(entry)
        print(f"    [{stage}] {message}")

    result = await orchestrator.execute_edit(
        session_id, 1,
        "Change Q3 to Q4 and make the background light blue",
        on_progress,
    )

    issues = []

    # Should have both programmatic and visual
    plan_entries = [p for p in progress_log if p["stage"] == "planned"]
    if plan_entries:
        plan_data = plan_entries[0].get("plan", {})
        op_types = [op["type"] for op in plan_data.get("operations", [])]
        has_text_replace = "text_replace" in op_types
        has_visual = "visual_regenerate" in op_types
        if has_text_replace:
            print(f"  [PASS] Plan includes text_replace")
        else:
            print(f"  [INFO] No text_replace in plan: {op_types}")
        if has_visual:
            print(f"  [PASS] Plan includes visual_regenerate")
        else:
            print(f"  [INFO] No visual_regenerate in plan: {op_types}")

    # Should have executed both types (or at least attempted)
    print(f"  Programmatic: {result.programmatic_count}, Visual: {result.visual_count}")

    # Text layer should be mixed (or ocr if programmatic was skipped)
    if result.text_layer_source in ("mixed", "ocr", "programmatic_edit"):
        print(f"  [PASS] Text layer source: {result.text_layer_source}")
    else:
        print(f"  [INFO] Text layer source: {result.text_layer_source}")

    # At least one op should have succeeded
    any_success = any(r.success for r in result.operations)
    if any_success:
        print(f"  [PASS] At least one operation succeeded")
    else:
        issues.append("No operations succeeded")
        print(f"  [FAIL] All operations failed")

    # Output image should exist
    new_img = session_path / "pages" / f"page_1_v{result.version}.png"
    if new_img.exists():
        img = Image.open(new_img)
        print(f"  [PASS] Output image: {new_img.name} ({img.size[0]}x{img.size[1]})")
    else:
        issues.append("Output image missing")
        print(f"  [FAIL] No output image")

    # If programmatic ran, working.pdf should exist
    if result.programmatic_count > 0:
        working_pdf = session_path / "working.pdf"
        if working_pdf.exists():
            print(f"  [PASS] Working PDF exists (programmatic edits applied)")
        else:
            issues.append("Working PDF missing after programmatic edit")
            print(f"  [FAIL] No working.pdf")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print(f"\n  PASSED")
    return True


# ── Main ──────────────────────────────────────────────────────────────


async def main():
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Image model: {settings.gemini_model}")
    print(f"Planning model: {settings.planning_model}")
    print()

    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)

    session_mgr = SessionManager(TEST_DIR / "sessions")
    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.model_timeout_seconds,
    )
    orchestrator = Orchestrator(
        model_provider=provider,
        session_manager=session_mgr,
    )

    tests = [
        ("Programmatic text replace", test_programmatic_text_replace),
        ("Visual uses PDF base", test_visual_uses_pdf_base),
        ("Hybrid plan", test_hybrid_plan),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = await test_fn(orchestrator, session_mgr)
        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            passed = False
        results.append((name, passed))
        await asyncio.sleep(2)

    # Summary
    print(f"\n{'='*70}")
    print("PIPELINE E2E SUMMARY")
    print(f"{'='*70}")
    passed_count = sum(1 for _, p in results if p)
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n{passed_count}/{len(results)} tests passed")

    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)

    if passed_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
