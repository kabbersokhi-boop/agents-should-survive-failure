"""Provider-independent model contracts with deterministic local behavior."""

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ModelRequest:
    correlation_id: str
    prompt: str


@dataclass(frozen=True)
class ModelResponse:
    provider: str
    model: str
    summary: str
    input_tokens: int
    output_tokens: int


class ModelProvider(Protocol):
    async def explain(self, request: ModelRequest) -> ModelResponse: ...


class DeterministicModelProvider:
    """Stable provider used by local runs and CI; it cannot authorize decisions."""

    provider_name = "deterministic_mock"
    model_name = "deterministic-explainer-v1"

    async def explain(self, request: ModelRequest) -> ModelResponse:
        words = request.prompt.split()
        return ModelResponse(
            provider=self.provider_name,
            model=self.model_name,
            summary="Deterministic explanation based only on supplied evidence.",
            input_tokens=len(words),
            output_tokens=8,
        )


class NVIDIAModelProvider:
    """NVIDIA's OpenAI-compatible chat-completions adapter."""

    def __init__(self, *, api_key: str | None, base_url: str, model: str) -> None:
        if not api_key:
            raise ValueError("NVIDIA_API_KEY is required for the NVIDIA provider")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def explain(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": 0,
        }
        try:
            response = await asyncio.to_thread(self._post, payload)
        except (HTTPError, URLError) as error:
            raise RuntimeError("NVIDIA provider request failed") from error
        choices = response.get("choices", [])
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("NVIDIA provider returned no completion")
        choice = cast(dict[str, object], choices[0])
        message = choice.get("message", {})
        if not isinstance(message, dict):
            raise RuntimeError("NVIDIA provider returned invalid message")
        message_data = cast(dict[str, object], message)
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        usage_data = cast(dict[str, object], usage)
        return ModelResponse(
            provider="nvidia_nim",
            model=self._model,
            summary=str(message_data.get("content", "")),
            input_tokens=self._token_count(usage_data.get("prompt_tokens", 0)),
            output_tokens=self._token_count(usage_data.get("completion_tokens", 0)),
        )

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            decoded: object = json.load(response)
        if not isinstance(decoded, dict):
            raise RuntimeError("NVIDIA provider returned invalid JSON")
        return cast(dict[str, object], decoded)

    @staticmethod
    def _token_count(value: object) -> int:
        return value if isinstance(value, int) else 0
