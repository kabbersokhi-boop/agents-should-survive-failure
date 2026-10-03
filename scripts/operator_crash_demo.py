"""Local-only recording harness; Docker authority never enters the browser/API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy.engine import make_url

from agents_should_survive_failure.auth_cli import bootstrap
from agents_should_survive_failure.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]


def local_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("the demonstration requires a loopback HTTP instance")
    return value.rstrip("/")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement prevents a recording reader from seeing partial JSON.
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
    temporary.replace(path)


async def session(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.app_env != "development":
        raise ValueError("session provisioning is development-only")
    if make_url(settings.database_url).host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("session provisioning requires a loopback database")
    url = local_url(args.api_url)
    async with httpx.AsyncClient(base_url=url) as client:
        (await client.get("/health/ready")).raise_for_status()
    key = await bootstrap(
        "portfolio-operator@example.invalid",
        "Synthetic Portfolio Operator",
        [
            "runs:read",
            "runs:write",
            "approvals:read",
            "approvals:decide",
            "evaluations:execute",
            "evaluations:read",
        ],
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    write_json(args.output, {"api_url": url, "api_key": key})
    print("Scoped development session provisioned; credential not printed.", flush=True)


def compose(project: str, *arguments: str) -> str:
    if not project.startswith("agents-portfolio") or not project.replace("-", "").isalnum():
        raise ValueError("worker control is restricted to an agents-portfolio Compose project")
    return subprocess.check_output(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "--project-directory",
            str(ROOT),
            "-f",
            str(ROOT / "docker-compose.yml"),
            *arguments,
        ],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def inspected_worker(project: str) -> tuple[str, dict[str, Any]]:
    container = compose(project, "ps", "--all", "-q", "worker")
    if not container or "\n" in container:
        raise ValueError("expected exactly one isolated worker")
    inspected = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    labels = inspected["Config"]["Labels"]
    if (
        labels["com.docker.compose.project"] != project
        or labels["com.docker.compose.service"] != "worker"
    ):
        raise ValueError("refusing to control an unrelated worker")
    if inspected["Config"]["Env"] and "APP_ENV=development" not in inspected["Config"]["Env"]:
        raise ValueError("refusing to terminate a non-development worker")
    return container, inspected


def worker_pid(project: str) -> int:
    return int(inspected_worker(project)[1]["State"]["Pid"])


def restart_worker(project: str) -> None:
    """Restart the inspected container without reconciling any Compose configuration."""
    container, _ = inspected_worker(project)
    subprocess.run(["docker", "start", container], check=True, stdout=subprocess.DEVNULL)


async def case(client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/workflow-runs/{run_id}/business-evidence")
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


async def arm(args: argparse.Namespace, client: httpx.AsyncClient) -> None:
    UUID(args.run_id)
    summary = await case(client, args.run_id)
    if summary["business_status"] != "waiting" or summary["effects"]["approval_decisions"]:
        raise ValueError("arm only a pending, undecided supplier case")
    response = await client.post(
        "/api/v1/fault-plans",
        json={
            "fault_point": "email.send.after_commit_before_ack",
            "action": "delay",
            "scope_key": args.run_id,
            "delay_ms": 60_000,
            "safe_metadata": {"proof": "operator-console-worker-crash"},
        },
    )
    response.raise_for_status()
    print("Post-commit/pre-acknowledgement crash window armed for the selected case.", flush=True)


async def recover(args: argparse.Namespace, client: httpx.AsyncClient) -> None:
    UUID(args.run_id)
    _, worker = inspected_worker(args.project)
    if "FAULT_INJECTION_ENABLED=true" not in worker["Config"]["Env"]:
        raise ValueError("the isolated worker must have development fault injection enabled")
    proof: dict[str, Any] = {"run_id": args.run_id, "project": args.project, "stages": []}

    def stage(name: str, **details: object) -> None:
        proof["stages"].append({"stage": name, "at": datetime.now(UTC).isoformat(), **details})
        write_json(args.output, proof)
        print(name.replace("_", " "), flush=True)

    stage("waiting_for_operator_approval")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        before = await case(client, args.run_id)
        faults = (
            (await client.get("/api/v1/fault-plans", params={"scope_key": args.run_id}))
            .raise_for_status()
            .json()
        )
        consumed = any(
            p["scope_key"] == args.run_id
            and p["fault_point"] == "email.send.after_commit_before_ack"
            and p["remaining_triggers"] == 0
            for p in faults
        )
        if (
            consumed
            and list(before["effects"].values()) == [1, 1, 1]
            and before["temporal_status"] == "RUNNING"
        ):
            break
        await asyncio.sleep(0.25)
    else:
        raise TimeoutError("no verified post-commit crash window appeared")
    pid = await asyncio.to_thread(worker_pid, args.project)
    if pid <= 0:
        raise ValueError("the isolated worker is not running")
    stage("effects_committed_before_ack", evidence=before)
    await asyncio.to_thread(compose, args.project, "kill", "-s", "KILL", "worker")
    inspected = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            ["docker", "inspect", "--format", "{{json .State}}", f"{args.project}-worker-1"],
            text=True,
        )
    )
    if inspected["Running"] or inspected["ExitCode"] != 137:
        raise AssertionError("worker termination was not observed")
    stage("worker_sigkill_observed", exit_code=inspected["ExitCode"], prior_pid=pid)
    try:
        await asyncio.sleep(args.hold_seconds)
    finally:
        # Restore only the isolated worker even when the observation hold is interrupted.
        await asyncio.to_thread(restart_worker, args.project)
    replacement = await asyncio.to_thread(worker_pid, args.project)
    if replacement <= 0 or replacement == pid:
        raise AssertionError("a replacement worker process was not observed")
    stage("replacement_worker_started", replacement_pid=replacement)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        after = await case(client, args.run_id)
        if after["temporal_status"] == "COMPLETED":
            break
        await asyncio.sleep(0.5)
    else:
        raise TimeoutError("Temporal did not complete after replacement")
    if (
        list(after["effects"].values()) != [1, 1, 1]
        or (after["decision_activity_attempt"] or 0) < 2
    ):
        raise AssertionError("redelivery and one-of-each business effects were not verified")
    stage("recovery_verified", evidence=after)
    proof["verified"] = True
    write_json(args.output, proof)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    provision = sub.add_parser("session")
    provision.add_argument("--api-url", default="http://127.0.0.1:18100")
    provision.add_argument("--output", type=Path, default=ROOT / "artifacts/operator-session.json")
    for name in ("arm", "recover"):
        command = sub.add_parser(name)
        command.add_argument("--run-id", required=True)
        command.add_argument(
            "--session", type=Path, default=ROOT / "artifacts/operator-session.json"
        )
        command.add_argument("--project", default="agents-portfolio")
        command.add_argument(
            "--output", type=Path, default=ROOT / "artifacts/operator-crash-proof.json"
        )
        command.add_argument("--hold-seconds", type=float, default=8)
    args = parser.parse_args()
    if args.command == "session":
        await session(args)
        return
    if not 0 <= args.hold_seconds <= 20:
        raise ValueError("observation hold must be between zero and twenty seconds")
    credentials = json.loads(args.session.read_text())
    async with httpx.AsyncClient(
        base_url=local_url(credentials["api_url"]),
        timeout=15,
        headers={"Authorization": "Bearer " + credentials["api_key"]},
    ) as client:
        if args.command == "arm":
            await arm(args, client)
        else:
            await recover(args, client)


if __name__ == "__main__":
    asyncio.run(main())
