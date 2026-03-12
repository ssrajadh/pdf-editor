"""Standalone test for Gemini image editing integration.

Usage:
    cd backend
    .venv/bin/python -m tests.test_model_provider [image_path]

If no image_path is given, uses the first rendered page from the most recent session.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.config import settings
from app.services.model_provider import ProviderFactory


async def main():
    if len(sys.argv) > 1:
        img_path = Path(sys.argv[1])
    else:
        data_dir = settings.storage_path
        if not data_dir.exists():
            print("Storage directory does not exist. Upload a PDF first or pass an image path.")
            sys.exit(1)
        sessions = sorted(
            (p for p in data_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        img_path = None
        for s in sessions:
            pages_dir = s / "pages"
            if pages_dir.exists():
                pngs = sorted(pages_dir.glob("page_1_v*.png"))
                if pngs:
                    img_path = pngs[-1]
                    break
        if not img_path:
            print("No session page images found. Upload a PDF first or pass an image path.")
            sys.exit(1)

    print(f"Input image: {img_path}")
    image = Image.open(img_path)
    print(f"Image size: {image.size}")

    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    provider = ProviderFactory.get_provider(
        settings.model_provider,
        settings.gemini_api_key,
    )
    print(f"Provider: {provider.provider_name}, model: {settings.gemini_model}")

    prompt = "Change the title text to say Hello World"
    print(f"Prompt: {prompt}")
    print("Sending to Gemini...")

    t0 = time.monotonic()
    try:
        result = await provider.edit_image(image, prompt)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    elapsed = time.monotonic() - t0
    print(f"Response received in {elapsed:.2f}s")
    print(f"Result image size: {result.size}")

    output_path = Path("/tmp/gemini_edit_test_result.png")
    result.save(output_path)
    print(f"Result saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
