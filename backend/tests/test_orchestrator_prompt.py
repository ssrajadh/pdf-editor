"""Test the orchestrator planning prompt against Gemini Flash (text-only).

Usage:
    cd backend
    .venv/bin/python -m tests.test_orchestrator_prompt
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.models.schemas import ExecutionPlan
from app.prompts.orchestrator_plan import build_orchestrator_messages

# ── Sample page data ─────────────────────────────────────────────────────

SAMPLE_PAGE_TEXT = """\
Q3 2025 Revenue Report
Prepared by: Finance Department
Date: September 30, 2025

Total Revenue: $4.2M
Growth Rate: 12% YoY

Sales by Region:
North America: $1.8M (43%)
Europe: $1.2M (29%)
Asia Pacific: $0.8M (19%)
Rest of World: $0.4M (9%)

Key Highlights:
- Record quarter for North America division
- New product line launched in Q3 2025
- Operating margin improved to 18.5%
- Headcount grew to 450 employees

Copyright 2025 Acme Corporation. All rights reserved.\
"""

SAMPLE_TEXT_BLOCKS_JSON = json.dumps([
    {"text": "Q3 2025 Revenue Report", "x0": 72, "y0": 72, "x1": 360, "y1": 96, "font_name": "Helvetica-Bold", "font_size": 24},
    {"text": "Prepared by: Finance Department", "x0": 72, "y0": 110, "x1": 300, "y1": 124, "font_name": "Helvetica", "font_size": 12},
    {"text": "Date: September 30, 2025", "x0": 72, "y0": 130, "x1": 250, "y1": 144, "font_name": "Helvetica", "font_size": 12},
    {"text": "Total Revenue: $4.2M", "x0": 72, "y0": 180, "x1": 240, "y1": 196, "font_name": "Helvetica-Bold", "font_size": 14},
    {"text": "Growth Rate: 12% YoY", "x0": 72, "y0": 200, "x1": 230, "y1": 216, "font_name": "Helvetica", "font_size": 12},
    {"text": "Sales by Region:", "x0": 72, "y0": 250, "x1": 200, "y1": 266, "font_name": "Helvetica-Bold", "font_size": 14},
    {"text": "North America: $1.8M (43%)", "x0": 72, "y0": 270, "x1": 280, "y1": 284, "font_name": "Helvetica", "font_size": 12},
    {"text": "Europe: $1.2M (29%)", "x0": 72, "y0": 288, "x1": 230, "y1": 302, "font_name": "Helvetica", "font_size": 12},
    {"text": "Asia Pacific: $0.8M (19%)", "x0": 72, "y0": 306, "x1": 270, "y1": 320, "font_name": "Helvetica", "font_size": 12},
    {"text": "Rest of World: $0.4M (9%)", "x0": 72, "y0": 324, "x1": 270, "y1": 338, "font_name": "Helvetica", "font_size": 12},
    {"text": "Key Highlights:", "x0": 72, "y0": 380, "x1": 190, "y1": 396, "font_name": "Helvetica-Bold", "font_size": 14},
    {"text": "- Record quarter for North America division", "x0": 82, "y0": 400, "x1": 380, "y1": 414, "font_name": "Helvetica", "font_size": 12},
    {"text": "- New product line launched in Q3 2025", "x0": 82, "y0": 418, "x1": 360, "y1": 432, "font_name": "Helvetica", "font_size": 12},
    {"text": "- Operating margin improved to 18.5%", "x0": 82, "y0": 436, "x1": 340, "y1": 450, "font_name": "Helvetica", "font_size": 12},
    {"text": "- Headcount grew to 450 employees", "x0": 82, "y0": 454, "x1": 320, "y1": 468, "font_name": "Helvetica", "font_size": 12},
    {"text": "Copyright 2025 Acme Corporation. All rights reserved.", "x0": 72, "y0": 720, "x1": 400, "y1": 734, "font_name": "Helvetica", "font_size": 10},
], indent=2)

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

# ── Test cases ───────────────────────────────────────────────────────────

TEST_CASES = [
    {
        "name": "Pure text swap",
        "instruction": "Change Q3 to Q4",
        "expect_ops": ["text_replace"],
        "expect_all_programmatic": True,
    },
    {
        "name": "Pure visual edit",
        "instruction": "Add a pie chart below the Sales by Region section showing the regional breakdown",
        "expect_ops": ["visual_regenerate"],
        "expect_all_programmatic": False,
    },
    {
        "name": "Hybrid (text + visual)",
        "instruction": "Change the title to Q4 Results and add a company logo in the top-right corner",
        "expect_ops": ["text_replace", "visual_regenerate"],
        "expect_all_programmatic": False,
    },
    {
        "name": "Style change",
        "instruction": "Make the title red and increase its font size to 30",
        "expect_ops": ["style_change"],
        "expect_all_programmatic": True,
    },
    {
        "name": "Ambiguous / vague instruction",
        "instruction": "Make this page look more professional and modern",
        "expect_ops": ["visual_regenerate"],
        "expect_all_programmatic": False,
    },
    {
        "name": "Text overflow -> visual fallback",
        "instruction": "Change 'Revenue Report' to 'Comprehensive Revenue and Profitability Analysis Report'",
        "expect_ops": ["text_replace", "visual_regenerate"],
        "expect_all_programmatic": False,
    },
    {
        "name": "Multiple text replacements",
        "instruction": "Update all instances of 2025 to 2026",
        "expect_ops": ["text_replace"],
        "expect_all_programmatic": True,
    },
]

# ── Gemini text-only API call ────────────────────────────────────────────

GEMINI_TEXT_MODEL = "gemini-2.5-flash"

async def call_gemini_text(messages: list[dict], api_key: str) -> str:
    """Call Gemini Flash for text-only generation."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent"
    body = {
        "contents": messages,
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, params={"key": api_key}, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")

        data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        raise RuntimeError(f"No candidates returned. Block reason: {block_reason}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = [p["text"] for p in parts if "text" in p]
    return "\n".join(text_parts)


def parse_plan_json(raw: str) -> ExecutionPlan:
    """Parse raw LLM output into an ExecutionPlan, handling markdown fences."""
    text = raw.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response:\n{raw[:300]}")

    json_str = text[start : end + 1]
    data = json.loads(json_str)
    return ExecutionPlan.model_validate(data)


# ── Main ─────────────────────────────────────────────────────────────────

def print_plan(plan: ExecutionPlan) -> None:
    """Pretty-print an execution plan."""
    print(f"  Summary: {plan.summary}")
    print(f"  All programmatic: {plan.all_programmatic}")
    print(f"  Execution order: {plan.execution_order}")
    print(f"  Operations ({len(plan.operations)}):")
    for i, op in enumerate(plan.operations):
        marker = "*" if i in plan.execution_order else " "
        print(f"    [{marker}{i}] {op.type} (confidence={op.confidence:.2f})")
        if op.type == "text_replace":
            print(f"         '{op.original_text}' -> '{op.replacement_text}' [{op.match_strategy}]")
        elif op.type == "style_change":
            print(f"         target='{op.target_text}' changes={op.changes}")
        elif op.type == "visual_regenerate":
            print(f"         region={op.region}")
            print(f"         prompt={op.prompt[:100]}{'...' if len(op.prompt) > 100 else ''}")
        print(f"         reasoning: {op.reasoning[:120]}")


async def run_test(test_case: dict, api_key: str) -> tuple[bool, str]:
    """Run a single test case. Returns (passed, details)."""
    name = test_case["name"]
    instruction = test_case["instruction"]

    messages = build_orchestrator_messages(
        user_instruction=instruction,
        page_text=SAMPLE_PAGE_TEXT,
        text_blocks_json=SAMPLE_TEXT_BLOCKS_JSON,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
    )

    t0 = time.monotonic()
    try:
        raw = await call_gemini_text(messages, api_key)
    except Exception as e:
        return False, f"API call failed: {e}"
    elapsed = time.monotonic() - t0

    try:
        plan = parse_plan_json(raw)
    except Exception as e:
        return False, f"Parse failed ({e}).\nRaw response:\n{raw[:500]}"

    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print(f"Instruction: \"{instruction}\"")
    print(f"API time: {elapsed:.2f}s")
    print_plan(plan)

    # Validate expectations
    issues = []

    actual_op_types = [op.type for op in plan.operations]
    for expected_type in test_case["expect_ops"]:
        if expected_type not in actual_op_types:
            issues.append(f"Expected op type '{expected_type}' not found in {actual_op_types}")

    if plan.all_programmatic != test_case["expect_all_programmatic"]:
        issues.append(
            f"all_programmatic={plan.all_programmatic}, expected={test_case['expect_all_programmatic']}"
        )

    # Validate execution_order references valid indices
    for idx in plan.execution_order:
        if idx < 0 or idx >= len(plan.operations):
            issues.append(f"execution_order index {idx} out of range (0-{len(plan.operations)-1})")

    # Validate programmatic ops come before visual in execution_order
    last_prog_pos = -1
    first_visual_pos = len(plan.execution_order)
    for pos, idx in enumerate(plan.execution_order):
        op = plan.operations[idx]
        if op.type in ("text_replace", "style_change"):
            last_prog_pos = max(last_prog_pos, pos)
        elif op.type == "visual_regenerate":
            first_visual_pos = min(first_visual_pos, pos)
    if last_prog_pos > first_visual_pos:
        issues.append("Programmatic ops should execute before visual ops in execution_order")

    if issues:
        print(f"\n  ISSUES:")
        for issue in issues:
            print(f"    - {issue}")
        return False, "; ".join(issues)

    print(f"\n  PASSED")
    return True, ""


async def main():
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Using model: {GEMINI_TEXT_MODEL}")
    print(f"Running {len(TEST_CASES)} test cases...")

    results = []
    for tc in TEST_CASES:
        passed, details = await run_test(tc, settings.gemini_api_key)
        results.append((tc["name"], passed, details))
        # Small delay to avoid rate limits
        await asyncio.sleep(1)

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
