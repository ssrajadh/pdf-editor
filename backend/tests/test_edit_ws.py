"""Standalone WebSocket test for the edit pipeline.

Usage:
    1. Start the backend:  uvicorn app.main:app --reload --port 8000
    2. Upload a PDF:       curl -X POST http://localhost:8000/api/pdf/upload -F "file=@test.pdf"
    3. Run this test:      .venv/bin/python -m tests.test_edit_ws <session_id> [page_num] [prompt]

Connects to the edit WebSocket, sends an edit instruction, and prints
every progress/completion/error message as it arrives.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    try:
        import websockets
    except ImportError:
        print("ERROR: 'websockets' package required.  pip install websockets")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python -m tests.test_edit_ws <session_id> [page_num] [prompt]")
        sys.exit(1)

    session_id = sys.argv[1]
    page_num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    prompt = sys.argv[3] if len(sys.argv) > 3 else "Change the title text to say Hello World"

    url = f"ws://localhost:8000/api/edit/ws/{session_id}"
    print(f"Connecting to {url}")

    async with websockets.connect(url) as ws:
        edit_msg = json.dumps({
            "type": "edit",
            "page_num": page_num,
            "prompt": prompt,
        })
        print(f"Sending: {edit_msg}")
        t0 = time.monotonic()
        await ws.send(edit_msg)

        while True:
            raw = await ws.recv()
            elapsed = time.monotonic() - t0
            msg = json.loads(raw)
            msg_type = msg.get("type", "unknown")

            if msg_type == "progress":
                print(f"  [{elapsed:6.1f}s] PROGRESS  stage={msg['stage']:<14s}  {msg['message']}")
            elif msg_type == "complete":
                result = msg["result"]
                print(f"  [{elapsed:6.1f}s] COMPLETE  version={result['version']}  "
                      f"time={result['processing_time_ms']:.0f}ms  "
                      f"text_preserved={result['text_layer_preserved']}")
                break
            elif msg_type == "error":
                print(f"  [{elapsed:6.1f}s] ERROR     {msg['message']}")
                break
            else:
                print(f"  [{elapsed:6.1f}s] {msg_type.upper():<10s}  {msg}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
