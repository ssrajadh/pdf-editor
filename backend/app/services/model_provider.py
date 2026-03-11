"""AI model abstraction layer."""


class ModelProvider:
    """Abstraction for AI model calls."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def generate(self, prompt: str, image: bytes | None = None) -> str:
        """Generate a response from the AI model."""
        # TODO: implement model API call
        return ""
