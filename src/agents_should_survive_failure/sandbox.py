"""A deliberately narrow, host-operated Docker sandbox broker.

The broker is a local operator capability, not an API route. Docker is not a complete hostile-code
boundary; see ``docs/security/sandbox.md`` for the explicit limits and production alternatives.
"""

import asyncio
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: tuple[str, ...] = Field(min_length=1, max_length=32)
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def command_parts_are_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part or len(part) > 4_096 or "\x00" in part for part in value):
            raise ValueError("sandbox command contains an invalid argument")
        return value

    @field_validator("environment")
    @classmethod
    def environment_values_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32 or any(
            not key or len(key) > 128 or len(item) > 4_096 or "\x00" in item
            for key, item in value.items()
        ):
            raise ValueError("sandbox environment exceeds the allowed bounds")
        return value


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = "agents-control-plane:local"
    cpu_limit: float = 0.5
    memory_limit: str = "256m"
    process_limit: int = 64
    timeout_seconds: float = 30.0
    output_limit_bytes: int = 64_000
    temporary_space: str = "32m"
    environment_allowlist: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SandboxResult:
    status: str
    exit_code: int | None
    output: str


class SandboxPolicyError(PermissionError):
    pass


class DockerSandbox:
    """Execute one bounded command in a disposable container."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()

    def build_command(
        self,
        request: SandboxRequest,
        *,
        workspace: Path,
        container_name: str,
    ) -> list[str]:
        denied = set(request.environment).difference(self._policy.environment_allowlist)
        if denied:
            raise SandboxPolicyError("sandbox environment contains names not permitted by policy")
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._policy.process_limit),
            "--cpus",
            str(self._policy.cpu_limit),
            "--memory",
            self._policy.memory_limit,
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={self._policy.temporary_space}",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
        ]
        for key, value in sorted(request.environment.items()):
            command.extend(["--env", f"{key}={value}"])
        command.extend([self._policy.image, *request.command])
        return command

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        name = f"survive-sandbox-{secrets.token_hex(8)}"
        with tempfile.TemporaryDirectory(prefix="survive-sandbox-") as directory:
            workspace = Path(directory)
            command = self.build_command(request, workspace=workspace, container_name=name)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={"PATH": os.environ.get("PATH", "")},
            )
            try:
                output, limit_exceeded = await self._capture_output(process)
                if limit_exceeded:
                    return SandboxResult("output_limit_exceeded", None, output)
                return SandboxResult(
                    "succeeded" if process.returncode == 0 else "failed",
                    process.returncode,
                    output,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return SandboxResult("timed_out", None, "sandbox execution timed out")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                # The workload never receives a Docker socket; the broker owns best-effort cleanup.
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "--force",
                    name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()

    async def _capture_output(self, process: asyncio.subprocess.Process) -> tuple[str, bool]:
        stdout = process.stdout
        assert stdout is not None

        async def capture() -> tuple[str, bool]:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await stdout.read(8_192)
                if not chunk:
                    break
                available = self._policy.output_limit_bytes - total
                chunks.append(chunk[: max(available, 0)])
                total += len(chunk)
                if total > self._policy.output_limit_bytes:
                    process.kill()
                    await process.wait()
                    return b"".join(chunks).decode("utf-8", errors="replace"), True
            await process.wait()
            return b"".join(chunks).decode("utf-8", errors="replace"), False

        async with asyncio.timeout(self._policy.timeout_seconds):
            return await capture()
