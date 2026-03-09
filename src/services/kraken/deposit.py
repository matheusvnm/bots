import json
from pathlib import Path

from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset, KrakenCredentials
from components.trace import PageTracer


class KrakenDeposit:

    def __init__(self, cache_path: Path, tracer: PageTracer, **_):
        self.cache_path = cache_path.with_name("deposit_assets_cache.json")
        self.tracer = tracer

    def _open_crypto_modal(self, page: Page) -> None:
        """Navigate to portfolio and open the deposit crypto selection modal."""
        logger.info("Deposit step 1 — clicking portfolio link")
        page.click('a[href="/c/portfolio"]')
        page.wait_for_load_state("domcontentloaded")
        logger.info("Portfolio page loaded (url={})", page.url)
        self.tracer.save(page, "portfolio_page")

        logger.info("Deposit step 2 — waiting for #instant-btn-deposit")
        page.wait_for_selector("#instant-btn-deposit", timeout=60000)
        logger.info("Button visible — clicking")
        self.tracer.save(page, "deposit_btn_visible")
        page.click("#instant-btn-deposit")

        logger.info("Deposit step 3 — waiting for modal to open")
        page.wait_for_selector('[role="heading"]', timeout=30000)
        logger.info("Modal open")
        self.tracer.save(page, "deposit_modal_open")

        logger.info("Deposit step 4 — clicking crypto tab")
        page.click('[role="tab"][id="crypto"]')
        logger.info("Crypto tab clicked — waiting 5 s for list to load")
        page.wait_for_timeout(5000)
        self.tracer.save(page, "deposit_crypto_tab_loaded")

    def _load_cache(self, cache_path: Path) -> list[CryptoAsset] | None:
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            assets = [
                CryptoAsset(name=d["name"], short_name=d.get("short_name"), value=None, usd_value=None)
                for d in data
            ]
            logger.info("Cache loaded from {} ({} items)", cache_path, len(assets))
            return assets
        except Exception as e:
            logger.warning("Cache read failed ({}): {}", cache_path, e)
            return None

    def _save_cache(self, cache_path: Path, assets: list[CryptoAsset]) -> None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = [{"name": a.name, "short_name": a.short_name} for a in assets]
            cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Cache saved → {} ({} items)", cache_path, len(assets))
        except Exception as e:
            logger.warning("Cache write failed ({}): {}", cache_path, e)


    def _scrape_assets(self, page: Page) -> list[CryptoAsset]:
        """Scroll the full virtualized list and return all assets (name + short_name only).

        Stops after two consecutive passes with no new assets.
        """
        logger.info("Scraping all deposit assets (virtualized list)...")
        scroll_container = page.locator('[role="table"]').first
        seen: set[str] = set()
        assets: list[CryptoAsset] = []
        stable_passes = 0

        while stable_passes < 2:
            prev_count = len(seen)
            for btn in page.locator('[role="button"].group').all():
                name_el = btn.locator(".text-ds-primary.text-left")
                if name_el.count() == 0:
                    continue
                name = (name_el.text_content() or "").strip()
                if not name or name in seen:
                    continue

                seen.add(name)
                short_name_el = btn.locator(".text-ds-neutral.text-left")
                short_name = (
                    (short_name_el.text_content() or "").strip() or None
                    if short_name_el.count() > 0
                    else None
                )
                assets.append(CryptoAsset(name=name, short_name=short_name, value=None, usd_value=None))
                logger.debug("  Found: {} ({})", name, short_name or "—")

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

        logger.info("Deposit scrape complete — {} assets", len(assets))
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


    def run(self, page: Page, credentials: KrakenCredentials, refresh: bool = False, **_) -> None:
        """
        Deposit flow:
          1. Load asset list from cache (or scrape if cache missing / --refresh).
          2. Print all assets numbered for selection.
          3. Ask the user which asset to deposit.
          4. Log the intended action (execution TBD).
        """

        assets: list[CryptoAsset] | None = None
        if not refresh:
            assets = self._load_cache(self.cache_path)
            if assets:
                logger.info("Using cached asset list — pass --refresh to re-scrape")

        if assets is None:
            logger.info("Scraping deposit asset list from Kraken...")
            self._open_crypto_modal(page)
            assets = self._scrape_assets(page)
            self._save_cache(self.cache_path, assets)

        logger.info("Available assets for deposit ({} total):", len(assets))
        for i, asset in enumerate(assets, 1):
            print(f"  {i:4d}. {asset.name} ({asset.short_name or '—'})")

        choice = input("[?] Enter number or ticker to deposit: ").strip()
        selected = self._find_asset(assets, choice)

        if selected:
            logger.info(
                "Selected for deposit: {} ({})",
                selected.name, selected.short_name or "—",
            )
            logger.info("TODO: complete deposit execution for {} — not yet implemented", selected.name)
        else:
            logger.warning("No asset matched '{}' — aborting", choice)
