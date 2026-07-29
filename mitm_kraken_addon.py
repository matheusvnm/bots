"""mitmproxy addon: dump the TRUE on-the-wire request headers for Kraken's
sign-in call (and any iapi.kraken.com POST), which WebResourceRequest hides.

Run:
    mitmdump -q -s mitm_kraken_addon.py -p 8080
"""

from mitmproxy import http

TARGET = "settings/tfa"


STRIP_XRW = True  # in-flight test: remove x-requested-with from Kraken API calls


def request(flow: http.HTTPFlow) -> None:
    url = flow.request.pretty_url
    # DECISIVE TEST: strip x-requested-with on the live wire for iapi.kraken.com.
    if STRIP_XRW and "iapi.kraken.com" in url and "x-requested-with" in flow.request.headers:
        del flow.request.headers["x-requested-with"]
        print(f"[STRIPPED x-requested-with] {flow.request.method} {url[:70]}", flush=True)
    # Log EVERY host so we can confirm 10.0.2.2 is bypassed (should NOT appear).
    if "kraken.com" not in url:
        print(f"[wire-other] {flow.request.method} {url[:80]}", flush=True)
        return
    # Heartbeat: prove interception + TLS trust as soon as the page loads.
    if "iapi.kraken.com" not in url:
        print(f"[wire] {flow.request.method} {url[:80]}", flush=True)
        return
    if TARGET in url or flow.request.method == "POST":
        tag = " <<< TFA" if TARGET in url else ""
        lines = [f"\n[WIRE REQ]{tag} {flow.request.method} {url}"]
        for k, v in flow.request.headers.items(multi=True):
            lines.append(f"    {k}: {v}")
        print("\n".join(lines), flush=True)
    else:
        print(f"[wire] {flow.request.method} {url[:80]}", flush=True)


def response(flow: http.HTTPFlow) -> None:
    if "settings/tfa" in flow.request.pretty_url:
        print(
            f"[WIRE RESP] tfa -> {flow.response.status_code}",
            flush=True,
        )
