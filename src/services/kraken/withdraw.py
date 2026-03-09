import re

from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset
from components.trace import PageTracer


class KrakenWithdraw:

    def __init__(self, tracer: PageTracer, **_):
        self.tracer = tracer

    def _open_crypto_modal(self, page: Page) -> None:
        """Navigate to portfolio and open the withdraw crypto selection modal."""
        logger.info("Withdraw step 1 — clicking portfolio link")
        page.click('a[href="/c/portfolio"]')
        page.wait_for_load_state("domcontentloaded")
        logger.info("Portfolio page loaded (url={})", page.url)
        self.tracer.save(page, "portfolio_page")

        logger.info("Withdraw step 2 — waiting for #instant-btn-withdraw")
        page.wait_for_selector("#instant-btn-withdraw", timeout=60000)
        logger.info("Button visible — clicking")
        self.tracer.save(page, "withdraw_btn_visible")
        page.click("#instant-btn-withdraw")

        logger.info("Withdraw step 3 — waiting for modal to open")
        page.wait_for_selector('[role="heading"]', timeout=30000)
        logger.info("Modal open")
        self.tracer.save(page, "withdraw_modal_open")

        logger.info("Withdraw step 4 — clicking crypto tab")
        page.click('[role="tab"][id="crypto"]')
        logger.info("Crypto tab clicked — waiting 5 s for list to load")
        page.wait_for_timeout(5000)
        self.tracer.save(page, "withdraw_crypto_tab_loaded")

    def _is_zero_balance(self, value: str | None, usd_value: str | None) -> bool:
        """Return True if the value or USD value represents a zero balance."""
        for text in (usd_value, value):
            if not text:
                continue
            stripped = re.sub(r"[^\d.]", "", text)
            try:
                if float(stripped) == 0:
                    return True
            except ValueError:
                continue
        return False

    def _scrape_assets(self, page: Page) -> list[CryptoAsset]:
        """Scroll the virtualized list and return only assets with non-zero balance.

        Stops immediately when the first zero-balance row is encountered, assuming
        the list is sorted by balance descending.
        """
        logger.info("Scraping withdraw assets (stopping at first zero balance)...")
        scroll_container = page.locator('[role="table"]').first
        seen: set[str] = set()
        assets: list[CryptoAsset] = []
        stable_passes = 0

        while stable_passes < 2:
            prev_count = len(seen)
            stop = False

            for btn in page.locator('[role="button"].group').all():
                name_el = btn.locator(".text-ds-primary.text-left")
                if name_el.count() == 0:
                    continue
                name = (name_el.text_content() or "").strip()
                if not name or name in seen:
                    continue

                value_el = btn.locator(".text-ds-primary.text-right")
                usd_el = btn.locator(".text-ds-neutral.text-right")
                value = (
                    (value_el.text_content() or "").strip() or None
                    if value_el.count() > 0
                    else None
                )
                usd_value = (
                    (usd_el.text_content() or "").strip() or None
                    if usd_el.count() > 0
                    else None
                )

                if self._is_zero_balance(value, usd_value):
                    logger.info("Zero balance at '{}' — stopping scroll", name)
                    stop = True
                    break

                seen.add(name)
                short_name_el = btn.locator(".text-ds-neutral.text-left")
                short_name = (
                    (short_name_el.text_content() or "").strip() or None
                    if short_name_el.count() > 0
                    else None
                )
                asset = CryptoAsset(name=name, short_name=short_name, value=value, usd_value=usd_value)
                assets.append(asset)
                logger.info(
                    "  {} ({}) — value={} usd={}",
                    name, short_name or "—", value or "—", usd_value or "—",
                )

            if stop:
                break

            new = len(seen) - prev_count
            if new == 0:
                stable_passes += 1
                logger.debug("No new rows (pass {}/2, total={})", stable_passes, len(seen))
            else:
                stable_passes = 0
                logger.debug("{} new rows (total={})", new, len(seen))

            if stable_passes < 2:
                scroll_container.evaluate("el => { el.scrollTop += 400; }")
                page.wait_for_timeout(300)

        logger.info("Withdraw scrape complete — {} non-zero assets", len(assets))
        return assets

    def _find_asset(self, assets: list[CryptoAsset], query: str) -> CryptoAsset | None:
        """Find an asset by 1-based index, ticker, or partial name (case-insensitive)."""
        if query.isdigit():
            idx = int(query) - 1
            return assets[idx] if 0 <= idx < len(assets) else None

        q = query.upper()
        for asset in assets:
            if asset.short_name and asset.short_name.upper() == q:
                return asset

        q_lower = query.lower()
        for asset in assets:
            if q_lower in asset.name.lower():
                return asset

        return None

    def run(self, page: Page, **_) -> None:
        """
        Withdraw flow:
          1. Scrape assets with non-zero balance (stops at first zero-balance row).
          2. Print available assets with their current balances.
          3. Ask the user which asset to withdraw.
          4. Log the intended action (execution TBD).
        """
        self._open_crypto_modal(page)
        assets = self._scrape_assets(page)

        if not assets:
            logger.warning("No assets with non-zero balance found — nothing to withdraw")
            return

        logger.info("Available assets for withdrawal ({} total):", len(assets))
        for i, asset in enumerate(assets, 1):
            print(f"  {i:4d}. {asset.name} ({asset.short_name or '—'}) — {asset.value or '—'} ({asset.usd_value or '—'})")

        choice = input("[?] Enter number or ticker to withdraw: ").strip()
        selected = self._find_asset(assets, choice)

        if selected:
            logger.info(
                "Selected for withdrawal: {} ({}) — balance: {} ({})",
                selected.name, selected.short_name or "—",
                selected.value or "—", selected.usd_value or "—",
            )
            logger.info("TODO: complete withdraw execution for {} — not yet implemented", selected.name)
        else:
            logger.warning("No asset matched '{}' — aborting", choice)
