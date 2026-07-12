"""Local-only entry point for a constrained sandbox demonstration."""

import asyncio

from agents_should_survive_failure.sandbox import DockerSandbox, SandboxRequest


def main() -> None:
    result = asyncio.run(
        DockerSandbox().execute(
            SandboxRequest(command=("python", "-c", "print('sandbox demonstration completed')"))
        )
    )
    print(result.output, end="")
    if result.status != "succeeded":
        raise SystemExit(f"sandbox demo failed: {result.status}")


if __name__ == "__main__":
    main()
