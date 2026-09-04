from api.refunds import handle_refund_request


def test_refund_ok():
    status, body = handle_refund_request({"charge_id": "ch_1", "amount_cents": 500})
    assert status == 200
    assert body["status"] == "refunded"


def test_refund_rejected():
    status, body = handle_refund_request({"charge_id": "ch_1", "amount_cents": 0})
    assert status == 422
    assert "error" in body
