"""Credentials and the sandbox-only refusal. No secret value is ever put in a message."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

KEYS = ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_ENV")


class ConfigError(Exception):
    pass


class Secret:
    """Holds a value; repr and str print `***`. Use `.reveal()` at the single point of use."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "***"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __reduce__(self):
        raise TypeError("Secret cannot be pickled")


@dataclass(frozen=True)
class Config:
    client_id: Secret
    client_secret: Secret
    env: str


def parse_env_file(path: Path) -> dict[str, str]:
    """A minimal KEY=VALUE parser. Errors name the line number only, never its content."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read env file {Path(path).name}: {type(exc).__name__}") from None
    out: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum():
            raise ConfigError(f"env file line {number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_config(env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    source = dict(os.environ if environ is None else environ)
    if env_file is not None:
        source.update(parse_env_file(env_file))
    missing = [k for k in KEYS if not source.get(k, "").strip()]
    if missing:
        raise ConfigError(f"missing settings: {', '.join(missing)}")
    if source["PAYPAL_ENV"].strip() != "sandbox":
        raise ConfigError("PAYPAL_ENV must be exactly 'sandbox'; this program is sandbox-only")
    return Config(
        client_id=Secret(source["PAYPAL_CLIENT_ID"].strip()),
        client_secret=Secret(source["PAYPAL_CLIENT_SECRET"].strip()),
        env="sandbox",
    )
