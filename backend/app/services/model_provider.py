"""AI model abstraction layer."""

import abc
import base64
import io
import logging
import time

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


class ModelProvider(abc.ABC):
    """Abstract base class for AI model providers."""

    @property
    @abc.abstractmethod
    def provider_name(self) -> str: ...

    @abc.abstractmethod
    async def edit_image(
        self,
        image: Image.Image,
        prompt: str,
        conversation_history: list | None = None,
    ) -> Image.Image:
        """Take a page image and edit instruction, return edited image."""
        ...


def _pil_to_base64(image: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _base64_to_pil(data: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data)))


class GeminiProvider(ModelProvider):
    """Google Gemini image generation/editing provider."""

    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 2.0

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp", timeout: int = 60):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _build_contents(
        self,
        image: Image.Image,
        prompt: str,
        conversation_history: list | None = None,
    ) -> list[dict]:
        """Build the contents array for the Gemini API request.

        For multi-turn editing, conversation_history contains prior
        user/model turns as dicts: {"role": "user"|"model", "parts": [...]}.
        The current image+prompt is appended as the final user turn.
        """
        contents: list[dict] = []

        if conversation_history:
            contents.extend(conversation_history)

        # Current turn: image + text prompt
        image_b64 = _pil_to_base64(image)
        contents.append({
            "role": "user",
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": image_b64,
                    }
                },
                {"text": prompt},
            ],
        })

        return contents

    async def edit_image(
        self,
        image: Image.Image,
        prompt: str,
        conversation_history: list | None = None,
    ) -> Image.Image:
        url = f"{self.API_BASE}/{self._model}:generateContent"
        params = {"key": self._api_key}

        body = {
            "contents": self._build_contents(image, prompt, conversation_history),
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }

        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            t0 = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, params=params, json=body)

                elapsed = time.monotonic() - t0
                logger.info("Gemini API call took %.2fs (attempt %d)", elapsed, attempt + 1)

                if resp.status_code == 429:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning("Rate limited (429), retrying in %.1fs", backoff)
                    import asyncio
                    await asyncio.sleep(backoff)
                    continue

                if resp.status_code != 200:
                    error_detail = resp.text[:500]
                    raise RuntimeError(
                        f"Gemini API error {resp.status_code}: {error_detail}"
                    )

                data = resp.json()
                return self._extract_image(data)

            except httpx.TimeoutException as e:
                elapsed = time.monotonic() - t0
                logger.warning("Gemini API timeout after %.2fs (attempt %d)", elapsed, attempt + 1)
                last_exc = e
                continue
            except RuntimeError:
                raise
            except Exception as e:
                last_exc = e
                logger.error("Gemini API unexpected error: %s", e)
                continue

        raise RuntimeError(
            f"Gemini API failed after {self.MAX_RETRIES} attempts"
        ) from last_exc

    def _extract_image(self, response_data: dict) -> Image.Image:
        """Extract the generated image from the Gemini response."""
        candidates = response_data.get("candidates", [])
        if not candidates:
            # Check for content filtering
            block_reason = response_data.get("promptFeedback", {}).get("blockReason")
            if block_reason:
                raise RuntimeError(f"Content blocked by Gemini: {block_reason}")
            raise RuntimeError("Gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])

        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if inline_data and "data" in inline_data:
                return _base64_to_pil(inline_data["data"])

        # No image found — check finish reason
        finish_reason = candidates[0].get("finishReason", "")
        if finish_reason == "SAFETY":
            raise RuntimeError("Gemini blocked the response due to safety filters")

        text_parts = [p.get("text", "") for p in parts if "text" in p]
        text_summary = " ".join(text_parts)[:200]
        raise RuntimeError(
            f"Gemini response contained no image. Finish reason: {finish_reason}. "
            f"Text: {text_summary}"
        )


class ProviderFactory:
    """Factory for creating model provider instances."""

    _providers: dict[str, type[ModelProvider]] = {
        "gemini": GeminiProvider,
    }

    @classmethod
    def get_provider(cls, provider_name: str, api_key: str, **kwargs) -> ModelProvider:
        provider_cls = cls._providers.get(provider_name)
        if not provider_cls:
            available = ", ".join(cls._providers.keys())
            raise ValueError(
                f"Unknown provider '{provider_name}'. Available: {available}"
            )

        if provider_name == "gemini":
            from app.config import settings
            return GeminiProvider(
                api_key=api_key,
                model=kwargs.get("model", settings.gemini_model),
                timeout=kwargs.get("timeout", settings.model_timeout_seconds),
            )

        return provider_cls(api_key=api_key, **kwargs)
