"""MoneyEvent and amount/time parsers, mirroring a money-in ledger contract: same fields,
order, invariants and parsing, so events can move into that ledger unchanged."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

# ISO 4217 minor-unit exponents that differ from the default of 2. Add a row
# before importing an export in that currency; an unknown currency gets 2.
_MINOR_EXPONENT: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "JOD": 3,
    "TND": 3,
}

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
# "12.34", "-12.34", "+12.34", "1,234.56", "1234", "(12.34)" — nothing else: no
# currency symbols, no spaces inside, no bare "." forms, thousands groups must
# be exact triples. Parentheses mean negative and exclude an explicit sign.
_AMOUNT_RE = re.compile(
    r"^(?P<paren>\()?(?P<sign>[+-])?(?P<body>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?(?(paren)\))$"
)


def is_currency_code(text: str) -> bool:
    """True for an upper-case three-letter ISO 4217 code; the one currency
    shape check, shared with the adapters' config validation."""
    return _CURRENCY_RE.match(text) is not None


def minor_exponent(currency: str) -> int:
    return _MINOR_EXPONENT.get(currency.upper(), 2)


def parse_amount_minor(text: str, currency: str) -> int:
    """Parse a provider's decimal amount string into signed minor units.

    Accepts "12.34", "-12.34", "+12.34", "1,234.56" and accounting-style
    "(12.34)" (negative). Rejects anything else — including more decimal
    places than the currency carries — so a column mapped to the wrong field
    fails the import instead of silently importing nonsense."""
    raw = text.strip()
    m = _AMOUNT_RE.match(raw)
    if m is None:
        raise ValueError(f"amount {text!r}: not a decimal amount")
    if m.group("paren") and m.group("sign"):
        raise ValueError(f"amount {text!r}: parentheses and an explicit sign")
    exponent = minor_exponent(currency)
    frac = m.group("frac") or ""
    if len(frac) > exponent:
        raise ValueError(
            f"amount {text!r}: {len(frac)} decimal places but {currency} carries {exponent}"
        )
    body = m.group("body").replace(",", "")
    value = Decimal(f"{body}.{frac}" if frac else body).scaleb(exponent)
    minor = int(value)
    if m.group("paren") or m.group("sign") == "-":
        minor = -minor
    return minor


def parse_iso_utc(value: str, *, label: str = "timestamp") -> float:
    """ISO 8601 instant -> epoch seconds; `Z` or an offset is honoured and a
    NAIVE instant is UTC. Money windows and export timestamps are calendar
    bounds, not readings compared against /usage, so naive is allowed here —
    unlike `reconcile`, which refuses it on purpose (`cli._parse_iso_epoch`)."""
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} {value!r}: not a valid ISO 8601 timestamp ({exc})") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def format_amount_minor(minor: int, currency: str) -> str:
    """Inverse of `parse_amount_minor` for display: 1234, "USD" -> "12.34"."""
    exponent = minor_exponent(currency)
    sign = "-" if minor < 0 else ""
    digits = str(abs(minor)).rjust(exponent + 1, "0")
    if exponent == 0:
        return f"{sign}{digits}"
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"


@dataclass(frozen=True, slots=True)
class MoneyEvent:
    """One external transaction as an adapter reports it.

    `external_id` is the provider's own transaction id and is REQUIRED — it is
    never synthesized from row content, because a synthesized id would make
    two genuinely distinct identical-looking transactions collapse into one
    row and would make a provider's edit to a memo look like a new payment.
    `amount_minor` is signed: positive is money in, negative is money out."""

    source: str
    external_id: str
    amount_minor: int
    currency: str
    ts: float
    counterparty: str | None = None
    memo: str | None = None

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise ValueError("source: empty")
        if not isinstance(self.external_id, str) or not self.external_id.strip():
            raise ValueError(f"external_id: empty (source={self.source!r}); refusing to store")
        if not is_currency_code(self.currency):
            raise ValueError(f"currency {self.currency!r}: not an upper-case ISO 4217 code")
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ValueError(f"amount_minor {self.amount_minor!r}: not an int")
