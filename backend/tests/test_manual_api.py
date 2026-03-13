"""Manual test checklist — exercised via HTTP API against the running server.

Requires the backend to be running at localhost:8000.
Uses the real resume PDF for CID font validation.

Usage:
    TEST_PDF_PATH=../SohamR_Resume_Intern.pdf python -m tests.test_manual_api
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import websockets

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"
PDF_PATH = Path(os.environ.get("TEST_PDF_PATH", "../SohamR_Resume_Intern.pdf"))

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"  [{status}] {name}: {detail}" if detail else f"  [{status}] {name}")


async def main():
    if not PDF_PATH.exists():
        print(f"PDF not found: {PDF_PATH}")
        sys.exit(1)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # ---------------------------------------------------------------
        # 1. Health check
        # ---------------------------------------------------------------
        print("\n=== 1. Health Check ===")
        r = await client.get("/health")
        record("Health check", r.status_code == 200, f"status={r.status_code}")

        # ---------------------------------------------------------------
        # 2. Upload PDF
        # ---------------------------------------------------------------
        print("\n=== 2. Upload PDF ===")
        with open(PDF_PATH, "rb") as f:
            r = await client.post(
                "/api/pdf/upload",
                files={"file": (PDF_PATH.name, f, "application/pdf")},
            )
        record("Upload returns 200", r.status_code == 200, f"status={r.status_code}")

        data = r.json()
        session_id = data.get("session_id", "")
        page_count = data.get("page_count", 0)
        record("Upload returns session_id", bool(session_id), f"session_id={session_id[:12]}...")
        record("Upload returns page_count", page_count > 0, f"page_count={page_count}")
        print(f"  Session: {session_id}")

        # ---------------------------------------------------------------
        # 3. Get session info
        # ---------------------------------------------------------------
        print("\n=== 3. Session Info ===")
        r = await client.get(f"/api/pdf/{session_id}/info")
        record("Session info", r.status_code == 200, json.dumps(r.json(), indent=2)[:200])

        # ---------------------------------------------------------------
        # 4. Get page image
        # ---------------------------------------------------------------
        print("\n=== 4. Page Image ===")
        r = await client.get(f"/api/pdf/{session_id}/page/1/image")
        record("Page image returns PNG",
               r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"),
               f"size={len(r.content)} bytes, type={r.headers.get('content-type')}")

        # ---------------------------------------------------------------
        # 5. Text extraction
        # ---------------------------------------------------------------
        print("\n=== 5. Text Extraction ===")
        r = await client.get(f"/api/pdf/{session_id}/page/1/text")
        text_data = r.json()
        full_text = text_data.get("full_text", "")
        blocks = text_data.get("blocks", [])
        record("Text extraction works",
               len(full_text) > 100 and len(blocks) > 50,
               f"{len(full_text)} chars, {len(blocks)} blocks")
        print(f"  First 150 chars: {full_text[:150]!r}")

        # ---------------------------------------------------------------
        # 6. Plan preview (CID font routing)
        # ---------------------------------------------------------------
        print("\n=== 6. Plan Preview (CID Font Routing) ===")
        t0 = time.monotonic()
        r = await client.post(
            f"/api/edit/{session_id}/page/1/plan-preview",
            json={"prompt": "Change the name to Test Name"},
            timeout=60,
        )
        plan_ms = int((time.monotonic() - t0) * 1000)
        record("Plan preview returns 200", r.status_code == 200, f"{plan_ms}ms")

        plan = r.json()
        print(f"  Plan: {json.dumps(plan, indent=2)[:500]}")

        has_visual = any(op.get("type") == "visual_regenerate" for op in plan.get("operations", []))
        no_text_replace = not any(op.get("type") == "text_replace" for op in plan.get("operations", []))
        record("CID: routes to visual_regenerate", has_visual,
               f"ops={[op['type'] for op in plan.get('operations', [])]}")
        record("CID: no text_replace attempted", no_text_replace)

        # ---------------------------------------------------------------
        # 7. WebSocket edit (real edit on CID font PDF)
        # ---------------------------------------------------------------
        print("\n=== 7. WebSocket Edit ===")
        ws_messages = []
        t0 = time.monotonic()

        try:
            async with websockets.connect(
                f"{WS_URL}/api/edit/ws/{session_id}",
                close_timeout=5,
            ) as ws:
                await ws.send(json.dumps({
                    "type": "edit",
                    "page_num": 1,
                    "prompt": "Change the email to test@example.com",
                }))

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        msg = json.loads(raw)
                        ws_messages.append(msg)
                        msg_type = msg.get("type", "")
                        print(f"    WS {msg_type}: {msg.get('message', msg.get('stage', ''))[:100]}")

                        if msg_type == "complete":
                            break
                        if msg_type == "error":
                            print(f"    ERROR: {msg}")
                            break
                    except asyncio.TimeoutError:
                        print("    TIMEOUT waiting for WS message")
                        break
        except Exception as e:
            print(f"    WS connection error: {e}")

        edit_ms = int((time.monotonic() - t0) * 1000)

        stages = [m.get("stage", m.get("type", "")) for m in ws_messages]
        record("WS: received progress events", "planning" in stages,
               f"stages={stages}, time={edit_ms}ms")

        complete_msgs = [m for m in ws_messages if m.get("type") == "complete"]
        if complete_msgs:
            result = complete_msgs[0].get("result", {})
            record("WS: edit completed", bool(result),
                   f"prog={result.get('programmatic_count')}, vis={result.get('visual_count')}, "
                   f"v={result.get('version')}, time={result.get('total_time_ms')}ms")

            planned_msgs = [m for m in ws_messages if m.get("stage") == "planned"]
            has_plan_data = planned_msgs and planned_msgs[0].get("plan")
            record("WS: plan data in progress", bool(has_plan_data))
        else:
            record("WS: edit completed", False, "No complete message received")

        # ---------------------------------------------------------------
        # 8. Check updated page image
        # ---------------------------------------------------------------
        print("\n=== 8. Updated Page Image ===")
        r = await client.get(f"/api/pdf/{session_id}/info")
        info = r.json()
        current_v = info.get("current_page_versions", {}).get("1", 0)
        record("Version incremented", current_v > 0, f"current version={current_v}")

        r = await client.get(f"/api/pdf/{session_id}/page/1/image?v={current_v}")
        record("Updated image available",
               r.status_code == 200 and len(r.content) > 1000,
               f"size={len(r.content)} bytes")

        # ---------------------------------------------------------------
        # 9. Edit history
        # ---------------------------------------------------------------
        print("\n=== 9. Edit History ===")
        r = await client.get(f"/api/edit/{session_id}/page/1/history")
        history = r.json()
        record("Edit history has entries", len(history) > 0, f"{len(history)} entries")
        if history:
            entry = history[-1]
            print(f"  Latest: v{entry.get('version')} — {entry.get('prompt', '')[:60]}")
            print(f"    plan_summary: {entry.get('plan_summary', 'N/A')[:80]}")
            print(f"    text_layer_source: {entry.get('text_layer_source', 'N/A')}")
            print(f"    working_pdf_modified: {entry.get('working_pdf_modified', 'N/A')}")

        # ---------------------------------------------------------------
        # 10. Export PDF
        # ---------------------------------------------------------------
        print("\n=== 10. Export PDF ===")
        r = await client.post(f"/api/pdf/{session_id}/export", timeout=30)
        record("Export returns PDF",
               r.status_code == 200 and len(r.content) > 1000,
               f"size={len(r.content)} bytes, type={r.headers.get('content-type')}")

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print(f"\n{'='*60}")
    print("MANUAL TEST SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for name, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {passed}/{passed + failed} tests passed")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
