"""HTTP-facing refund endpoint (framework omitted for the example)."""

from payments.refund import RefundError, refund_payment


def handle_refund_request(payload: dict) -> tuple[int, dict]:
    try:
        record = refund_payment(payload["charge_id"], payload["amount_cents"])
    except RefundError as exc:
        return 422, {"error": str(exc)}
    return 200, record
