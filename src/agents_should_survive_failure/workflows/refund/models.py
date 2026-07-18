"""Domain contracts for the high-value refund example."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RefundRequest:
    refund_id: str
    order_id: str
    amount: Decimal
    reason: str
    customer_id: str


@dataclass(frozen=True)
class RefundEvidence:
    order_status: str
    order_total: Decimal
    policy: str
    citations: tuple[str, ...]


@dataclass(frozen=True)
class RefundDecision:
    risk_score: int
    explanation: str
    approved: bool
