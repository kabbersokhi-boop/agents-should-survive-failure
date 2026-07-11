from types import SimpleNamespace

import pytest

from agents_should_survive_failure import workflow_start_cli
from agents_should_survive_failure.workflow_starts import RecoveryResult


@pytest.mark.asyncio
async def test_recovery_cli_closes_resources_and_returns_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class Resources:
        engine = object()
        temporal_client = object()

        async def close(self) -> None:
            nonlocal closed
            closed = True

    async def create_resources(*args: object, **kwargs: object) -> Resources:
        del args, kwargs
        return Resources()

    async def recover(self: object) -> RecoveryResult:
        del self
        return RecoveryResult(inspected=3, unavailable=0)

    monkeypatch.setattr(workflow_start_cli, "create_resources", create_resources)
    monkeypatch.setattr(workflow_start_cli, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(workflow_start_cli.WorkflowStartCoordinator, "recover", recover)

    assert await workflow_start_cli._recover() == (3, 0)  # pyright: ignore[reportPrivateUsage]
    assert closed


def test_recovery_cli_exits_nonzero_when_records_remain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def recover() -> tuple[int, int]:
        return 3, 1

    monkeypatch.setattr(workflow_start_cli, "_recover", recover)

    with pytest.raises(SystemExit, match="1"):
        workflow_start_cli.recovery_main()
    assert capsys.readouterr().out == "workflow start recovery: inspected=3 unavailable=1\n"
