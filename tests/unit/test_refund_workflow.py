from decimal import Decimal

import pytest

from agents_should_survive_failure.workflows.contracts import RefundWorkflowInput
from agents_should_survive_failure.workflows.refund.activities import RefundActivities


@pytest.mark.asyncio
async def test_high_value_refund_risk_is_deterministic() -> None:
    activities = RefundActivities(database=None)  # type: ignore[arg-type]
    request = RefundWorkflowInput(
        "run", "refund", "order", "750.00", "duplicate charge", "customer"
    )
    result = await activities.calculate_refund_risk(
        request, {"status": "delivered"}, {"citations": []}
    )
    assert result == {"score": 60, "summary": "Deterministic refund risk score: 60."}


def test_refund_amount_contract_uses_decimal() -> None:
    assert Decimal("750.00") >= Decimal("500")
