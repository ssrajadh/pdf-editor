"""End-to-end test of the Orchestrator: plan + execute.

Usage:
    cd backend
    .venv/bin/python -m tests.test_orchestrator_e2e

Creates a test PDF, uploads it, and runs three edit scenarios:
1. Pure text replacement: "Change Q3 to Q4"
2. Pure visual edit: "Replace the bar chart with a pie chart"
3. Hybrid: "Change Q3 to Q4 and replace the bar chart with a pie chart"

All ops route through visual for now (programmatic not yet implemented).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.schemas import ExecutionResult
from app.services.model_provider import GeminiProvider
from app.services.orchestrator import Orchestrator
from app.storage.session import SessionManager
from app.services import pdf_service


# ── Create test PDF ──────────────────────────────────────────────────────

def create_test_pdf(path: Path) -> None:
    """Create a PDF with text + a simple bar chart for testing."""
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 24)
    c.drawString(72, h - 72, "Q3 2025 Revenue Report")

    c.setFont("Helvetica", 12)
    c.drawString(72, h - 100, "Prepared by: Finance Department")
    c.drawString(72, h - 118, "Date: September 30, 2025")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, h - 170, "Total Revenue: $4.2M")
    c.setFont("Helvetica", 12)
    c.drawString(72, h - 190, "Growth Rate: 12% YoY")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, h - 240, "Sales by Region:")

    bars = [("NA", 180, 0.43), ("EU", 120, 0.29), ("APAC", 80, 0.19), ("RoW", 40, 0.09)]
    for i, (label, height, pct) in enumerate(bars):
        x = 72 + i * 120
        c.setFillColorRGB(0.08, 0.14, 0.49)
        c.rect(x, h - 480, 80, height, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 10)
        c.drawString(x + 20, h - 500, f"{label} ({pct:.0%})")

    c.setFont("Helvetica", 10)
    c.drawString(72, 40, "Copyright 2025 Acme Corporation. All rights reserved.")

    c.save()


# ── Test runner ──────────────────────────────────────────────────────────

TEST_CASES = [
    {
        "name": "Pure text replacement",
        "instruction": "Change Q3 to Q4",
        "expect_plan_ops": ["text_replace"],
        "expect_all_programmatic": True,
    },
    {
        "name": "Pure visual edit",
        "instruction": "Replace the bar chart with a pie chart showing the same data",
        "expect_plan_ops": ["visual_regenerate"],
        "expect_all_programmatic": False,
    },
    {
        "name": "Hybrid (text + visual)",
        "instruction": "Change Q3 to Q4 and replace the bar chart with a pie chart",
        "expect_plan_ops": ["text_replace", "visual_regenerate"],
        "expect_all_programmatic": False,
    },
]


async def setup_session(session_mgr: SessionManager) -> str:
    """Create a session with the test PDF."""
    pdf_path = Path("/tmp/test_orchestrator_e2e.pdf")
    create_test_pdf(pdf_path)

    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = session_mgr.create_session(pdf_bytes, "test.pdf", page_count)

    session_path = session_mgr.get_session_path(session_id)
    await pdf_service.render_all_pages_async(
        session_path / "original.pdf", session_path / "pages",
    )

    return session_id


async def run_test(
    test_case: dict,
    orchestrator: Orchestrator,
    session_mgr: SessionManager,
) -> tuple[bool, str]:
    """Run a single test case. Returns (passed, details)."""
    name = test_case["name"]
    instruction = test_case["instruction"]

    # Fresh session for each test so version numbers are clean
    session_id = await setup_session(session_mgr)

    progress_log: list[dict] = []

    async def on_progress(stage: str, message: str, extra: dict | None) -> None:
        entry = {"stage": stage, "message": message}
        if extra:
            entry["extra"] = extra
        progress_log.append(entry)
        print(f"    [{stage}] {message}")

    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print(f"Instruction: \"{instruction}\"")
    print(f"Session: {session_id}")
    print(f"{'='*70}")

    t0 = time.monotonic()

    # --- Phase 1: Plan ---
    try:
        plan = await orchestrator.plan(session_id, 1, instruction, on_progress)
    except Exception as e:
        return False, f"Planning failed: {e}"

    plan_elapsed = time.monotonic() - t0
    print(f"\n  Plan ({plan_elapsed:.1f}s):")
    print(f"    summary: {plan.summary}")
    print(f"    all_programmatic: {plan.all_programmatic}")
    print(f"    execution_order: {plan.execution_order}")
    for i, op in enumerate(plan.operations):
        marker = "*" if i in plan.execution_order else " "
        print(f"    [{marker}{i}] {op.type} (confidence={op.confidence:.2f})")
        if op.type == "text_replace":
            print(f"         '{op.original_text}' -> '{op.replacement_text}' [{op.match_strategy}]")
        elif op.type == "visual_regenerate":
            print(f"         region={op.region}")
            print(f"         prompt={op.prompt[:100]}{'...' if len(op.prompt) > 100 else ''}")
        print(f"         reasoning: {op.reasoning[:120]}")

    # Validate plan
    issues = []
    actual_op_types = [op.type for op in plan.operations]
    for expected_type in test_case["expect_plan_ops"]:
        if expected_type not in actual_op_types:
            issues.append(f"Expected op type '{expected_type}' not in {actual_op_types}")

    if plan.all_programmatic != test_case["expect_all_programmatic"]:
        issues.append(
            f"all_programmatic={plan.all_programmatic}, "
            f"expected={test_case['expect_all_programmatic']}"
        )

    if issues:
        print(f"\n  PLAN ISSUES:")
        for issue in issues:
            print(f"    - {issue}")

    # --- Phase 2: Execute ---
    print(f"\n  Executing plan...")
    t1 = time.monotonic()

    try:
        result = await orchestrator.execute(
            session_id, 1, plan, instruction, on_progress,
        )
    except Exception as e:
        return False, f"Execution failed: {e}"

    exec_elapsed = time.monotonic() - t1
    print(f"\n  Result ({exec_elapsed:.1f}s):")
    print(f"    version: {result.version}")
    print(f"    total_time_ms: {result.total_time_ms}")
    print(f"    programmatic_count: {result.programmatic_count}")
    print(f"    visual_count: {result.visual_count}")
    print(f"    text_layer_source: {result.text_layer_source}")
    for op_r in result.operations:
        status = "OK" if op_r.success else "FAIL"
        print(f"    [{status}] op {op_r.op_index} ({op_r.op_type.value}) "
              f"via {op_r.path} — {op_r.time_ms}ms")
        print(f"           {op_r.detail[:100]}")
        if op_r.error:
            print(f"           ERROR: {op_r.error[:100]}")

    # Verify execution
    any_success = any(r.success for r in result.operations)
    if not any_success:
        issues.append("No operations succeeded")

    # Verify the new version image exists
    session_path = session_mgr.get_session_path(session_id)
    new_image = session_path / "pages" / f"page_1_v{result.version}.png"
    if not new_image.exists():
        issues.append(f"New version image not found: {new_image}")
    else:
        from PIL import Image
        img = Image.open(new_image)
        print(f"\n  New version image: {new_image.name} ({img.size[0]}x{img.size[1]})")

    # Log the full plan JSON for inspection
    print(f"\n  Plan JSON:")
    print(f"    {json.dumps(plan.model_dump(), indent=2)[:500]}...")

    if issues:
        print(f"\n  ISSUES:")
        for issue in issues:
            print(f"    - {issue}")
        return False, "; ".join(issues)

    # Cleanup
    session_mgr.cleanup_session(session_id)

    print(f"\n  PASSED")
    return True, ""


async def main():
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Image model: {settings.gemini_model}")
    print(f"Planning model: {settings.planning_model}")
    print(f"Running {len(TEST_CASES)} end-to-end test cases...")

    session_mgr = SessionManager(settings.storage_path)
    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.model_timeout_seconds,
    )
    orchestrator = Orchestrator(
        model_provider=provider,
        session_manager=session_mgr,
    )

    results = []
    for tc in TEST_CASES:
        passed, details = await run_test(tc, orchestrator, session_mgr)
        results.append((tc["name"], passed, details))
        # Brief pause between tests for rate limiting
        await asyncio.sleep(2)

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    passed_count = sum(1 for _, p, _ in results if p)
    for name, passed, details in results:
        status = "PASS" if passed else "FAIL"
        line = f"  [{status}] {name}"
        if not passed:
            line += f" — {details[:80]}"
        print(line)
    print(f"\n{passed_count}/{len(results)} tests passed")

    if passed_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
