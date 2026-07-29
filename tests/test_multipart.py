import json

from probe.multipart import fold_multipart


def test_plain_json_passthrough():
    body = json.dumps({"data": {"viewer": {"cashAssets": {"edges": []}}}})
    assert fold_multipart(body) == {"data": {"viewer": {"cashAssets": {"edges": []}}}}


def test_folds_incremental_defer_patch():
    base = (
        "--graphql\r\n"
        "content-type: application/json\r\n\r\n"
        '{"data":{"viewer":{"cryptoAssets":{}}},"hasNext":true}\r\n'
        "--graphql\r\n"
        "content-type: application/json\r\n\r\n"
        '{"incremental":[{"path":["data","viewer","cryptoAssets"],'
        '"data":{"edges":[{"node":{"k":"BTC"}}]}}],"hasNext":false}\r\n'
        "--graphql--\r\n"
    )
    folded = fold_multipart(base)
    assert folded["data"]["viewer"]["cryptoAssets"]["edges"][0]["node"]["k"] == "BTC"
    assert "incremental" not in folded
    assert "hasNext" not in folded


def test_skips_malformed_chunk():
    body = (
        "--graphql\r\n\r\n"
        '{"data":{"ok":true}}\r\n'
        "--graphql\r\n\r\n"
        "{not valid json\r\n"
        "--graphql--\r\n"
    )
    folded = fold_multipart(body)
    assert folded["data"]["ok"] is True


def test_rejects_prototype_pollution_path():
    body = (
        "--graphql\r\n\r\n"
        '{"data":{}}\r\n'
        "--graphql\r\n\r\n"
        '{"incremental":[{"path":["__proto__"],"data":{"polluted":true}}]}\r\n'
        "--graphql--\r\n"
    )
    folded = fold_multipart(body)
    assert "polluted" not in folded
    assert not hasattr({}, "polluted")
