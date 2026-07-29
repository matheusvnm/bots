#!/usr/bin/env python3
"""Strip Coinbase device-trust artifacts from a Playwright storage_state.

Purpose: force the send-confirmation step-up (OTP) to fire for testing.

The send-time OTP is gated by `POST /api/uis/v1/verify-authorization`
(action `web-retail-crypto-sends`). It returns
`status=ACTION_AUTHORIZATION_STATUS_COMPLETE, next_steps=[]` (no OTP) when the
device is already trusted. Trust is carried by the cookies + localStorage keys
below. Removing them — while KEEPING the login/session cookies — leaves the
account logged in but the device un-trusted, so verify-authorization should
return a real 2FA challenge (next_steps populated) at confirmation.

Usage:
    python scripts/strip_device_trust.py                 # default state, keep login
    python scripts/strip_device_trust.py --dry-run       # show what would change
    python scripts/strip_device_trust.py --full-reset    # also drop login/session
    python scripts/strip_device_trust.py --state PATH    # custom storage_state
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

DEFAULT_STATE = (
    Path(".context") / "coinbase_webkit" / "user" / "001" / "state.json"
)

# Device-trust cookies (names). Domain-agnostic match by name.
DEVICE_TRUST_COOKIES = {
    "coinbase_device_id",
    "cb_dm",
    "df_pro",
    "df3",
    "_iidt",
    "ARID",
}

# Session/login cookies — kept unless --full-reset.
SESSION_COOKIES = {
    "login-session",
    "logged_in",
    "unified-session-manager-cookie",
    "unified-oauth-state-cookie",
    "cb-gssc",
}

# Device-trust localStorage keys, keyed by origin.
DEVICE_TRUST_LOCALSTORAGE = {
    "https://login.coinbase.com": {"sealed_df_pro"},
    "https://api.cb-device-intelligence.com": {"_immortal|deviceToken"},
}
# Origins to drop entirely (pure device-intelligence surfaces).
DEVICE_TRUST_ORIGINS = {"https://api.cb-device-intelligence.com"}


def strip(state: dict, full_reset: bool) -> list[str]:
    removed: list[str] = []

    drop_cookies = set(DEVICE_TRUST_COOKIES)
    if full_reset:
        drop_cookies |= SESSION_COOKIES

    kept_cookies = []
    for c in state.get("cookies", []):
        if c.get("name") in drop_cookies:
            removed.append(f"cookie {c.get('name')} ({c.get('domain')})")
        else:
            kept_cookies.append(c)
    state["cookies"] = kept_cookies

    kept_origins = []
    for o in state.get("origins", []):
        origin = o.get("origin")
        if origin in DEVICE_TRUST_ORIGINS:
            removed.append(f"origin {origin} (dropped entirely)")
            continue
        drop_keys = DEVICE_TRUST_LOCALSTORAGE.get(origin, set())
        if drop_keys:
            kept_ls = []
            for item in o.get("localStorage", []):
                if item.get("name") in drop_keys:
                    removed.append(f"localStorage {origin} :: {item.get('name')}")
                else:
                    kept_ls.append(item)
            o["localStorage"] = kept_ls
        kept_origins.append(o)
    state["origins"] = kept_origins

    return removed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--full-reset",
        action="store_true",
        help="also drop login/session cookies (forces a fresh login)",
    )
    args = ap.parse_args()

    if not args.state.exists():
        raise SystemExit(f"storage_state not found: {args.state}")

    state = json.loads(args.state.read_text(encoding="utf-8"))
    removed = strip(json.loads(json.dumps(state)) if args.dry_run else state, args.full_reset)

    print(f"state: {args.state}")
    print(f"mode:  {'FULL RESET' if args.full_reset else 'keep login (device-trust only)'}")
    if not removed:
        print("nothing matched — already stripped?")
        return
    print(f"removing {len(removed)} item(s):")
    for r in removed:
        print(f"  - {r}")

    if args.dry_run:
        print("\n(dry-run: no file written)")
        return

    backup = args.state.with_suffix(".json.bak")
    shutil.copy2(args.state, backup)
    args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nbackup -> {backup}")
    print(f"written -> {args.state}")


if __name__ == "__main__":
    main()
