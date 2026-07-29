"""
On-device Android WebView request-header capture via CDP.

Attaches to a running Android System WebView (exposed by
setWebContentsDebuggingEnabled(true)) over the DevTools protocol and records the
REQUEST HEADERS of the Kraken sign-in call, so we can confirm -- on the real
device -- whether x-requested-with is actually stripped by
setRequestedWithHeaderOriginAllowList.

The Kraken .../account/settings/tfa POST fires from a SUBFRAME (mainFrame=false),
so we use Target.setAutoAttach(flatten) to attach to every nested frame/worker
target and enable Network on each. This is what the plain page-level listener
missed.

Prereq (other shell):
    adb forward tcp:9333 localabstract:webview_devtools_remote_<pid>

Then:
    cd /Users/smmarques/Github/scrapping/bots
    uv run python android_cdp_header_capture.py
"""

from __future__ import annotations

import functools
import os
import re
import sys

from playwright.sync_api import sync_playwright

print = functools.partial(print, flush=True)  # noqa: A001

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9333")
KEY = re.compile(r"settings/tfa|iapi\.kraken|/auth|sign-?in|/login", re.I)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        print(f"connected to {CDP_URL}; contexts={len(browser.contexts)}")

        def attach_network(session, label: str) -> None:
            try:
                session.send("Network.enable")
            except Exception as exc:  # noqa: BLE001
                print(f"  (Network.enable failed on {label}: {exc})")
                return

            def will(evt) -> None:
                req = evt.get("request", {})
                url = req.get("url", "")
                if not KEY.search(url):
                    return
                h = {k.lower(): v for k, v in (req.get("headers") or {}).items()}
                is_tfa = "settings/tfa" in url
                if not (is_tfa or req.get("method") == "POST"):
                    return
                tag = " <<< TFA" if is_tfa else ""
                print(f"\n[REQ]{tag} {req.get('method')} {url[:110]}")
                print(f"    x-requested-with : {h.get('x-requested-with', '<ABSENT>')}")
                print(f"    sec-ch-ua        : {h.get('sec-ch-ua', '<absent>')}")
                print(f"    user-agent       : {h.get('user-agent', '<absent>')[:70]}")

            def resp(evt) -> None:
                r = evt.get("response", {})
                if "settings/tfa" in r.get("url", ""):
                    print(f"[RESP] tfa -> HTTP {r.get('status')}")

            session.on("Network.requestWillBeSent", will)
            session.on("Network.responseReceived", resp)

        def wire(page) -> None:
            root = page.context.new_cdp_session(page)
            attach_network(root, page.url[:60])
            print(f"WATCHING (root+subframes): {page.url[:70]}")

            # Auto-attach to nested frames / workers (the tfa call is in a subframe).
            def on_attached(evt) -> None:
                sid = evt.get("sessionId")
                ti = evt.get("targetInfo", {})
                if not sid:
                    return
                try:
                    child = root.session_from_id(sid)  # type: ignore[attr-defined]
                except Exception:
                    child = None
                if child is not None:
                    attach_network(child, ti.get("url", "")[:50])
                    print(f"  + attached child target: {ti.get('type')} "
                          f"{ti.get('url','')[:60]}")

            try:
                root.on("Target.attachedToTarget", on_attached)
                root.send(
                    "Target.setAutoAttach",
                    {"autoAttach": True, "waitForDebuggerOnStart": False,
                     "flatten": True},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  (setAutoAttach unsupported: {exc})")

        for ctx in browser.contexts:
            ctx.on("page", wire)
            for pg in ctx.pages:
                wire(pg)

        print("\nREADY - submit the Kraken sign-in on the device now. Ctrl-C to stop.\n")
        try:
            while True:
                sys.stdin.readline()
        except KeyboardInterrupt:
            print("stopping")


if __name__ == "__main__":
    main()
