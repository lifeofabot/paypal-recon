import json
from importlib import resources
from pathlib import Path

import pytest

from paypal_recon.paypal import parse_transactions
from paypal_recon.reconcile import ExpectedError, load_expected, reconcile

FIX = resources.files("paypal_recon") / "fixtures"


def _result():
    page = json.loads((FIX / "transactions.json").read_text())
    with resources.as_file(FIX / "expected.csv") as p:
        return reconcile(load_expected(p), parse_transactions(page))


def _by_class():
    return {d.klass: d for d in _result().discrepancies}


def test_every_class_present():
    r = _result()
    assert len(r.matched) == 2
    assert set(_by_class()) == {
        "underpaid", "overpaid", "missing_payment", "unexpected_payment",
        "duplicate", "pending_or_denied",
    }  # fmt: skip


def test_amounts_are_computed_in_code():
    c = _by_class()
    assert c["underpaid"].amount_minor == 750  # expected 50.00, got 42.50
    assert c["overpaid"].amount_minor == 500  # expected 20.00, got 25.00
    assert c["missing_payment"].amount_minor == 7500
    assert c["duplicate"].amount_minor == 3000
    assert c["unexpected_payment"].amount_minor == 1500
    assert c["pending_or_denied"].amount_minor is None


def test_matching_keys():
    c = _by_class()
    # underpaid row has no transaction id: matched through invoice_id
    assert c["underpaid"].txn.event.external_id == "6QX11822HB410934K"
    assert c["duplicate"].txn.event.external_id == "1AE66314PS889572J"
    assert c["unexpected_payment"].order_ref is None


def test_allowed_actions_per_class():
    c = _by_class()
    assert c["overpaid"].allowed_actions == ("refund", "flag_for_review")
    assert c["underpaid"].allowed_actions == ("invoice", "flag_for_review")
    assert "refund" not in c["underpaid"].allowed_actions
    assert "refund" not in c["pending_or_denied"].allowed_actions


def test_outgoing_refund_rows_are_not_payments():
    ids = {d.txn.event.external_id for d in _result().discrepancies if d.txn}
    assert "3HJ88536RU001794N" not in ids


def test_currency_mismatch_only_flags(tmp_path: Path):
    page = json.loads((FIX / "transactions.json").read_text())
    for item in page["transaction_details"]:
        if item["transaction_info"]["transaction_id"] == "5TY05013RG002845M":
            item["transaction_info"]["transaction_amount"]["currency_code"] = "EUR"
    with resources.as_file(FIX / "expected.csv") as p:
        r = reconcile(load_expected(p), parse_transactions(page))
    d = next(x for x in r.discrepancies if x.order_ref == "ORD-1001")
    assert d.allowed_actions == ("flag_for_review",) and d.amount_minor is None


def test_expected_csv_validation(tmp_path: Path):
    bad = tmp_path / "e.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ExpectedError):
        load_expected(bad)
    bad.write_text("order_ref,amount,currency,paypal_transaction_id,invoice_id\nA,1.234,USD,,\n")
    with pytest.raises(ExpectedError):
        load_expected(bad)


@pytest.mark.parametrize("bad", ["D", "P"])
def test_completed_repay_after_failed_primary_is_not_a_duplicate(bad):
    from paypal_recon.model import MoneyEvent  # noqa: F401
    from paypal_recon.reconcile import Expected

    page = json.loads((FIX / "transactions.json").read_text())
    base = parse_transactions(page)[0]
    import dataclasses

    def mk(tid, status, ts):
        ev = dataclasses.replace(
            base.event, external_id=tid, ts=ts, amount_minor=1000, currency="USD"
        )
        return dataclasses.replace(base, event=ev, status=status, invoice_id="INV-1")

    r = reconcile([Expected("O1", 1000, "USD", "A", "INV-1")], [mk("A", bad, 1), mk("B", "S", 2)])
    assert [t.event.external_id for _, t in r.matched] == ["B"]
    assert not any(d.klass == "duplicate" for d in r.discrepancies)
    assert not any(
        "refund" in d.allowed_actions and d.txn.event.external_id == "B" for d in r.discrepancies
    )
    assert [d.klass for d in r.discrepancies] == ["pending_or_denied"]
