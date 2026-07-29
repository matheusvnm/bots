import json
from pathlib import Path

import pytest

from coinbase_balance import (
    AssetBalance,
    ParseStatus,
    parse_crypto,
    parse_cash,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_crypto_returns_complete_with_two_assets():
    status, balances = parse_crypto(_load("crypto_query.json"))
    assert status == ParseStatus.COMPLETE
    assert [b.key for b in balances] == ["BTC", "ETH"]


def test_parse_crypto_maps_fields():
    _, balances = parse_crypto(_load("crypto_query.json"))
    btc = balances[0]
    assert btc.key == "BTC"
    assert btc.label == "Bitcoin"
    assert btc.amount == "0.0000608"
    assert btc.notional == "19.7372680171393598704118832"
    assert btc.currency == "BRL"
    assert btc.totalStakedPercent is None
    assert btc.precision is None
    assert btc.extractedAt  # non-empty ISO string


def test_parse_crypto_scales_staked_percent_times_100():
    _, balances = parse_crypto(_load("crypto_query.json"))
    eth = balances[1]
    assert eth.key == "ETH"
    assert eth.totalStakedPercent == "100.0"


def test_parse_crypto_currency_defaults_to_usd_when_absent():
    data = {"data": {"viewer": {"cryptoAssets": {"edges": [
        {"node": {
            "totalBalanceCrypto": {"amount": "1"},
            "totalBalanceFiat": {"amount": "2"},
            "asset": {"asset": {"displaySymbol": "SOL", "name": "Solana"}},
        }}
    ]}}}}
    _, balances = parse_crypto(data)
    assert balances[0].currency == "USD"


def test_parse_cash_skips_tierscurrency_zero_edge_without_crashing():
    status, balances = parse_cash(_load("cash_query.json"))
    assert status == ParseStatus.COMPLETE
    assert [b.key for b in balances] == ["USDC"]
    usdc = balances[0]
    assert usdc.amount == "8.91252"
    assert usdc.notional == "45.248180746799999702916"
    assert usdc.currency == "BRL"
    assert usdc.totalStakedPercent is None


def test_parse_cash_currency_from_deposited_summary():
    data = {"data": {"viewer": {
        "netPerformance": {"summary": {"deposited": {"currency": "EUR"}}},
        "cashAssets": {"edges": [
            {"node": {
                "totalBalanceCrypto": {"amount": "1"},
                "totalBalanceFiat": {"amount": "2"},
                "asset": {"asset": {"displaySymbol": "USDC", "name": "USDC"}},
            }}
        ]},
    }}}
    _, balances = parse_cash(data)
    assert balances[0].currency == "EUR"


def test_parse_cash_currency_falls_back_to_fiat_accounts():
    data = {"data": {"viewer": {
        "fiatAccounts": [{"availableBalance": {"currency": "GBP"}}],
        "cashAssets": {"edges": [
            {"node": {
                "totalBalanceCrypto": {"amount": "1"},
                "totalBalanceFiat": {"amount": "2"},
                "asset": {"asset": {"displaySymbol": "USDC", "name": "USDC"}},
            }}
        ]},
    }}}
    _, balances = parse_cash(data)
    assert balances[0].currency == "GBP"


def test_parse_cash_currency_none_when_absent():
    data = {"data": {"viewer": {"cashAssets": {"edges": [
        {"node": {
            "totalBalanceCrypto": {"amount": "1"},
            "totalBalanceFiat": {"amount": "2"},
            "asset": {"asset": {"displaySymbol": "USDC", "name": "USDC"}},
        }}
    ]}}}}
    _, balances = parse_cash(data)
    assert balances[0].currency is None


def test_parse_skips_zero_balance_edge():
    data = {"data": {"viewer": {"cryptoAssets": {"edges": [
        {"node": {
            "totalBalanceCrypto": {"amount": "0"},
            "totalBalanceFiat": {"amount": "0"},
            "asset": {"asset": {"displaySymbol": "BTC", "name": "Bitcoin"}},
        }}
    ]}}}}
    status, balances = parse_crypto(data)
    assert status == ParseStatus.COMPLETE
    assert balances == []


def test_parse_missing_viewer_is_incomplete():
    status, balances = parse_crypto({"data": {}})
    assert status == ParseStatus.INCOMPLETE
    assert balances == []


def test_parse_missing_connection_is_incomplete():
    status, _ = parse_crypto({"data": {"viewer": {}}})
    assert status == ParseStatus.INCOMPLETE


def test_parse_present_but_empty_connection_is_complete_zero():
    status, balances = parse_cash({"data": {"viewer": {"cashAssets": {"edges": []}}}})
    assert status == ParseStatus.COMPLETE
    assert balances == []


from coinbase_balance import success_envelope, error_envelope


def test_success_envelope_serializes_balances():
    _, balances = parse_crypto(_load("crypto_query.json"))
    env = success_envelope(balances)
    assert env["success"] is True
    assert env["error"] is None
    assert env["retryable"] is False
    assert isinstance(env["data"], list)
    assert env["data"][0]["key"] == "BTC"
    assert set(env["data"][0].keys()) == {
        "key", "label", "amount", "notional", "currency",
        "totalStakedPercent", "precision", "extractedAt",
    }


def test_error_envelope_shape():
    env = error_envelope("CHALLENGE_UNSOLVED", retryable=True)
    assert env == {
        "success": False,
        "data": None,
        "error": "CHALLENGE_UNSOLVED",
        "retryable": True,
    }


def test_error_envelope_defaults_not_retryable():
    env = error_envelope("NOT_LOGGED_IN")
    assert env["retryable"] is False


from coinbase_balance import _decode_graphql_body


def test_decode_plain_json_body():
    out = _decode_graphql_body('{"data":{"viewer":{}}}', "application/json")
    assert out == {"data": {"viewer": {}}}


def test_decode_multipart_body_folds():
    body = (
        "--graphql\r\n\r\n"
        '{"data":{"viewer":{"x":1}}}\r\n'
        "--graphql--\r\n"
    )
    out = _decode_graphql_body(body, "multipart/mixed; deferSpec=20220824")
    assert out["data"]["viewer"]["x"] == 1


def test_decode_invalid_body_returns_none():
    assert _decode_graphql_body("not json", "application/json") is None
