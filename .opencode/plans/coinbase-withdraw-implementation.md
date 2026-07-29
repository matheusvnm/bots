# Coinbase Browser Withdraw Implementation Plan

## Target File
`src/services/coinbase/browser/withdraw.py`

## Implementation

Replace the entire file with the following implementation:

```python
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

        # After typing an address, Coinbase shows matching recent addresses
        # or recognises the raw address. Click the first matching result.
        results = self.page.locator('[data-testid="list-cell-cell-pressable"]')
        results.first.wait_for(timeout=15000)
        self.tracer.save(self.page, "recipient_search_results")

        results.first.click()
        human_delay(self.page)
        self.tracer.save(self.page, "recipient_selected")
        logger.info("Recipient address entered")

    # ── Step 3: Select blockchain network ──────────────────────

    def _select_network(self) -> str:
        """Display available networks with fees and let the user pick one."""
        logger.info("Waiting for network selection screen...")
        self.page.wait_for_selector(
            '[data-testid^="l2-list-item-"]', timeout=15000
        )
        human_delay(self.page, low=0.5, high=1.0)
        self.tracer.save(self.page, "network_selection")

        items = self.page.locator('[data-testid^="l2-list-item-"]')
        networks: list[tuple[str, str, int]] = []

        for i in range(items.count()):
            el = items.nth(i)
            text_lines = el.inner_text().strip().splitlines()
            name = text_lines[0] if text_lines else f"Network {i}"
            fee = text_lines[-1] if len(text_lines) > 1 else "?"
            networks.append((name, fee, i))

        if not networks:
            logger.warning("No network options found — proceeding without selection")
            return "unknown"

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
        self.page.wait_for_selector(
            '[data-testid="currency-input"]', timeout=15000
        )
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
        else:
            logger.info("Entering amount: {}", amount)
            self.page.click('[data-testid="currency-input"]')
            human_delay(self.page, low=0.3, high=0.6)
            self.page.keyboard.type(amount)

        human_delay(self.page)
        self.tracer.save(self.page, "amount_entered")

        # Click "Continuar" (Continue)
        continue_btn = self.page.get_by_text("Continuar")
        continue_btn.click()
        human_delay(self.page)
        logger.info("Amount submitted")

    # ── Step 5: Fill travel rule / beneficiary form ────────────

    def _fill_travel_rule(
        self, name: str, country: str, is_self: bool
    ) -> None:
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

        # Click "Continuar" (Continue)
        continue_btn = self.page.get_by_text("Continuar")
        continue_btn.click()
        human_delay(self.page)
        logger.info("Travel rule form submitted")

    # ── Step 6: Review and confirm ─────────────────────────────

    def _confirm_and_send(self) -> dict:
        """Read the send preview, display it, and click 'Send now'."""
        logger.info("Waiting for confirmation screen...")
        self.page.wait_for_selector(
            '[data-testid="send-now-button"]', timeout=15000
        )
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
            time_est = self.page.locator(
                '[data-testid="time-estimate"]'
            ).text_content()
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
            '#one-time-code, '
            '[data-testid="code-inputs-container"], '
            'input[inputmode="numeric"]'
        )

        def _past_2fa() -> bool:
            """Check if we've moved past the 2FA screen."""
            # The modal may close or show a success status
            try:
                overlay = self.page.locator('[data-testid="modal-overlay"]')
                if not overlay.is_visible():
                    return True
            except Exception:
                return True

            # Or the status step becomes active with a non-loading state
            try:
                if self.page.locator(
                    '[data-testid="step-statusStep-active"]'
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
                        first_val = self.page.locator(
                            "#one-time-code"
                        ).input_value()
                        if first_val == "":
                            rejected = True
                            break
                    except Exception:
                        pass

            if rejected:
                self.tracer.save(self.page, f"2fa_{attempt:02d}_rejected")
                logger.error("2FA code rejected — incorrect code")
                if attempt == max_retries:
                    raise RuntimeError(
                        f"2FA failed after {max_retries} attempts"
                    )
                continue

        raise RuntimeError(f"2FA failed after {max_retries} attempts")

    # ── Step 8: Wait for transaction result ────────────────────

    def _wait_for_result(self) -> str:
        """Wait for the transaction to process and return the final status."""
        logger.info("Waiting for transaction result...")

        # The send flow may show a loading animation then return to home,
        # or show a success status inside the modal.
        waited = 0
        while waited < 60000:
            self.page.wait_for_timeout(2000)
            waited += 2000

            # Check if modal closed (returned to home)
            try:
                overlay = self.page.locator('[data-testid="modal-overlay"]')
                if not overlay.is_visible():
                    self.tracer.save(self.page, "send_complete_home")
                    logger.info("Transaction submitted — modal closed")
                    return "submitted"
            except Exception:
                self.tracer.save(self.page, "send_complete_no_modal")
                return "submitted"

            # Check for a success status inside the modal
            try:
                headline = self.page.locator(
                    '[data-testid="default-send-stepper-headline"]'
                ).text_content()
                if headline and headline.strip():
                    logger.info("Status headline: {}", headline.strip())
                    self.tracer.save(self.page, "send_status")
                    return headline.strip()
            except Exception:
                pass

        self.tracer.save(self.page, "send_timeout")
        logger.warning("Timeout waiting for transaction result")
        return "timeout"

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
            input("[?] Beneficiary country (ISO 2-letter, e.g. BR): ")
            .strip()
            .upper()
        )

        self._fill_travel_rule(name, country, is_self)

        details = self._confirm_and_send()
        self._handle_2fa()
        status = self._wait_for_result()

        print("\n── Withdraw info ─────────────────────────")
        print(f"  To:      {address}")
        print(f"  Network: {network}")
        if details.get("crypto_amount"):
            print(f"  Amount:  {details['crypto_amount']}")
        elif details.get("fiat_amount"):
            print(f"  Amount:  {details['fiat_amount']}")
        else:
            print(f"  Amount:  {amount}")
        if details.get("fee"):
            print(f"  Fee:     {details['fee']}")
        print(f"  Status:  {status}")
        print("──────────────────────────────────────────")
```

## Key Design Decisions

### Selectors (all from explored HTML snapshots)

| Step | Selector | Source |
|------|----------|--------|
| Open modal | `[data-testid="quick-action-send"]` | screenshot 002 |
| Recipient input | `[data-testid="recipient-search-input"]` | screenshot 003 |
| Search result | `[data-testid="list-cell-cell-pressable"]` | screenshot 014 |
| Network items | `[data-testid^="l2-list-item-"]` | screenshot 016-017 |
| Amount input | `[data-testid="currency-input"]` | screenshot 018-020 |
| Max button | `[data-testid="max-button"]` | screenshot 018 |
| Balance display | `[data-testid="asset-balance-cell"]` | screenshot 018 |
| Beneficiary name | `[data-testid="beneficiary-full-name"]` | screenshot 025 |
| Country select | `[data-testid="country-select"]` | screenshot 025 |
| Country option | `[data-testid="country-option-{ISO}"]` | screenshot 029 |
| Self-send checkbox | `[data-testid="checkbox-outer"]` | screenshot 025 |
| Send now button | `[data-testid="send-now-button"]` | HTML 033 |
| Preview headers | `[data-testid="send-preview-fiat-header"]`, etc. | HTML 033 |
| 2FA wrapper | `[data-testid="identity-access-view-wrapper"]` | HTML 034 |

### Patterns Followed

- Same constructor signature as `CoinbaseDeposit` and `CoinbaseBalance`
- Uses `human_delay()` between actions (from `components.utils`)
- Uses `self.tracer.save()` at each significant step
- CLI `input()` for user interaction
- Same 2FA handling pattern as `login.py:_handle_totp()` (digit-by-digit, retry loop)
- Result printed in `── info ──────` box format matching `CoinbaseDeposit` and `CoinbaseApiWithdraw`

### Important Notes for Users

1. **Network selection is critical**: Sending on the wrong network causes permanent fund loss. Available networks depend on the address format (EVM `0x...` shows Ethereum/Base/Polygon/Arbitrum/etc., Bitcoin addresses show Bitcoin only).

2. **Observed networks for USDC** (with `0x...` address): Ethereum ($0.127), Base ($0.003), Polygon ($0.011), Sui ($0.091), Arbitrum ($0.001), Avalanche C-Chain ($0.00), Aptos ($0.113).

3. **Travel rule**: Required for BR/EU/GB jurisdictions. The form asks for beneficiary name, country, and whether you're sending to yourself.

4. **2FA**: Required for every send. Have your authenticator app ready. The bot prompts for the TOTP code via CLI.

5. **Amount input**: Default mode is USD. The crypto equivalent is shown below. The asset (e.g. USDC) is auto-selected based on context.

6. **No other files need changes**: `CoinbaseBot.ACTIONS` already maps `"withdraw"` to `CoinbaseWithdraw`.

## Files Modified

| File | Change |
|------|--------|
| `src/services/coinbase/browser/withdraw.py` | Full implementation (only file changed) |
