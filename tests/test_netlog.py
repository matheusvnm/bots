import json

from probe.netlog import extract_operation_name, is_graphql, build_row


def test_is_graphql_matches_path():
    assert is_graphql("https://www.coinbase.com/graphql/query?x=1")
    assert not is_graphql("https://www.coinbase.com/crypto")


def test_extract_operation_name_from_query_string():
    url = "https://www.coinbase.com/graphql/query?operationName=CryptoQuery&x=1"
    assert extract_operation_name(url, None) == "CryptoQuery"


def test_extract_operation_name_from_post_body():
    body = json.dumps({"operationName": "CashQuery", "variables": {}})
    assert extract_operation_name("https://www.coinbase.com/graphql/query", body) == "CashQuery"


def test_extract_operation_name_none_when_absent():
    assert extract_operation_name("https://www.coinbase.com/graphql/query", "{}") is None


def test_build_row_shape():
    row = build_row(
        seq=7,
        kind="response",
        method="GET",
        url="https://x/graphql/query?operationName=CryptoQuery",
        status=200,
        request_headers={"a": "1"},
        request_body=None,
        response_headers={"content-type": "multipart/mixed"},
        response_body='{"data":{}}',
    )
    assert row["seq"] == 7
    assert row["kind"] == "response"
    assert row["operation_name"] == "CryptoQuery"
    assert row["status"] == 200
    assert "ts" in row
