import uuid

import pytest

from agents_should_survive_failure.persistence import cli
from agents_should_survive_failure.persistence.models import EvaluationStatus


def test_evaluate_command_accepts_a_passing_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid.uuid4()

    async def evaluate(idempotency_key: str) -> tuple[uuid.UUID, EvaluationStatus]:
        assert idempotency_key == "release-1"
        return run_id, EvaluationStatus.SUCCEEDED

    monkeypatch.setattr(cli, "_evaluate_vendor_onboarding", evaluate)

    cli.evaluate_main("release-1")


def test_evaluate_command_fails_for_production_evidence_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()

    async def evaluate(idempotency_key: str) -> tuple[uuid.UUID, EvaluationStatus]:
        assert idempotency_key == "release-1"
        return run_id, EvaluationStatus.FAILED

    monkeypatch.setattr(cli, "_evaluate_vendor_onboarding", evaluate)

    with pytest.raises(SystemExit, match="failed production workflow evidence checks"):
        cli.evaluate_main("release-1")
