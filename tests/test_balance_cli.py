import coinbase_balance as m


def test_module_exposes_main_and_run():
    assert callable(m.main)
    assert callable(m.run)


def test_run_returns_envelope_on_error(monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def fake_session(state_file, headless):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(m, "webkit_session", fake_session)
    env = m.run(timeout_s=1, state_file=m.DEFAULT_STATE, headless=True)
    assert env["success"] is False
    assert env["error"] == "boom"
