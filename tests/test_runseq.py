import json

from probe.runseq import SeqClock


def test_seq_is_monotonic():
    clock = SeqClock(meta={"engine": "webkit"})
    assert clock.next() == 0
    assert clock.next() == 1
    assert clock.next() == 2


def test_records_index_entries():
    clock = SeqClock(meta={"engine": "webkit"})
    s = clock.next()
    clock.record(s, kind="nav", url="https://www.coinbase.com/crypto")
    assert clock.index[0]["seq"] == s
    assert clock.index[0]["kind"] == "nav"
    assert clock.index[0]["url"].endswith("/crypto")
    assert "ts" in clock.index[0]


def test_writes_manifest(tmp_path):
    clock = SeqClock(meta={"engine": "webkit", "device": "iPhone 14 Pro"})
    clock.record(clock.next(), kind="tick", url="https://x")
    out = tmp_path / "manifest.json"
    clock.write_manifest(out)
    data = json.loads(out.read_text())
    assert data["meta"]["engine"] == "webkit"
    assert data["meta"]["device"] == "iPhone 14 Pro"
    assert len(data["index"]) == 1
    assert "started_at" in data and "ended_at" in data
