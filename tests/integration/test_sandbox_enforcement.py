import pytest

from agents_should_survive_failure.sandbox import DockerSandbox, SandboxPolicy, SandboxRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sandbox_denies_network_egress() -> None:
    result = await DockerSandbox(SandboxPolicy(timeout_seconds=10)).execute(
        SandboxRequest(
            command=(
                "python",
                "-c",
                "import socket; socket.create_connection(('1.1.1.1', 53), 1)",
            )
        )
    )

    assert result.status == "failed"
    assert result.exit_code != 0
    assert "Network is unreachable" in result.output


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sandbox_runs_nonroot_with_only_its_workspace_writable() -> None:
    result = await DockerSandbox(SandboxPolicy(timeout_seconds=10)).execute(
        SandboxRequest(
            command=(
                "python",
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "Path('/workspace/proof.txt').write_text('bounded'); "
                    "print(f'uid={os.getuid()}'); "
                    "print(f'workspace={Path(\"/workspace/proof.txt\").read_text()}'); "
                    "\ntry:\n"
                    " Path('/root-filesystem-write').write_text('blocked')\n"
                    "except OSError:\n"
                    " print('root_filesystem=read_only')"
                ),
            )
        )
    )

    assert result.status == "succeeded"
    assert result.output.splitlines() == [
        "uid=65532",
        "workspace=bounded",
        "root_filesystem=read_only",
    ]
