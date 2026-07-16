"""A bounded operations investigation agent implemented only with the public SDK."""

from __future__ import annotations

import json
from typing import cast

from agents_should_survive_failure_sdk import (
    AgentArtifact,
    AgentMetadata,
    AgentResult,
    AgentTask,
    Capability,
    RunContext,
    ToolDeclaration,
)


class OperationsInvestigationAgent:
    """Collect governed policy evidence and write a durable investigation artifact."""

    metadata = AgentMetadata(
        slug="operations-investigation",
        version="1.0.0",
        display_name="Operations Investigation",
        description=(
            "Investigates a bounded operational incident through governed evidence retrieval."
        ),
        input_schema={
            "type": "object",
            "required": ["incident_id", "question"],
            "properties": {
                "incident_id": {"type": "string"},
                "question": {"type": "string"},
                "requires_approval": {"type": "boolean"},
            },
        },
        output_schema={
            "type": "object",
            "required": ["incident_id", "approved_follow_up"],
            "properties": {
                "incident_id": {"type": "string"},
                "approved_follow_up": {"type": "boolean"},
            },
        },
        required_capabilities=(Capability.TOOLS, Capability.CHECKPOINTS, Capability.ARTIFACTS),
        tools=(ToolDeclaration(name="internal_policy_search", version="1"),),
        checkpoint_supported=True,
        artifact_supported=True,
    )

    async def run(self, task: AgentTask, context: RunContext) -> AgentResult:
        incident_id = task.input.get("incident_id")
        question = task.input.get("question")
        if not isinstance(incident_id, str) or not incident_id:
            raise ValueError("incident_id must be a non-empty string")
        if not isinstance(question, str) or not question:
            raise ValueError("question must be a non-empty string")

        await context.check_cancelled()
        await context.emit_event(
            "operations_investigation.started",
            "Started bounded operations investigation.",
            {"incident_id": incident_id},
        )
        await context.save_checkpoint(
            "investigation-input",
            "1",
            {"incident_id": incident_id, "question": question},
        )
        evidence = await context.call_tool(
            "internal_policy_search",
            {"query": question},
            idempotency_key=f"operations-investigation:{incident_id}:policy-search",
        )
        await context.check_cancelled()
        report = {
            "incident_id": incident_id,
            "question": question,
            "evidence": dict(evidence),
            "approved_follow_up": False,
        }
        artifact = AgentArtifact(
            name=f"investigation-{incident_id}.json",
            content_type="application/json",
            content=json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        await context.emit_event(
            "operations_investigation.completed",
            "Created operations investigation artifact.",
            {"incident_id": incident_id},
        )
        return AgentResult(
            output=cast(dict[str, object], report),
            summary="Operations investigation completed.",
            artifacts=(artifact,),
        )
