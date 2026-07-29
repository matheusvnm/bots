import json

from probe.storage import StateSnapshotter, STORAGE_JS, INDEXEDDB_JS


class FakePage:
    def __init__(self, eval_results):
        self._eval_results = eval_results
        self.calls = []

    def evaluate(self, script):
        self.calls.append(script)
        # return queued result by call order
        return self._eval_results.pop(0)


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


def test_storage_js_is_nonempty_strings():
    assert "localStorage" in STORAGE_JS
    assert "indexedDB" in INDEXEDDB_JS


def test_snapshot_writes_all_four_files(tmp_path):
    page = FakePage(
        eval_results=[
            {"local": {"userleap.ids": "abc"}, "session": {"k": "v"}},  # STORAGE_JS
            [{"name": "cb-db", "stores": ["kv"]}],                       # INDEXEDDB_JS
        ]
    )
    ctx = FakeContext(cookies=[{"name": "coinbase_device_id", "value": "d1"}])
    snap = StateSnapshotter(log_dir=tmp_path, context=ctx)
    snap.snapshot(page, seq=3)

    cookies = json.loads((tmp_path / "storage" / "0003_cookies.json").read_text())
    local = json.loads((tmp_path / "storage" / "0003_local.json").read_text())
    session = json.loads((tmp_path / "storage" / "0003_session.json").read_text())
    idb = json.loads((tmp_path / "storage" / "0003_indexeddb.json").read_text())

    assert cookies[0]["name"] == "coinbase_device_id"
    assert local["userleap.ids"] == "abc"
    assert session["k"] == "v"
    assert idb[0]["name"] == "cb-db"
