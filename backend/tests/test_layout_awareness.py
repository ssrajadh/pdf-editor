"""Layout-aware planner tests.

Tests that the layout analyzer produces correct metadata and that the
planner adjusts its routing decisions based on layout complexity.

Usage:
    # Layout analyzer unit tests (no API key needed):
    python -m tests.test_layout_awareness

    # Include planner integration tests (needs GEMINI_API_KEY):
    GEMINI_API_KEY=... python -m tests.test_layout_awareness --all

    # Test with a real PDF:
    TEST_PDF_PATH=/path/to/resume.pdf python -m tests.test_layout_awareness --real
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.services.orchestrator import (
    analyze_layout_complexity,
    build_page_context,
    format_font_summary,
    Orchestrator,
    PageContext,
)
from app.services.model_provider import ProviderFactory
from app.services import pdf_service
from app.storage.session import SessionManager
from app.config import settings

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "test_document.pdf"
REAL_PDF_DEFAULT = Path(__file__).parent.parent.parent / "SohamR_Resume_Intern.pdf"
REAL_PDF = Path(os.environ.get("TEST_PDF_PATH", str(REAL_PDF_DEFAULT)))
STORAGE = Path(__file__).parent / "test_data_layout"

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"    [{status}] {detail}" if detail else f"    [{status}]")


# ---------------------------------------------------------------------------
# Test: Layout analyzer on simple fixture PDF
# ---------------------------------------------------------------------------


def test_layout_analyzer_simple():
    print(f"\n{'='*70}")
    print("TEST: Layout analyzer — simple fixture PDF")
    print(f"{'='*70}")

    if not FIXTURE_PDF.exists():
        from tests.generate_fixture import generate
        generate()

    info = analyze_layout_complexity(FIXTURE_PDF, 1)

    print(f"  Layout complexity: {info['layout_complexity']}")
    print(f"  Column count: {info['column_count']}")
    print(f"  Has CID fonts: {info['has_cid_fonts']}")
    print(f"  Text density: {info['text_density']}")
    print(f"  Fonts ({len(info['font_summary'])}):")
    for f in info["font_summary"]:
        print(f"    - {f.name}: standard={f.is_standard}, cid={f.is_cid}, "
              f"spans={f.usage_count}, sample={f.sample_text!r}")

    record("Simple: complexity is 'simple'",
           info["layout_complexity"] == "simple",
           f"Got: {info['layout_complexity']}")

    record("Simple: single column",
           info["column_count"] == 1,
           f"Got: {info['column_count']}")

    record("Simple: no CID fonts",
           not info["has_cid_fonts"],
           f"Got: {info['has_cid_fonts']}")

    record("Simple: fonts detected",
           len(info["font_summary"]) > 0,
           f"{len(info['font_summary'])} fonts found")

    has_standard = any(f.is_standard for f in info["font_summary"])
    record("Simple: has standard fonts",
           has_standard,
           f"Standard: {[f.name for f in info['font_summary'] if f.is_standard]}")

    record("Simple: text density in range",
           0 < info["text_density"] < 1,
           f"density={info['text_density']}")

    return info


# ---------------------------------------------------------------------------
# Test: Layout analyzer on real resume PDF
# ---------------------------------------------------------------------------


def test_layout_analyzer_resume():
    print(f"\n{'='*70}")
    print(f"TEST: Layout analyzer — real PDF ({REAL_PDF.name})")
    print(f"{'='*70}")

    if not REAL_PDF.exists():
        print(f"  SKIP: {REAL_PDF} not found")
        return None

    info = analyze_layout_complexity(REAL_PDF, 1)

    print(f"  Layout complexity: {info['layout_complexity']}")
    print(f"  Column count: {info['column_count']}")
    print(f"  Has CID fonts: {info['has_cid_fonts']}")
    print(f"  Text density: {info['text_density']}")
    print(f"  Fonts ({len(info['font_summary'])}):")
    for f in info["font_summary"]:
        print(f"    - {f.name}: standard={f.is_standard}, cid={f.is_cid}, "
              f"spans={f.usage_count}, sample={f.sample_text!r}")

    # Resumes are typically moderate or complex
    record("Resume: not 'simple' layout",
           info["layout_complexity"] in ("moderate", "complex"),
           f"Got: {info['layout_complexity']}")

    record("Resume: has CID fonts",
           info["has_cid_fonts"],
           f"Got: {info['has_cid_fonts']}")

    record("Resume: multiple fonts",
           len(info["font_summary"]) >= 2,
           f"{len(info['font_summary'])} fonts")

    return info


# ---------------------------------------------------------------------------
# Test: Font summary formatting
# ---------------------------------------------------------------------------


def test_font_summary_format():
    print(f"\n{'='*70}")
    print("TEST: Font summary formatting")
    print(f"{'='*70}")

    from app.models.schemas import FontInfo

    fonts = [
        FontInfo(name="Helvetica", is_standard=True, is_cid=False,
                 usage_count=15, sample_text="Revenue Report 2025"),
        FontInfo(name="NotoSansCJK-Bold", is_standard=False, is_cid=True,
                 usage_count=3, sample_text="日本語テスト"),
    ]

    formatted = format_font_summary(fonts)
    print(f"  Output:\n{formatted}")

    record("Format: includes font names",
           "Helvetica" in formatted and "NotoSansCJK-Bold" in formatted)
    record("Format: marks standard/non-standard",
           "standard" in formatted and "non-standard" in formatted)
    record("Format: marks CID",
           "CID" in formatted)

    empty = format_font_summary([])
    record("Format: handles empty list",
           "no fonts" in empty.lower(),
           f"Got: {empty!r}")


# ---------------------------------------------------------------------------
# Test: PageContext includes layout fields
# ---------------------------------------------------------------------------


async def test_page_context_with_layout(session_mgr: SessionManager, provider):
    print(f"\n{'='*70}")
    print("TEST: PageContext includes layout fields")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)

    ctx = await build_page_context(session_id, 1, provider, session_mgr)

    print(f"  page_num: {ctx.page_num}")
    print(f"  layout_complexity: {ctx.layout_complexity}")
    print(f"  column_count: {ctx.column_count}")
    print(f"  has_cid_fonts: {ctx.has_cid_fonts}")
    print(f"  text_density: {ctx.text_density}")
    print(f"  fonts: {len(ctx.font_summary)}")
    for f in ctx.font_summary:
        print(f"    - {f.name}: standard={f.is_standard}, cid={f.is_cid}")

    record("Context: has layout_complexity",
           ctx.layout_complexity in ("simple", "moderate", "complex"),
           f"Got: {ctx.layout_complexity}")
    record("Context: has column_count",
           ctx.column_count >= 1,
           f"Got: {ctx.column_count}")
    record("Context: has text_density",
           isinstance(ctx.text_density, (int, float)),
           f"Got: {ctx.text_density}")
    record("Context: has font_summary",
           len(ctx.font_summary) > 0,
           f"{len(ctx.font_summary)} fonts")

    # Verify caching works
    session_path = session_mgr.get_session_path(session_id)
    layout_cache = session_path / "edits" / "page_1_v0_layout.json"
    record("Context: layout cached",
           layout_cache.exists(),
           f"Cache at {layout_cache.name}")

    if layout_cache.exists():
        cached = json.loads(layout_cache.read_text())
        record("Context: cache has correct keys",
               all(k in cached for k in ("layout_complexity", "font_summary", "has_cid_fonts")),
               f"Keys: {list(cached.keys())}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Planner receives layout data (simple doc, same-length swap)
# ---------------------------------------------------------------------------


async def test_planner_simple_same_length(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Planner — simple doc, same-length swap (Q3→Q4)")
    print(f"{'='*70}")

    session_id = _create_session(session_mgr, FIXTURE_PDF)

    t0 = time.monotonic()
    plan = await orchestrator.plan_only(session_id, 1, "Change Q3 to Q4")
    plan_ms = int((time.monotonic() - t0) * 1000)

    print(f"  Planning time: {plan_ms}ms")
    print(f"  Summary: {plan.summary}")
    print(f"  All programmatic: {plan.all_programmatic}")
    for i, op in enumerate(plan.operations):
        print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
        print(f"        reasoning: {op.reasoning}")

    has_text_replace = any(op.type == "text_replace" for op in plan.operations)
    record("Simple same-length: routes to text_replace",
           has_text_replace,
           f"Types: {[op.type for op in plan.operations]}")

    if has_text_replace:
        tr_ops = [op for op in plan.operations if op.type == "text_replace"]
        high_conf = all(op.confidence >= 0.7 for op in tr_ops)
        record("Simple same-length: high confidence (>=0.7)",
               high_conf,
               f"Confidences: {[op.confidence for op in tr_ops]}")

    record("Simple same-length: all programmatic",
           plan.all_programmatic,
           f"all_programmatic={plan.all_programmatic}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Planner on complex layout, same-length swap (should still be programmatic)
# ---------------------------------------------------------------------------


async def test_planner_complex_same_length(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Planner — complex layout, same-length swap (2024→2025)")
    print(f"{'='*70}")

    if not REAL_PDF.exists():
        print(f"  SKIP: {REAL_PDF} not found")
        return

    session_id = _create_session(session_mgr, REAL_PDF)

    plan = await orchestrator.plan_only(session_id, 1, "Change 2024 to 2025")

    print(f"  Summary: {plan.summary}")
    for i, op in enumerate(plan.operations):
        print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
        print(f"        reasoning: {op.reasoning}")

    has_text_replace = any(op.type == "text_replace" for op in plan.operations)
    record("Complex same-length: routes to text_replace",
           has_text_replace,
           f"Same-length swap should still be programmatic. Types: {[op.type for op in plan.operations]}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Planner on complex layout, length-changing swap (should escalate)
# ---------------------------------------------------------------------------


async def test_planner_complex_overflow(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Planner — complex layout, length-changing swap")
    print(f"{'='*70}")

    if not REAL_PDF.exists():
        print(f"  SKIP: {REAL_PDF} not found")
        return

    session_id = _create_session(session_mgr, REAL_PDF)

    plan = await orchestrator.plan_only(
        session_id, 1,
        "Change the job title to Senior Principal Staff Software Engineering Manager and Technical Lead"
    )

    print(f"  Summary: {plan.summary}")
    for i, op in enumerate(plan.operations):
        print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
        print(f"        reasoning: {op.reasoning}")

    has_visual = any(op.type == "visual_regenerate" for op in plan.operations)
    has_low_conf_text = any(
        op.type == "text_replace" and op.confidence < 0.5
        for op in plan.operations
    )

    record("Complex overflow: routes to visual or low-conf text",
           has_visual or has_low_conf_text,
           f"Overflow on complex layout should escalate. "
           f"Visual: {has_visual}, low-conf text: {has_low_conf_text}")

    # Check if reasoning mentions layout complexity
    all_reasoning = " ".join(op.reasoning for op in plan.operations).lower()
    mentions_layout = any(kw in all_reasoning for kw in ("complex", "layout", "overflow", "longer"))
    record("Complex overflow: reasoning mentions layout/overflow",
           mentions_layout,
           f"Reasoning snippet: {all_reasoning[:200]}")

    session_mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test: Planner accounts for CID fonts in reasoning
# ---------------------------------------------------------------------------


async def test_planner_cid_font_awareness(session_mgr: SessionManager, orchestrator: Orchestrator):
    print(f"\n{'='*70}")
    print("TEST: Planner — CID font awareness")
    print(f"{'='*70}")

    if not REAL_PDF.exists():
        print(f"  SKIP: {REAL_PDF} not found")
        return

    session_id = _create_session(session_mgr, REAL_PDF)

    # Log the PageContext to see what the planner receives
    from app.services.orchestrator import build_page_context as bpc
    provider = ProviderFactory.get_provider(
        settings.model_provider, settings.gemini_api_key,
    )
    ctx = await bpc(session_id, 1, provider, session_mgr)

    print(f"  PageContext layout_complexity: {ctx.layout_complexity}")
    print(f"  PageContext has_cid_fonts: {ctx.has_cid_fonts}")
    print(f"  PageContext column_count: {ctx.column_count}")
    print(f"  PageContext text_density: {ctx.text_density}")
    print(f"  PageContext fonts ({len(ctx.font_summary)}):")
    for f in ctx.font_summary:
        flags = []
        if f.is_cid:
            flags.append("CID")
        if f.is_standard:
            flags.append("standard")
        else:
            flags.append("non-standard")
        print(f"    - {f.name} [{', '.join(flags)}] — {f.usage_count} spans")

    record("CID awareness: context has CID flag set",
           ctx.has_cid_fonts,
           f"has_cid_fonts={ctx.has_cid_fonts}")

    # Plan an edit and check that reasoning acknowledges fonts
    try:
        plan = await orchestrator.plan_only(
            session_id, 1, "Change the name to Test Name"
        )

        print(f"\n  Plan summary: {plan.summary}")
        for i, op in enumerate(plan.operations):
            print(f"    [{i}] {op.type}: confidence={op.confidence:.2f}")
            print(f"        reasoning: {op.reasoning}")

        record("CID awareness: plan generated successfully",
               len(plan.operations) > 0,
               f"{len(plan.operations)} operations")
    except Exception as e:
        print(f"\n  ⚠️  Plan call failed (likely rate limit): {e}")
        record("CID awareness: plan generated successfully",
               False, f"API error: {e}")

    session_mgr.cleanup_session(session_id)


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


async def _noop_progress(stage: str, message: str, extra=None):
    pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    import shutil
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True)

    session_mgr = SessionManager(STORAGE)
    has_api_key = bool(settings.gemini_api_key)
    run_all = "--all" in sys.argv
    run_real = "--real" in sys.argv or REAL_PDF.exists()

    # --- Unit tests (no API key) ---
    test_layout_analyzer_simple()
    test_font_summary_format()

    if REAL_PDF.exists():
        test_layout_analyzer_resume()
    elif run_real:
        print(f"\n  ⚠️  Real PDF not found at: {REAL_PDF}")

    # --- Integration tests (need API key) ---
    if has_api_key:
        provider = ProviderFactory.get_provider(
            settings.model_provider, settings.gemini_api_key,
        )
        orchestrator = Orchestrator(
            model_provider=provider, session_manager=session_mgr,
        )

        await test_page_context_with_layout(session_mgr, provider)
        await test_planner_simple_same_length(session_mgr, orchestrator)

        if run_all or run_real:
            if REAL_PDF.exists():
                await test_planner_complex_same_length(session_mgr, orchestrator)
                await test_planner_complex_overflow(session_mgr, orchestrator)
                await test_planner_cid_font_awareness(session_mgr, orchestrator)
            else:
                print(f"\n  ⚠️  Skipping real PDF planner tests — {REAL_PDF} not found")
    else:
        print("\n  ⚠️  GEMINI_API_KEY not set — skipping planner integration tests")

    # --- Summary ---
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for name, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    print(f"\n  {passed}/{passed + failed} tests passed")

    if STORAGE.exists():
        shutil.rmtree(STORAGE)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
