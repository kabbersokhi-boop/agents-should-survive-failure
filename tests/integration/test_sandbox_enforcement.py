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
