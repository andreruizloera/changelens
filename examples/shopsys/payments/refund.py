"""Refund workflow built on top of the payment gateway."""

from payments.gateway import GatewayError, reverse_charge


class RefundError(Exception):
    pass


def refund_payment(charge_id: str, amount_cents: int) -> dict:
    """Reverse a charge and return a refund record."""
    try:
        reversal_id = reverse_charge(charge_id, amount_cents)
    except GatewayError as exc:
        raise RefundError(f"gateway rejected refund: {exc}") from exc
    return {
        "charge_id": charge_id,
        "reversal_id": reversal_id,
        "amount_cents": amount_cents,
        "status": "refunded",
    }
