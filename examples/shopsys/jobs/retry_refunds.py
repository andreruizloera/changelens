"""Background job that retries refunds that previously failed."""

from payments import refund


def retry_failed_refunds(failed: list[dict]) -> list[dict]:
    succeeded = []
    for item in failed:
        try:
            record = refund.refund_payment(item["charge_id"], item["amount_cents"])
        except refund.RefundError:
            continue
        succeeded.append(record)
    return succeeded
