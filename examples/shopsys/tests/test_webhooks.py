from webhooks.stripe import on_charge_dispute_closed


def test_retries_failed_refunds():
    event = {"failed_refunds": [{"charge_id": "ch_2", "amount_cents": 250}]}
    assert on_charge_dispute_closed(event) == 1
