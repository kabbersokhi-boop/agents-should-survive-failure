import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def harness() -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "scripts/operator_crash_demo.py"
    spec = importlib.util.spec_from_file_location("operator_crash_harness", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restart_preserves_container_configuration_and_does_not_reconcile_dependencies(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def inspected_worker(project: str) -> tuple[str, dict[str, object]]:
        assert project == "agents-portfolio"
        return "inspected-worker-id", {}

    def run(args: list[str], **kwargs: object) -> None:
        assert kwargs["check"] is True
        calls.append(tuple(args))

    monkeypatch.setattr(harness, "inspected_worker", inspected_worker)
    monkeypatch.setattr(harness.subprocess, "run", run)
    harness.restart_worker("agents-portfolio")
    assert calls == [("docker", "start", "inspected-worker-id")]


@pytest.mark.parametrize("project", ["customer-ops-ai", "agents-verify", "agents-portfolio;other"])
def test_unrelated_projects_are_rejected_before_docker_access(
    harness: ModuleType, project: str
) -> None:
    with pytest.raises(ValueError, match="restricted"):
        harness.compose(project, "kill", "-s", "KILL", "worker")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1/private",
        "http://key@localhost",
        "http://localhost?key=secret",
    ],
)
def test_remote_or_credential_bearing_urls_are_rejected(harness: ModuleType, url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        harness.local_url(url)
