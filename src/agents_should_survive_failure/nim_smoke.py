"""Manual, credential-gated NVIDIA NIM smoke commands."""

import asyncio

from agents_should_survive_failure.provider_factory import (
    build_embedding_provider,
    build_model_provider,
)
from agents_should_survive_failure.providers import ModelRequest
from agents_should_survive_failure.settings import get_settings


def _require_nvidia_provider() -> None:
    if get_settings().model_provider != "nvidia_nim":
        raise SystemExit(
            "Set MODEL_PROVIDER=nvidia_nim and NVIDIA_API_KEY before running this smoke test."
        )


async def _model_smoke() -> None:
    _require_nvidia_provider()
    response = await build_model_provider(get_settings()).explain(
        ModelRequest(correlation_id="manual-nim-smoke", prompt="Reply with a concise health check.")
    )
    print(
        f"NIM model smoke passed: provider={response.provider} model={response.model} "
        f"input_tokens={response.input_tokens} output_tokens={response.output_tokens}"
    )


async def _embedding_smoke() -> None:
    _require_nvidia_provider()
    response = await build_embedding_provider(get_settings()).embed(
        "manual embedding health check", input_type="query"
    )
    print(
        f"NIM embedding smoke passed: provider={response.provider} model={response.model} "
        f"dimensions={len(response.vector)}"
    )


def model_main() -> None:
    asyncio.run(_model_smoke())


def embedding_main() -> None:
    asyncio.run(_embedding_smoke())
