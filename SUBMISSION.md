# Devpost submission text

Paste each section into the matching Devpost field.

## Project name

paypal-recon

## Tagline

A PayPal sandbox payout-reconciliation agent: it matches transactions to what you expected, sorts every discrepancy, and drafts refunds and invoices for a human to approve. It never sends.

## Who it's for

Small merchants and marketplaces that take PayPal payments still match those payments to their orders by hand, in a spreadsheet. Underpayments, overpayments, duplicates and payments with no order are easy to miss, and each one is either lost revenue or a customer owed money. paypal-recon finds every one and drafts the fix. It suits a team that wants an AI to do the matching and the writing but cannot let a model move money: nothing it produces is sent until a person approves it.

## The idea

The model writes the words; code owns the amounts. Every refund and invoice amount in a proposal's PayPal request is computed from PayPal's own data. The model returns exactly three strings: an action from a fixed list for that discrepancy class, a summary and a customer note. Any other key, such as an amount field, rejects the reply. A payer's memo that says "refund 1000 USD" cannot change the amount in the request, and a person reads every draft before anything is sent.

## What it does

paypal-recon reads the PayPal sandbox Transaction Search API and matches each transaction against an expected-payments CSV. It uses PayPal's transaction_id, with invoice_id as the fallback key. Every discrepancy lands in a fixed class: underpaid, overpaid, missing payment, unexpected payment, duplicate, or pending/denied. For each one, an AI model drafts the human-facing part of a proposal: a short summary and a customer note. The result is a report plus one JSON proposal per discrepancy. Each proposal has the status `pending_human_approval` and carries the exact PayPal request (a refund or an invoice draft) that a person would approve and send.

The offline demo (`uv run paypal-recon demo`) needs no credentials and no model. It reads bundled fixtures that follow PayPal's documented Transaction Search response and uses recorded model replies. On the bundled fixtures it reports 2 matched, 6 discrepancies and 6 proposals, then prints "Nothing was sent to PayPal. Drafts only."

## How it was built

The whole entry is Python 3.12 with no third-party runtime dependencies. A reconciliation core does the matching and classification and computes every amount from the data. A small PayPal client handles OAuth 2.0 client credentials and one read call. The drafting step calls the Claude Code CLI (`claude -p`) with no tools, in an empty directory, behind a backend interface that also supports recorded replies for the offline demo. The tests use pytest and ruff, and none of them opens a real socket.

## Challenges

- Keeping a model away from money. The model returns only `action`, `summary` and `customer_note`. Any other key, such as an amount, rejects the reply, and a rejected reply becomes a `flag_for_review` draft.
- Untrusted text. Payers control memo fields. They are fenced in the prompt as data, and a fixture carrying a "refund 1000 USD" memo is tested to confirm it cannot change the amount.
- Making "sandbox only" a property of the code rather than a promise. The transport allows one host and two calls and raises before a socket opens for anything else.

## Disclosure

This entry was built by an automated AI system operated by the human owner of the Devpost account, and is published under that system's own GitHub account. No person wrote its code by hand.

The project is new: its first code was committed on 2026-11-03 (UTC), inside the Oct 1 - Nov 12 submission period.

## Built with

- Python 3.12
- PayPal REST API (sandbox)
- PayPal Transaction Search
- OAuth 2.0 client credentials
- Claude Sonnet, via the Claude Code CLI
- uv
- pytest
- ruff

## Guarantees

- **Sandbox only.** The transport allows one host, `api-m.sandbox.paypal.com`, and exactly two calls: `POST /v1/oauth2/token` and `GET /v1/reporting/transactions`. Anything else raises before a socket opens. `PAYPAL_ENV` other than `sandbox` stops the program.
- **Drafts only.** Each proposal is a JSON file with `status: "pending_human_approval"` and the exact PayPal request it would make. No code path sends it.
- **Code owns the amounts.** Refund and invoice amounts are computed from the data. The model writes only the words.
- **Untrusted memo.** Transaction memo fields are fenced in the prompt as data and cannot change an amount.

## Video

<link>

## Try it out

<link>
