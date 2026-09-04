"""Thin wrapper around the (imaginary) payment provider API."""


class GatewayError(Exception):
    pass


def reverse_charge(charge_id: str, amount_cents: int) -> str:
    if amount_cents <= 0:
        raise GatewayError("amount must be positive")
    return f"rev_{charge_id}_{amount_cents}"
