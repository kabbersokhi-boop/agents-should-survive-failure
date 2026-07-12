import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from agents_should_survive_failure import sandbox as sandbox_module
from agents_should_survive_failure.sandbox import (
    DockerSandbox,
    SandboxPolicy,
    SandboxPolicyError,
    SandboxRequest,
)


class FakeStream:
    def __init__(self, chunks: list[bytes], *, delay_seconds: float = 0) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds

    async def read(self, count: int) -> bytes:
        del count
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        return self._chunks.pop(0) if self._chunks else b""


class FakeProcess:
    def __init__(
        self, chunks: list[bytes], *, exit_code: int = 0, delay_seconds: float = 0
    ) -> None:
        self.stdout = FakeStream(chunks, delay_seconds=delay_seconds)
        self.returncode: int | None = None
        self._exit_code = exit_code
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._exit_code
        return self.returncode


async def process_iterator(*processes: FakeProcess) -> AsyncIterator[FakeProcess]:
    for process in processes:
        yield process


def install_fake_processes(monkeypatch: MonkeyPatch, processes: AsyncIterator[FakeProcess]) -> None:
    async def create_subprocess_exec(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        del args, kwargs
        return cast(asyncio.subprocess.Process, await anext(processes))

    monkeypatch.setattr(sandbox_module.asyncio, "create_subprocess_exec", create_subprocess_exec)


def test_sandbox_command_uses_restrictive_docker_defaults() -> None:
    sandbox = DockerSandbox(SandboxPolicy(environment_allowlist=frozenset({"SAFE_VALUE"})))

    command = sandbox.build_command(
        SandboxRequest(command=("python", "-c", "print('ok')"), environment={"SAFE_VALUE": "1"}),
        workspace=Path("/tmp/survive-sandbox-test"),
        container_name="survive-sandbox-test",
    )

    assert command[command.index("--network") : command.index("--network") + 2] == [
        "--network",
        "none",
    ]
    assert "--read-only" in command
    assert command[command.index("--user") : command.index("--user") + 2] == [
        "--user",
        "65532:65532",
    ]
    assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == [
        "--cap-drop",
        "ALL",
    ]
    assert "no-new-privileges" in command
    assert "--pids-limit" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert "/var/run/docker.sock" not in " ".join(command)


def test_sandbox_rejects_unapproved_environment_names() -> None:
    sandbox = DockerSandbox()

    with pytest.raises(SandboxPolicyError, match="not permitted"):
        sandbox.build_command(
            SandboxRequest(command=("python", "-V"), environment={"SECRET": "value"}),
            workspace=Path("/tmp/survive-sandbox-test"),
            container_name="survive-sandbox-test",
        )


def test_sandbox_request_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="invalid argument"):
        SandboxRequest(command=("python", "bad\x00argument"))


@pytest.mark.asyncio
async def test_sandbox_executes_and_cleans_up(monkeypatch: MonkeyPatch) -> None:
    workload = FakeProcess([b"sandbox output\n", b""])
    cleanup = FakeProcess([])
    processes = process_iterator(workload, cleanup)
    install_fake_processes(monkeypatch, processes)

    result = await DockerSandbox().execute(SandboxRequest(command=("python", "-V")))

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.output == "sandbox output\n"
    assert cleanup.returncode == 0


@pytest.mark.asyncio
async def test_sandbox_stops_excessive_output_and_cleans_up(monkeypatch: MonkeyPatch) -> None:
    workload = FakeProcess([b"abcdefgh", b""])
    cleanup = FakeProcess([])
    processes = process_iterator(workload, cleanup)
    install_fake_processes(monkeypatch, processes)

    result = await DockerSandbox(SandboxPolicy(output_limit_bytes=4)).execute(
        SandboxRequest(command=("python", "-V"))
    )

    assert result.status == "output_limit_exceeded"
    assert result.exit_code is None
    assert result.output == "abcd"
    assert workload.killed
    assert cleanup.returncode == 0


@pytest.mark.asyncio
async def test_sandbox_times_out_and_cleans_up(monkeypatch: MonkeyPatch) -> None:
    workload = FakeProcess([b"later"], delay_seconds=0.01)
    cleanup = FakeProcess([])
    processes = process_iterator(workload, cleanup)
    install_fake_processes(monkeypatch, processes)

    result = await DockerSandbox(SandboxPolicy(timeout_seconds=0.001)).execute(
        SandboxRequest(command=("python", "-V"))
    )

    assert result.status == "timed_out"
    assert workload.killed
    assert cleanup.returncode == 0
