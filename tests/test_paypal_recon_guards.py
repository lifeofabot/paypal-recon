import io
import json
import urllib.error
import urllib.request

import pytest

from paypal_recon import cli
from paypal_recon.config import ConfigError, Secret, load_config, parse_env_file
from paypal_recon.http import BlockedRequestError, PayPalError, Transport

CID, CSEC = "SENTINEL-CLIENT-ID-xyz", "SENTINEL-CLIENT-SECRET-abc"
TOKEN = "SENTINEL-ACCESS-TOKEN-123"
ENV = {"PAYPAL_CLIENT_ID": CID, "PAYPAL_CLIENT_SECRET": CSEC, "PAYPAL_ENV": "sandbox"}


@pytest.fixture(autouse=True)
def no_real_socket(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real socket")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


class Resp:
    def __init__(self, payload, status=200):
        self.status, self._raw = status, json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._raw


def http_error(code, payload):
    return urllib.error.HTTPError(
        "https://x", code, "m", {}, io.BytesIO(json.dumps(payload).encode())
    )


@pytest.mark.parametrize(
    "method,url",
    [
        ("POST", "https://api-m.sandbox.paypal.com/v2/payments/captures/ABC/refund"),
        ("POST", "https://api-m.sandbox.paypal.com/v2/invoicing/invoices"),
        ("POST", "https://api-m.sandbox.paypal.com/v1/payments/payouts"),
        ("POST", "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1/send"),
        ("GET", "https://api-m.paypal.com/v1/reporting/transactions"),
        ("POST", "https://api-m.paypal.com/v1/oauth2/token"),
        ("GET", "https://api-m.sandbox.paypal.com.evil.example/v1/reporting/transactions"),
        ("GET", "http://api-m.sandbox.paypal.com/v1/reporting/transactions"),
        ("DELETE", "https://api-m.sandbox.paypal.com/v1/reporting/transactions"),
    ],
)
def test_allowlist_blocks_before_socket(method, url):
    def opener(*a, **k):
        raise AssertionError("socket opened")

    with pytest.raises(BlockedRequestError):
        Transport(opener).check(method, url)


def test_transport_has_no_path_to_money_calls():
    def opener(*a, **k):
        raise AssertionError("socket opened")

    for path in (
        "/v2/payments/captures/X/refund",
        "/v2/invoicing/invoices",
        "/v1/payments/payouts",
    ):
        with pytest.raises(BlockedRequestError):
            Transport(opener).request("POST", path, body=b"{}")


def test_allowed_calls_and_token_flow():
    seen = []

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url.split("?")[0]))
        if req.full_url.endswith("/token"):
            return Resp({"access_token": TOKEN})
        return Resp({"transaction_details": []})

    t = Transport(opener)
    tok = t.get_token(Secret(CID), Secret(CSEC))
    assert repr(tok) == "Secret(***)" and str(tok) == "***" and TOKEN not in f"{tok!r}{tok}"
    t.search(tok, {"page": "1"})
    assert seen == [
        ("POST", "https://api-m.sandbox.paypal.com/v1/oauth2/token"),
        ("GET", "https://api-m.sandbox.paypal.com/v1/reporting/transactions"),
    ]


def test_http_error_is_redacted():
    def opener(req, timeout):
        raise http_error(
            403, {"name": "NOT_AUTHORIZED", "debug_id": "abc123", "message": CSEC, "details": [CID]}
        )

    t = Transport(opener)
    with pytest.raises(PayPalError) as ei:
        t.get_token(Secret(CID), Secret(CSEC))
    text = str(ei.value) + repr(ei.value)
    assert "403" in text and "NOT_AUTHORIZED" in text and "abc123" in text
    assert CID not in text and CSEC not in text


def test_non_sandbox_env_refused():
    for bad in ("live", "production", "", "Sandbox "[:-1] + "x"):
        with pytest.raises(ConfigError):
            load_config(None, {**ENV, "PAYPAL_ENV": bad})
    with pytest.raises(ConfigError):
        load_config(None, {k: v for k, v in ENV.items() if k != "PAYPAL_ENV"})


def test_env_file_parse_and_errors_hide_values(tmp_path):
    f = tmp_path / "p.env"
    f.write_text(
        f"# c\nexport PAYPAL_CLIENT_ID='{CID}'\nPAYPAL_CLIENT_SECRET={CSEC}\nPAYPAL_ENV=sandbox\n"
    )
    cfg = load_config(f, {})
    assert cfg.client_id.reveal() == CID and CID not in repr(cfg) + str(cfg)
    f.write_text(f"{CSEC} no equals sign\n")
    with pytest.raises(ConfigError) as ei:
        parse_env_file(f)
    assert CSEC not in str(ei.value)


def test_run_never_prints_secrets_and_stays_in_allowlist(tmp_path, monkeypatch, capsys):
    calls = []
    page = {
        "transaction_details": [
            {
                "transaction_info": {
                    "transaction_id": "TX1",
                    "transaction_event_code": "T0006",
                    "transaction_initiation_date": "2026-10-05T10:00:00+0000",
                    "transaction_amount": {"currency_code": "USD", "value": "12.00"},
                    "transaction_status": "S",
                }
            }
        ],
        "page": 1,
        "total_pages": 1,
    }

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url.split("?")[0]))
        return Resp({"access_token": TOKEN} if req.full_url.endswith("/token") else page)

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    env = tmp_path / "p.env"
    env.write_text("\n".join(f"{k}={v}" for k, v in ENV.items()))
    exp = tmp_path / "e.csv"
    exp.write_text("order_ref,amount,currency,paypal_transaction_id,invoice_id\nA,5.00,USD,,\n")
    out = tmp_path / "out"
    rc = cli.main(
        [
            "run",
            "--env-file",
            str(env),
            "--expected",
            str(exp),
            "--out",
            str(out),
            "--model",
            "replay",
        ]
    )
    # replay has no recording for these prompts: drafts fall back to flag_for_review
    assert rc == 0
    assert {c[1].rsplit("/", 1)[-1] for c in calls} == {"token", "transactions"}
    captured = capsys.readouterr()
    blob = (
        captured.out + captured.err + "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    )
    for secret in (CID, CSEC, TOKEN):
        assert secret not in blob
    assert "POST /v1/oauth2/token -> 200" in captured.out
    for p in (out / "proposals").glob("*.json"):
        assert json.loads(p.read_text())["status"] == "pending_human_approval"


def test_run_error_output_hides_secrets(tmp_path, monkeypatch, capsys):
    def opener(req, timeout):
        raise http_error(401, {"name": "AUTHENTICATION_FAILURE", "debug_id": "d1", "message": CSEC})

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    env = tmp_path / "p.env"
    env.write_text("\n".join(f"{k}={v}" for k, v in ENV.items()))
    exp = tmp_path / "e.csv"
    exp.write_text("order_ref,amount,currency,paypal_transaction_id,invoice_id\nA,5.00,USD,,\n")
    rc = cli.main(
        ["run", "--env-file", str(env), "--expected", str(exp), "--out", str(tmp_path / "o")]
    )
    cap = capsys.readouterr()
    assert rc == 2 and "401" in cap.err
    assert CID not in cap.out + cap.err and CSEC not in cap.out + cap.err
