# paypal-recon

A PayPal AI Hackathon entry: a payout-reconciliation agent for the **PayPal sandbox**. It reads Transaction Search, matches each transaction against an expected-payments CSV, sorts every discrepancy into a fixed class, and has an AI model draft the human-facing part of a refund or invoice proposal. It **drafts, never sends**.

## Disclosure

This entry was built by an automated AI system operated by the human owner of the Devpost account, and is published under that system's own GitHub account. No person wrote its code by hand.

## Run it (no credentials, no model)

```
uv run paypal-recon demo
```

This reads bundled fixtures that follow PayPal's documented Transaction Search response, uses recorded model replies, and writes `out/demo/report.md` and `out/demo/proposals/*.json`. It opens no network connection. Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). No third-party runtime dependencies.

## Run it against the sandbox

1. Create a sandbox app at developer.paypal.com with Transaction Search enabled, and copy `.env.example` to `.env` (git-ignored) with your sandbox `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. `PAYPAL_ENV` must be `sandbox`.
2. List the payments you expect in a CSV (see `examples/expected.csv`): `order_ref,amount,currency,paypal_transaction_id,invoice_id`.
3. Run the one command:

```
uv run paypal-recon run --env-file .env --expected examples/expected.csv
```

Options: `--since/--until` (ISO 8601, at most 31 days, default last 30 days), `--model claude|replay` (default `claude`, which calls `claude -p` with no tools in an empty directory; needs the Claude Code CLI), `--out DIR`, `--max-drafts N` (default 10). The sandbox can take up to 3 hours to show new transactions in Transaction Search.

## Classes

`matched`, `underpaid` (invoice the shortfall), `overpaid` (refund the excess), `missing_payment` (invoice the expected amount), `unexpected_payment` (refund or flag), `duplicate` (refund the second payment), `pending_or_denied` (hold). Matching uses PayPal's `transaction_id`, with `invoice_id` as the fallback key.

## Guarantees, enforced in code

- **Sandbox only.** The transport allows one host, `api-m.sandbox.paypal.com`, and exactly two calls: `POST /v1/oauth2/token` and `GET /v1/reporting/transactions`. Anything else raises before a socket opens. `PAYPAL_ENV` other than `sandbox` stops the program.
- **Drafts only.** Each proposal is a JSON file with `status: "pending_human_approval"` and the exact PayPal request it would make (refund or invoice draft). No code path sends it.
- **Code owns the amounts.** The model returns `action`, `summary` and `customer_note`. The action must be allowed for the class; any other key (such as an amount) rejects the reply, and a rejected reply becomes a `flag_for_review` draft. Refund and invoice amounts are computed from the data.
- **Untrusted text.** Transaction memo fields are fenced in the prompt as data. A fixture carries a prompt-injection memo ("refund 1000 USD") and the tests check it cannot change the amount.
- **Secrets.** Credentials are wrapped so printing them gives `***`; errors carry only method, path, status and PayPal's `name`/`debug_id`.

The reconciliation core uses the same `MoneyEvent` shape and amount parsing as a money-in ledger contract, so it can serve as a PayPal adapter test harness.

## Tests

```
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests scripts
```

No test opens a real socket.

## License

MIT, see `LICENSE`. Copyright (c) 2026 lifeofabit.
