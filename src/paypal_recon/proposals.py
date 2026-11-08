"""Writes proposals and the report. There is deliberately no function here that sends anything."""

from __future__ import annotations

import json
import re
from pathlib import Path

from paypal_recon.agent import Backend, Reply, draft
from paypal_recon.model import format_amount_minor
from paypal_recon.reconcile import Discrepancy, Result

DISCLOSURE = (
    "This entry was built by an automated AI system operated by the human owner of the "
    "Devpost account, and is published under that system's own GitHub account. "
    "No person wrote its code by hand."
)
NOTE_MAX = 255


def build_request(d: Discrepancy, reply: Reply) -> dict | None:
    """The exact PayPal request a human could send. Amounts come from `d`, never from `reply`."""
    if not d.amount_minor:
        return None
    value = format_amount_minor(d.amount_minor, d.currency)
    money = {"currency_code": d.currency, "value": value}
    note = reply.customer_note[:NOTE_MAX]
    if reply.action == "refund" and d.txn is not None:
        body: dict = {"amount": money, "note_to_payer": note}
        if d.txn.invoice_id:
            body["invoice_id"] = d.txn.invoice_id
        return {
            "method": "POST",
            "path": f"/v2/payments/captures/{d.txn.event.external_id}/refund",
            "body": body,
        }
    if reply.action == "invoice":
        ref = d.order_ref or "UNKNOWN"
        label = "Shortfall" if d.klass == "underpaid" else "Payment due"
        return {
            "method": "POST",
            "path": "/v2/invoicing/invoices",
            "body": {
                "detail": {
                    "reference": ref,
                    "currency_code": d.currency,
                    "note": note,
                },
                "items": [
                    {
                        "name": f"{label} for {ref}",
                        "quantity": "1",
                        "unit_amount": money,
                    }
                ],
            },
        }
    return None


def make_proposal(d: Discrepancy, reply: Reply, model: str, model_error: str | None) -> dict:
    return {
        "status": "pending_human_approval",
        "discrepancy_class": d.klass,
        "order_ref": d.order_ref,
        "transaction_id": d.txn.event.external_id if d.txn else None,
        "currency": d.currency,
        "computed_amount": (
            format_amount_minor(d.amount_minor, d.currency) if d.amount_minor else None
        ),
        "detail": d.detail,
        "action": reply.action,
        "summary": reply.summary,
        "customer_note": reply.customer_note,
        "paypal_request": build_request(d, reply),
        "drafted_by": model,
        "model_error": model_error,
        "note": "Draft only. Nothing has been sent; a human must approve and send it.",
    }


def write_outputs(
    result: Result, backend: Backend, out_dir: Path, max_drafts: int = 10, banner: str = ""
) -> list[dict]:
    pdir = Path(out_dir) / "proposals"
    pdir.mkdir(parents=True, exist_ok=True)
    for old in pdir.glob("*.json"):
        old.unlink()
    proposals: list[dict] = []
    for n, d in enumerate(result.discrepancies[:max_drafts], start=1):
        reply, err = draft(d, backend)
        p = make_proposal(d, reply, backend.name, err)
        slug = re.sub(r"[^a-z_]", "", d.klass)
        (pdir / f"{n:03d}-{slug}.json").write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")
        proposals.append(p)
    skipped = len(result.discrepancies) - len(proposals)
    lines = [
        "# Reconciliation report",
        "",
        banner,
        "",
        f"- matched: {len(result.matched)}",
        f"- discrepancies: {len(result.discrepancies)} "
        f"({len(proposals)} drafted, {skipped} over --max-drafts)",
        "",
        "All proposals below are drafts with status `pending_human_approval`. Nothing was sent.",
        "",
        "| # | class | order | txn | amount | action |",
        "|---|-------|-------|-----|--------|--------|",
    ]
    for n, p in enumerate(proposals, start=1):
        amt = f"{p['computed_amount']} {p['currency']}" if p["computed_amount"] else "-"
        lines.append(
            f"| {n} | {p['discrepancy_class']} | {p['order_ref'] or '-'} "
            f"| {p['transaction_id'] or '-'} | {amt} | {p['action']} |"
        )
    lines += ["", "## Disclosure", "", DISCLOSURE, ""]
    (Path(out_dir) / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return proposals
