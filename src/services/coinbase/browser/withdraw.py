"""
Coinbase withdraw/send flow.

Navigates through the Send modal to:
1. Enter a destination crypto address
2. Select the blockchain network
3. Enter the amount to send
4. Fill travel rule / beneficiary information
5. Review and confirm the transaction
6. Handle 2FA authentication

Usage (integrated into CoinbaseBot):
    with authenticator.login(state_path, credentials) as page:
        withdraw = CoinbaseWithdraw(page, tracer)
        withdraw.run()
"""

from loguru import logger
from patchright.sync_api import Page

from components.trace import PageTracer
from components.utils import human_delay
from services.coinbase.constants import CoinbasePages


class CoinbaseWithdraw:
    def __init__(self, page: Page, tracer: PageTracer, **_):
        self.page = page
        self.tracer = tracer

    # ── Step 1: Open the send modal ────────────────────────────

    def _open_send_modal(self) -> None:
        """Click 'Enviar criptomoeda' to open the send modal."""
        logger.info("Opening send modal...")
        human_delay(self.page)
        self.page.click('[data-testid="quick-action-send"]')
        self.page.wait_for_selector(
            '[data-testid="recipient-search-input"]', timeout=15000
        )
        self.tracer.save(self.page, "send_modal_open")
        logger.info("Send modal open")

    # ── Step 2: Enter recipient address ────────────────────────

    def _enter_recipient(self, address: str) -> None:
        """Type the destination address into the recipient search field."""
        logger.info("Entering recipient address...")
        human_delay(self.page)

        self.page.fill('[data-testid="recipient-search-input"]', address)
        human_delay(self.page, low=1.5, high=3.0)

        # Wait for the full address to appear in the search results.
        # Coinbase renders the address inside a plain div (no data-testid).
        # Recent addresses and "Adicionar contato" load first — we must wait
        # for the element that contains the exact full address text.
        address_locator = self.page.get_by_text(address, exact=True)
        address_locator.wait_for(timeout=15000)
        self.tracer.save(self.page, "recipient_search_results")

        address_locator.click()
        human_delay(self.page)
        self.tracer.save(self.page, "recipient_selected")
        logger.info("Recipient address entered")

    # ── Step 3: Select blockchain network ──────────────────────

    def _select_network(self) -> str | None:
        """Display available networks with fees and let the user pick one.

        Returns ``None`` when Coinbase skips network selection (single-network
        addresses like Solana).
        """
        logger.info("Waiting for network selection screen...")

        # Coinbase may skip network selection entirely for single-network
        # addresses (e.g. Solana) and go straight to the amount screen.
        try:
            self.page.wait_for_selector(
                '[data-testid$="-cell-pressable"][data-testid^="l2-list-item-"], '
                '[data-testid="currency-input"]',
                timeout=15000,
            )
        except Exception:
            logger.warning("Neither network selection nor amount screen appeared")
            return None

        # If the amount screen appeared, network selection was skipped.
        if self.page.locator('[data-testid="currency-input"]').is_visible():
            logger.info("Network selection skipped — single-network address")
            return None

        human_delay(self.page, low=0.5, high=1.0)
        self.tracer.save(self.page, "network_selection")

        # Match only enabled pressable elements:
        #  - suffix "-cell-pressable" excludes the wrapper parents
        #  - :not([disabled]) excludes greyed-out networks
        items = self.page.locator(
            '[data-testid$="-cell-pressable"][data-testid^="l2-list-item-"]'
            ":not([disabled])"
        )
        networks: list[tuple[str, str, int]] = []

        for i in range(items.count()):
            el = items.nth(i)
            text_lines = el.inner_text().strip().splitlines()
            name = text_lines[0] if text_lines else f"Network {i}"
            fee = text_lines[-1] if len(text_lines) > 1 else "?"
            networks.append((name, fee, i))

        if not networks:
            logger.warning("No network options found — proceeding without selection")
            return None

        if len(networks) == 1:
            name, fee, idx = networks[0]
            logger.info("Single network available: {} ({}) — auto-selecting", name, fee)
            items.nth(idx).click()
            self.tracer.save(self.page, f"network_selected_{name}")
            return name

        print("\n  Available networks:")
        for i, (name, fee, _) in enumerate(networks, 1):
            print(f"    {i:3d}. {name:<24s} fee: {fee}")

        choice = input("[?] Enter network number: ").strip()
        if not choice.isdigit():
            raise ValueError("Invalid network selection")

        idx = int(choice) - 1
        if not (0 <= idx < len(networks)):
            raise ValueError("Network index out of range")

        name, fee, item_idx = networks[idx]
        items.nth(item_idx).click()
        human_delay(self.page)
        self.tracer.save(self.page, f"network_selected_{name}")
        logger.info("Network {} selected (fee: {})", name, fee)
        return name

    # ── Step 4: Enter amount ───────────────────────────────────

    def _enter_amount(self, amount: str) -> None:
        """Enter the send amount. Pass 'max' to click the Max button."""
        logger.info("Waiting for amount input screen...")
        self.page.wait_for_selector('[data-testid="currency-input"]', timeout=15000)
        human_delay(self.page, low=0.5, high=1.0)

        # Show available balance
        balance_el = self.page.locator('[data-testid="asset-balance-cell"]')
        if balance_el.is_visible():
            balance_text = balance_el.inner_text().strip()
            print(f"\n  {balance_text}")

        self.tracer.save(self.page, "amount_screen")

        if amount.lower() == "max":
            logger.info("Clicking Max button...")
            self.page.click('[data-testid="max-button"]')
            # "Max" often auto-advances to the next step; only click
            # Continue if it is still visible.
            human_delay(self.page)
            self.tracer.save(self.page, "amount_entered")
            continue_btn = self.page.locator('[data-testid="preview-send-button"]')
            if continue_btn.is_visible():
                continue_btn.click()
                human_delay(self.page)
        else:
            logger.info("Entering amount: {}", amount)
            self.page.click('[data-testid="currency-input"]')
            human_delay(self.page, low=0.3, high=0.6)
            self.page.keyboard.type(amount)
            human_delay(self.page)
            self.tracer.save(self.page, "amount_entered")
            self.page.click('[data-testid="preview-send-button"]')
            human_delay(self.page)

        logger.info("Amount submitted")

    # ── Step 5: Fill travel rule / beneficiary form ────────────

    def _fill_travel_rule(self, name: str, country: str, is_self: bool) -> None:
        """Fill the travel-rule compliance form (required for BR/EU/GB users)."""
        logger.info("Waiting for travel rule form...")

        # The form may not appear for all jurisdictions; wait a short time.
        try:
            self.page.wait_for_selector(
                '[data-testid="beneficiary-full-name"], '
                '[data-testid="send-now-button"]',
                timeout=10000,
            )
        except Exception:
            logger.info("No travel rule form detected — skipping")
            return

        # If we landed directly on the confirmation screen, skip.
        if self.page.locator('[data-testid="send-now-button"]').is_visible():
            logger.info("Travel rule form not shown — skipping")
            return

        self.tracer.save(self.page, "travel_rule_form")

        if is_self:
            logger.info("Checking 'sending to myself' checkbox")
            self.page.click('[data-testid="checkbox-outer"]')
            human_delay(self.page, low=0.5, high=1.0)

        if name:
            logger.info("Filling beneficiary name: {}", name)
            self.page.fill('[data-testid="beneficiary-full-name"]', name)
            human_delay(self.page, low=0.3, high=0.6)

        # Select country from dropdown
        logger.info("Selecting country: {}", country)
        self.page.click('[data-testid="country-select"]')
        human_delay(self.page, low=0.5, high=1.0)

        country_option = f'[data-testid="country-option-{country}"]'
        self.page.wait_for_selector(country_option, timeout=5000)
        self.page.click(country_option)
        human_delay(self.page, low=0.3, high=0.6)

        self.tracer.save(self.page, "travel_rule_filled")

        # Click Continue
        self.page.click('[data-testid="submit-button"]')
        human_delay(self.page)
        logger.info("Travel rule form submitted")

    # ── Step 6: Review and confirm ─────────────────────────────

    def _confirm_and_send(self) -> dict:
        """Read the send preview, display it, and click 'Send now'."""
        logger.info("Waiting for confirmation screen...")
        self.page.wait_for_selector('[data-testid="send-now-button"]', timeout=15000)
        human_delay(self.page, low=0.5, high=1.0)
        self.tracer.save(self.page, "send_preview")

        details: dict = {}

        # Read transaction details
        try:
            fiat = self.page.locator(
                '[data-testid="send-preview-fiat-header"]'
            ).text_content()
            details["fiat_amount"] = (fiat or "").strip()
        except Exception:
            pass

        try:
            crypto = self.page.locator(
                '[data-testid="send-preview-crypto-header"]'
            ).text_content()
            details["crypto_amount"] = (crypto or "").strip()
        except Exception:
            pass

        try:
            recipient = self.page.locator(
                '[data-testid="send-preview-recipient-container"]'
            ).text_content()
            details["recipient"] = (recipient or "").strip()
        except Exception:
            pass

        try:
            network = self.page.locator(
                '[data-testid="send-preview-network"]'
            ).text_content()
            details["network"] = (network or "").strip()
        except Exception:
            pass

        try:
            time_est = self.page.locator('[data-testid="time-estimate"]').text_content()
            details["time_estimate"] = (time_est or "").strip()
        except Exception:
            pass

        try:
            fee_text = self.page.locator(
                '[data-testid="send-preview-footer-network-fee-explainer"]'
            ).text_content()
            details["fee"] = (fee_text or "").strip()
        except Exception:
            pass

        # Display summary
        print("\n── Send preview ──────────────────────────")
        if details.get("fiat_amount"):
            print(f"  Amount:   {details['fiat_amount']}")
        if details.get("crypto_amount"):
            print(f"  Crypto:   {details['crypto_amount']}")
        if details.get("recipient"):
            print(f"  To:       {details['recipient']}")
        if details.get("network"):
            print(f"  Network:  {details['network']}")
        if details.get("time_estimate"):
            print(f"  ETA:      {details['time_estimate']}")
        if details.get("fee"):
            print(f"  Fee:      {details['fee']}")
        print("──────────────────────────────────────────")

        # Auto-confirm
        logger.info("Clicking 'Send now'...")
        self.page.click('[data-testid="send-now-button"]')
        self.tracer.save(self.page, "send_confirmed")
        logger.info("Send confirmed")

        return details

    # ── Step 7: Handle 2FA verification ────────────────────────

    def _handle_2fa(self, max_retries: int = 3) -> None:
        """Handle 2FA challenge after clicking 'Send now'.

        The identity verification loads asynchronously inside
        ``[data-testid="identity-access-view-wrapper"]``.
        We wait for either a TOTP input or for the modal to
        transition past the 2FA step automatically (e.g. passkey).
        """
        logger.info("Waiting for 2FA / identity verification...")

        try:
            self.page.wait_for_selector(
                '[data-testid="identity-access-view-wrapper"], '
                '[data-testid="status-animation-loading"]',
                timeout=15000,
            )
        except Exception:
            logger.info("No 2FA challenge detected — may have been auto-approved")
            return

        # Wait for the 2FA content to load inside the wrapper
        human_delay(self.page, low=2.0, high=4.0)
        self.tracer.save(self.page, "2fa_screen")

        # Check if a TOTP input appeared
        totp_container = self.page.locator(
            "#one-time-code, "
            '[data-testid="code-inputs-container"], '
            'input[inputmode="numeric"]'
        )

        def _past_2fa() -> bool:
            """Check if we've moved past the 2FA screen."""
            # The success screen appeared
            try:
                if self.page.locator(
                    '[data-testid="send-success-content"]'
                ).is_visible():
                    return True
            except Exception:
                pass

            # The modal closed entirely
            try:
                overlay = self.page.locator('[data-testid="modal-overlay"]')
                if not overlay.is_visible():
                    return True
            except Exception:
                return True

            # The status step became active (loading finished)
            try:
                if self.page.locator(
                    '[data-testid="status-step-complete-button"]'
                ).is_visible():
                    return True
            except Exception:
                pass

            return False

        for attempt in range(1, max_retries + 1):
            if _past_2fa():
                logger.info("2FA passed — proceeding")
                return

            # Wait a bit more for dynamic content
            human_delay(self.page, low=1.0, high=2.0)

            if _past_2fa():
                logger.info("2FA auto-approved — proceeding")
                return

            if not totp_container.first.is_visible():
                # No TOTP input yet; could be passkey or still loading
                logger.info(
                    "No TOTP input visible — waiting for 2FA content to load..."
                )
                try:
                    totp_container.first.wait_for(timeout=30000)
                except Exception:
                    if _past_2fa():
                        logger.info("2FA resolved while waiting — proceeding")
                        return
                    logger.warning(
                        "2FA content did not load — may need manual intervention"
                    )
                    raise TimeoutError(
                        "2FA verification content did not appear within timeout"
                    )

            self.tracer.save(self.page, f"2fa_{attempt:02d}_prompt")
            totp = input(
                f"[?] Enter 2FA code (attempt {attempt}/{max_retries}): "
            ).strip()

            if _past_2fa():
                return

            # Try clicking the first input element
            try:
                self.page.click("#one-time-code")
            except Exception:
                try:
                    totp_container.first.click()
                except Exception:
                    pass

            # Type digit by digit
            for digit in totp:
                self.page.keyboard.type(digit)
                self.page.wait_for_timeout(60)

            self.tracer.save(self.page, f"2fa_{attempt:02d}_submitted")
            logger.info("2FA code submitted (attempt {})", attempt)

            # Poll for resolution
            waited = 0
            rejected = False
            while waited < 15000:
                self.page.wait_for_timeout(1000)
                waited += 1000

                if _past_2fa():
                    logger.info("2FA verification passed")
                    return

                # Check for rejection (input cleared)
                if waited >= 5000:
                    try:
                        first_val = self.page.locator("#one-time-code").input_value()
                        if first_val == "":
                            rejected = True
                            break
                    except Exception:
                        pass

            if rejected:
                self.tracer.save(self.page, f"2fa_{attempt:02d}_rejected")
                logger.error("2FA code rejected — incorrect code")
                if attempt == max_retries:
                    raise RuntimeError(f"2FA failed after {max_retries} attempts")
                continue

        raise RuntimeError(f"2FA failed after {max_retries} attempts")

    # ── Step 8: Wait for transaction result ────────────────────

    def _wait_for_result(self) -> str:
        """Wait for the success screen, read the result, and dismiss it."""
        logger.info("Waiting for transaction result...")

        # Wait for the success content or the "Done" button to appear.
        try:
            self.page.wait_for_selector(
                '[data-testid="send-success-content"], '
                '[data-testid="status-step-complete-button"]',
                timeout=60000,
            )
        except Exception:
            self.tracer.save(self.page, "send_timeout")
            logger.warning("Timeout waiting for transaction result")
            return "timeout"

        self.tracer.save(self.page, "send_success")

        # Read the result details from the success screen.
        status = "sent"
        try:
            headline = self.page.locator(
                '[data-testid="send-success-content-headline"]'
            ).text_content()
            if headline:
                status = headline.strip()
                logger.info("Result: {}", status)
        except Exception:
            pass

        # Click "Concluido" / "Done" to dismiss the modal.
        try:
            done_btn = self.page.locator('[data-testid="status-step-complete-button"]')
            if done_btn.is_visible():
                done_btn.click()
                human_delay(self.page)
                logger.info("Success modal dismissed")
        except Exception:
            pass

        self.tracer.save(self.page, "send_complete")
        return status

    # ── Step 9: Retrieve transaction hash ─────────────────────

    def _get_transaction_hash(self) -> str | None:
        """Navigate to /transactions, open the latest entry, and extract the tx hash.

        The hash is read from the block-explorer link inside the
        transaction detail panel, which is locale-independent.
        """
        logger.info("Navigating to transactions page...")
        self.page.goto(
            CoinbasePages.TRANSACTIONS,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Wait for transaction rows to load.
        try:
            self.page.wait_for_selector(
                '[data-testid="transaction-history-row"]', timeout=15000
            )
        except Exception:
            logger.warning("No transactions found on the page")
            self.tracer.save(self.page, "transactions_empty")
            return None

        human_delay(self.page, low=0.5, high=1.0)
        self.tracer.save(self.page, "transactions_list")

        # Click the first (most recent) transaction row.
        first_row = self.page.locator('[data-testid="transaction-history-row"]').first
        first_row.click()
        human_delay(self.page)

        # Wait for the detail panel to open.
        try:
            self.page.wait_for_selector(
                '[data-testid="transaction-modal-redesign"]', timeout=15000
            )
        except Exception:
            logger.warning("Transaction detail panel did not open")
            self.tracer.save(self.page, "transaction_detail_failed")
            return None

        human_delay(self.page, low=0.5, high=1.0)
        self.tracer.save(self.page, "transaction_detail")

        # Extract the hash from the block-explorer link (locale-independent).
        # The link lives inside the detail panel as an <a> whose href
        # contains "/tx/<full_hash>".
        tx_hash: str | None = None
        try:
            explorer_link = self.page.locator('a[href*="/tx/"]')
            explorer_link.wait_for(timeout=10000)
            href = explorer_link.get_attribute("href") or ""
            if "/tx/" in href:
                tx_hash = href.split("/tx/")[-1]
                logger.info("Transaction hash: {}", tx_hash)
        except Exception:
            pass

        # Fallback: click the hash row button to copy the full hash to
        # clipboard.  The hash row is the only row inside the detail panel
        # whose button contains a truncated hash (p with class fgMuted).
        # We locate it via: the last detail row that has a nested button,
        # which is always the hash row.
        if not tx_hash:
            try:
                # The hash row button is the <button> inside the row whose
                # data-testid starts with "transaction-detail-redesign-row-"
                # and that contains a <p> with class "fgMuted-fqraqpo".
                hash_btn = self.page.locator(
                    '[data-testid="crypto-send-transaction-details"] '
                    "button:has(p.fgMuted-fqraqpo)"
                ).last
                hash_btn.click()
                human_delay(self.page, low=0.5, high=1.0)
                tx_hash = self.page.evaluate("navigator.clipboard.readText()")
                if tx_hash:
                    logger.info("Transaction hash (clipboard): {}", tx_hash)
            except Exception:
                logger.warning("Could not retrieve transaction hash")

        # Close the detail panel.
        try:
            close_btn = self.page.locator('[data-testid="close-cta"]')
            if close_btn.is_visible():
                close_btn.click()
        except Exception:
            pass

        return tx_hash

    # ── Main flow ──────────────────────────────────────────────

    def run(self) -> None:
        self._open_send_modal()

        address = input("[?] Destination address: ").strip()
        if not address:
            logger.warning("No address provided — aborting")
            return

        self._enter_recipient(address)
        network = self._select_network()

        amount = input("[?] Amount to send (or 'max'): ").strip()
        if not amount:
            logger.warning("No amount provided — aborting")
            return

        self._enter_amount(amount)

        # Travel rule
        is_self_input = input("[?] Sending to yourself? (y/n): ").strip().lower()
        is_self = is_self_input in ("y", "yes")

        name = ""
        if not is_self:
            name = input("[?] Beneficiary full name: ").strip()

        country = (
            input("[?] Beneficiary country (ISO 2-letter, e.g. BR): ").strip().upper()
        )

        self._fill_travel_rule(name, country, is_self)

        details = self._confirm_and_send()
        self._handle_2fa()
        status = self._wait_for_result()
        tx_hash = self._get_transaction_hash()

        print("\n── Withdraw info ─────────────────────────")
        print(f"  To:      {address}")
        if network:
            print(f"  Network: {network}")
        if details.get("crypto_amount"):
            print(f"  Amount:  {details['crypto_amount']}")
        elif details.get("fiat_amount"):
            print(f"  Amount:  {details['fiat_amount']}")
        else:
            print(f"  Amount:  {amount}")
        if details.get("fee"):
            print(f"  Fee:     {details['fee']}")
        if tx_hash:
            print(f"  TX:      {tx_hash}")
        print(f"  Status:  {status}")
        print("──────────────────────────────────────────")
