"""Commands: `run` (live sandbox) and `demo` (offline fixtures + replay)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from paypal_recon.agent import (
    Backend,
    ClaudeBackend,
    ModelError,
    RecordingBackend,
    ReplayBackend,
)
from paypal_recon.config import ConfigError, load_config
from paypal_recon.http import PayPalError, Transport
from paypal_recon.paypal import fetch_transactions, parse_transactions, window
from paypal_recon.proposals import write_outputs
from paypal_recon.reconcile import ExpectedError, load_expected, reconcile

FIXTURES = resources.files("paypal_recon") / "fixtures"


def _when(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paypal-recon", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="reconcile the PayPal SANDBOX against an expected-payments CSV")
    r.add_argument("--expected", type=Path, required=True)
    r.add_argument("--env-file", type=Path)
    r.add_argument("--since", type=_when)
    r.add_argument("--until", type=_when)
    r.add_argument("--model", choices=("claude", "replay"), default="claude")
    r.add_argument("--out", type=Path, default=Path("out"))
    r.add_argument("--max-drafts", type=int, default=10)
    d = sub.add_parser("demo", help="offline demo on bundled fixtures; needs no credentials")
    d.add_argument("--model", choices=("claude", "replay"), default="replay")
    d.add_argument("--out", type=Path, default=Path("out/demo"))
    d.add_argument("--record-replay", type=Path, help="write validated model replies here")
    d.add_argument("--max-drafts", type=int, default=10)
    return p


def _backend(name: str) -> Backend:
    if name == "claude":
        return ClaudeBackend()
    return ReplayBackend.load(Path(str(FIXTURES / "replay.json")))


def _finish(result, backend, out, max_drafts, banner) -> None:
    proposals = write_outputs(result, backend, out, max_drafts, banner)
    print(f"matched: {len(result.matched)}")
    print(f"discrepancies: {len(result.discrepancies)}")
    print(f"proposals written: {len(proposals)} -> {out}/proposals (status pending_human_approval)")
    print(f"report: {out}/report.md")
    print("Nothing was sent to PayPal. Drafts only.")


def cmd_demo(a: argparse.Namespace) -> int:
    page = json.loads((FIXTURES / "transactions.json").read_text(encoding="utf-8"))
    with resources.as_file(FIXTURES / "expected.csv") as exp:
        expected = load_expected(exp)
    result = reconcile(expected, parse_transactions(page))
    backend = _backend(a.model)
    if a.record_replay:
        backend = RecordingBackend(backend)
    _finish(result, backend, a.out, a.max_drafts, "Source: bundled offline fixtures (no network).")
    if a.record_replay:
        a.record_replay.write_text(json.dumps(backend.recorded, indent=2, sort_keys=True) + "\n")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    cfg = load_config(a.env_file)
    start, end = window(a.since, a.until)
    expected = load_expected(a.expected)
    transport = Transport()
    try:
        token = transport.get_token(cfg.client_id, cfg.client_secret)
        txns = fetch_transactions(transport, token, start, end)
    finally:
        for method, path, status in transport.calls:
            print(f"paypal sandbox: {method} {path} -> {status}")
    print(f"transactions fetched: {len(txns)}")
    result = reconcile(expected, txns)
    _finish(result, _backend(a.model), a.out, a.max_drafts, "Source: PayPal sandbox (live).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return cmd_run(args) if args.cmd == "run" else cmd_demo(args)
    except (ConfigError, PayPalError, ExpectedError, ModelError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
