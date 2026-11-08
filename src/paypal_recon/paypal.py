"""Transaction Search (GET /v1/reporting/transactions) responses mapped to MoneyEvents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from paypal_recon.config import Secret
from paypal_recon.http import Transport
from paypal_recon.model import MoneyEvent, parse_amount_minor, parse_iso_utc

SOURCE = "paypal"
MAX_WINDOW = timedelta(days=31)
PAGE_SIZE = 100
MAX_PAGES = 50


@dataclass(frozen=True)
class Txn:
    event: MoneyEvent
    event_code: str
    status: str
    invoice_id: str | None
    reference_id: str | None
    # Free text written by the payer or a third party: untrusted.
    subject: str | None = None
    note: str | None = None
    custom_field: str | None = None


def _opt(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


def parse_transactions(page: dict[str, Any]) -> list[Txn]:
    out: list[Txn] = []
    for item in page.get("transaction_details") or []:
        info = item.get("transaction_info") or {}
        payer = item.get("payer_info") or {}
        amount = info.get("transaction_amount") or {}
        currency = str(amount.get("currency_code") or "")
        note, subject = _opt(info, "transaction_note"), _opt(info, "transaction_subject")
        event = MoneyEvent(
            source=SOURCE,
            external_id=str(info.get("transaction_id") or ""),
            amount_minor=parse_amount_minor(str(amount.get("value") or ""), currency),
            currency=currency,
            ts=parse_iso_utc(str(info.get("transaction_initiation_date") or "")),
            counterparty=_opt(payer, "account_id"),
            memo=note or subject,
        )
        out.append(
            Txn(
                event=event,
                event_code=str(info.get("transaction_event_code") or ""),
                status=str(info.get("transaction_status") or ""),
                invoice_id=_opt(info, "invoice_id"),
                reference_id=_opt(info, "paypal_reference_id"),
                subject=subject,
                note=note,
                custom_field=_opt(info, "custom_field"),
            )
        )
    return out


def window(since: datetime | None, until: datetime | None, now: datetime | None = None):
    now = now or datetime.now(UTC)
    end = until or now
    start = since or end - timedelta(days=30)
    if start >= end:
        raise ValueError("--since must be before --until")
    if end - start > MAX_WINDOW:
        raise ValueError("the window may be at most 31 days (PayPal's Transaction Search limit)")
    return start, end


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_transactions(
    transport: Transport, token: Secret, start: datetime, end: datetime
) -> list[Txn]:
    txns: list[Txn] = []
    page_no = 1
    while page_no <= MAX_PAGES:
        data = transport.search(
            token,
            {
                "start_date": _fmt(start),
                "end_date": _fmt(end),
                "fields": "transaction_info,payer_info",
                "page_size": str(PAGE_SIZE),
                "page": str(page_no),
            },
        )
        txns.extend(parse_transactions(data))
        total_pages = data.get("total_pages")
        if not isinstance(total_pages, int) or page_no >= total_pages:
            break
        page_no += 1
    return txns
