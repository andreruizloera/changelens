"""Inbound webhook handler that kicks off refund retries."""

from jobs.retry_refunds import retry_failed_refunds


def on_charge_dispute_closed(event: dict) -> int:
    failed = event.get("failed_refunds", [])
    return len(retry_failed_refunds(failed))
