"""Deterministic classifier. Every amount is computed here, in minor units."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from paypal_recon.model import is_currency_code, parse_amount_minor
from paypal_recon.paypal import Txn

CLASSES = (
    "matched",
    "underpaid",
    "overpaid",
    "missing_payment",
    "unexpected_payment",
    "duplicate",
    "pending_or_denied",
)
ALLOWED_ACTIONS: dict[str, tuple[str, ...]] = {
    "matched": (),
    "underpaid": ("invoice", "flag_for_review"),
    "overpaid": ("refund", "flag_for_review"),
    "missing_payment": ("invoice", "flag_for_review"),
    "unexpected_payment": ("refund", "flag_for_review"),
    "duplicate": ("refund", "flag_for_review"),
    "pending_or_denied": ("hold", "flag_for_review"),
}
COLUMNS = ("order_ref", "amount", "currency", "paypal_transaction_id", "invoice_id")


class ExpectedError(ValueError):
    pass


@dataclass(frozen=True)
class Expected:
    order_ref: str
    amount_minor: int
    currency: str
    transaction_id: str | None
    invoice_id: str | None


@dataclass(frozen=True)
class Discrepancy:
    klass: str
    order_ref: str | None
    txn: Txn | None
    expected: Expected | None
    currency: str
    amount_minor: int | None  # the one amount a refund or invoice would carry
    allowed_actions: tuple[str, ...]
    detail: str


def load_expected(path: Path) -> list[Expected]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ExpectedError(f"expected CSV columns must be exactly: {','.join(COLUMNS)}")
        rows: list[Expected] = []
        seen: set[str] = set()
        for n, row in enumerate(reader, start=2):
            ref = (row["order_ref"] or "").strip()
            cur = (row["currency"] or "").strip()
            if not ref or ref in seen:
                raise ExpectedError(f"line {n}: order_ref empty or repeated")
            if not is_currency_code(cur):
                raise ExpectedError(f"line {n}: currency is not an upper-case ISO code")
            try:
                minor = parse_amount_minor(row["amount"] or "", cur)
            except ValueError as exc:
                raise ExpectedError(f"line {n}: {exc}") from None
            if minor <= 0:
                raise ExpectedError(f"line {n}: amount must be positive")
            seen.add(ref)
            rows.append(
                Expected(
                    ref,
                    minor,
                    cur,
                    (row["paypal_transaction_id"] or "").strip() or None,
                    (row["invoice_id"] or "").strip() or None,
                )
            )
    return rows


@dataclass(frozen=True)
class Result:
    matched: list[tuple[Expected, Txn]]
    discrepancies: list[Discrepancy]


def _d(klass, e, t, amount, detail, currency=None, actions=None) -> Discrepancy:
    cur = currency or (e.currency if e else t.event.currency)
    return Discrepancy(
        klass, e.order_ref if e else None, t, e, cur, amount,
        ALLOWED_ACTIONS[klass] if actions is None else actions, detail,
    )  # fmt: skip


def reconcile(expected: list[Expected], txns: list[Txn]) -> Result:
    payments = sorted(
        (t for t in txns if t.event.amount_minor > 0),
        key=lambda t: (t.event.ts, t.event.external_id),
    )
    by_id = {t.event.external_id: t for t in payments}
    used: set[str] = set()
    matched: list[tuple[Expected, Txn]] = []
    out: list[Discrepancy] = []

    for e in expected:
        primary = by_id.get(e.transaction_id) if e.transaction_id else None
        if primary is None and e.invoice_id:
            cands = [
                t
                for t in payments
                if t.invoice_id == e.invoice_id and t.event.external_id not in used
            ]
            cands.sort(key=lambda t: t.status != "S")
            primary = cands[0] if cands else None
        if primary is None or primary.event.external_id in used:
            out.append(_d("missing_payment", e, None, e.amount_minor, "no matching transaction"))
            continue
        key = e.invoice_id or primary.invoice_id
        if primary.status != "S" and key:
            # The named payment did not clear. If the payer paid again and that
            # payment completed, it is the order's payment; the failed one is
            # reported on its own by the sweep below, never as the order's primary.
            done = [
                t
                for t in payments
                if t.invoice_id == key
                and t.event.external_id not in used
                and t.event.external_id != primary.event.external_id
                and t.status == "S"
            ]
            if done:
                primary = done[0]
        used.add(primary.event.external_id)
        if primary.status == "S" and key:
            for t in payments:
                if t.invoice_id == key and t.event.external_id not in used and t.status == "S":
                    used.add(t.event.external_id)
                    out.append(
                        _d("duplicate", e, t, t.event.amount_minor, f"second payment for {key}")
                    )
        got, cur = primary.event.amount_minor, primary.event.currency
        if primary.status != "S":
            out.append(_d("pending_or_denied", e, primary, None, f"status {primary.status or '?'}"))
        elif cur != e.currency:
            out.append(
                _d("unexpected_payment", e, primary, None, f"currency {cur}, expected {e.currency}",
                   currency=cur, actions=("flag_for_review",))
            )  # fmt: skip
        elif got == e.amount_minor:
            matched.append((e, primary))
        elif got < e.amount_minor:
            out.append(_d("underpaid", e, primary, e.amount_minor - got, "shortfall"))
        else:
            out.append(_d("overpaid", e, primary, got - e.amount_minor, "excess"))

    for t in payments:
        if t.event.external_id in used:
            continue
        if t.status != "S":
            out.append(_d("pending_or_denied", None, t, None, f"status {t.status or '?'}"))
        else:
            out.append(_d("unexpected_payment", None, t, t.event.amount_minor, "no expected row"))
    return Result(matched, out)
