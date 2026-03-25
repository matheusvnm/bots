"""
Browser-based API key provisioner for Kraken.

Automates the flow of creating an API key through the Kraken web UI:
Kraken Consumer (www.kraken.com) → Kraken Pro (pro.kraken.com).

Uses the existing KrakenAuthenticator for login, then navigates
through the API settings pages to create a key with the required
permissions (Query, Deposit, Withdraw).
"""

from loguru import logger
from patchright.sync_api import Page

from components.dtos import Credentials
from components.trace import PageTracer
from components.utils import human_delay
from services.kraken.constants import KrakenPages
from services.kraken.login import KrakenAuthenticator

_KEY_PREFIX = "ZEROHASH_API_KEY"

_PERMISSIONS = ["Query", "Deposit", "Withdraw"]


class KrakenApiKeyProvisioner:
    """Create an API key via the Kraken UI (Consumer → Pro)."""

    def __init__(self, tracer: PageTracer):
        self.tracer = tracer
        self._auth = KrakenAuthenticator(tracer)

    def provision(self, credentials: Credentials) -> tuple[str, str]:
        """Log in and create a new API key.

        Returns:
            (api_key, api_secret) where api_key is the public key string
            and api_secret is the base64-encoded private key.
        """
        with self._auth.login(credentials) as (page, _interceptor):
            return self._create_api_key(page)

    # ── Navigation ───────────────────────────────────────────

    def _navigate_to_pro_api_settings(self, page: Page) -> None:
        """Navigate from the Consumer dashboard to Kraken Pro API settings.

        Flow:
            1. Go to Consumer API settings page.
            2. Click "Create API Keys" which opens a modal.
            3. Click "Go to Kraken Pro" in the modal.
            4. Wait for Kraken Pro API settings page to load.
        """
        logger.info("Navigating to Consumer API settings...")
        page.goto(KrakenPages.API_SETTINGS, wait_until="domcontentloaded")
        human_delay(page, 2.0, 4.0)
        self.tracer.save(page, "consumer_api_settings")

        # Click "Create API Keys" link/button
        logger.info("Clicking 'Create API Keys'...")
        create_link = page.locator('a:has-text("Create API Keys")')
        create_link.wait_for(state="visible", timeout=15000)
        human_delay(page)
        create_link.click()

        # Handle the "Go to Kraken Pro" modal
        logger.info("Waiting for 'Go to Kraken Pro' modal...")
        dialog = page.locator('div[role="dialog"]')
        dialog.wait_for(state="visible", timeout=10000)
        self.tracer.save(page, "go_to_pro_modal")
        human_delay(page)

        go_to_pro_btn = dialog.locator('button:has-text("Go to Kraken Pro")')
        go_to_pro_btn.click()

        # Wait for navigation to Kraken Pro
        logger.info("Waiting for Kraken Pro API settings page...")
        page.wait_for_url(
            f"{KrakenPages.API_SETTINGS_PRO}**",
            timeout=30000,
            wait_until="domcontentloaded",
        )
        human_delay(page, 2.0, 4.0)
        self.tracer.save(page, "pro_api_settings")

    # ── Key creation ─────────────────────────────────────────

    def _create_api_key(self, page: Page) -> tuple[str, str]:
        """Full flow to create an API key on Kraken Pro."""
        self._navigate_to_pro_api_settings(page)

        # Click "Create API key" on the Pro page
        logger.info("Clicking 'Create API key' on Kraken Pro...")
        create_btn = page.locator('button:has-text("Create API key")')
        create_btn.wait_for(state="visible", timeout=15000)
        human_delay(page)
        create_btn.click()

        # Wait for the "Add API key" form dialog
        logger.info("Waiting for 'Add API key' form...")
        name_input = page.locator('input[name="keyDescription"]')
        name_input.wait_for(state="visible", timeout=15000)
        self.tracer.save(page, "add_api_key_form")
        human_delay(page)

        # Fill the key name
        logger.info("Filling key name: {}", _KEY_PREFIX)
        name_input.fill(_KEY_PREFIX)
        human_delay(page, 0.5, 1.5)

        # Select permissions (Query, Deposit, Withdraw)
        for permission in _PERMISSIONS:
            label = page.locator(
                f'label[data-testid="checkbox-label"][aria-label="{permission}"]'
            )
            label.wait_for(state="visible", timeout=5000)
            label.click()
            logger.info("Permission enabled: {}", permission)
            human_delay(page, 0.3, 0.8)

        self.tracer.save(page, "permissions_set")
        human_delay(page)

        # Click "Generate key"
        logger.info("Submitting key generation...")
        submit_btn = page.locator('button[type="submit"]:has-text("Generate key")')
        submit_btn.wait_for(state="visible", timeout=5000)

        # Wait for the button to become enabled
        page.wait_for_function(
            """() => {
                const btn = document.querySelector(
                    'button[type="submit"]'
                );
                return btn && !btn.disabled;
            }""",
            timeout=10000,
        )
        human_delay(page, 0.5, 1.0)
        submit_btn.click()
        self.tracer.save(page, "generate_key_clicked")

        # Handle 2FA verification
        self._handle_2fa(page)

        # Extract the generated credentials
        return self._extract_credentials(page)

    # ── 2FA handling ─────────────────────────────────────────

    def _handle_2fa(self, page: Page) -> None:
        """Handle the 2FA verification step for API key generation.

        Kraken shows a 2FA dialog after clicking "Generate key".
        We select "Authenticator app" and prompt the user for the code.
        """
        logger.info("Waiting for 2FA verification dialog...")
        tfa_dialog = page.locator('[data-testid="TwoFactorAuthentication"]')
        tfa_dialog.wait_for(state="visible", timeout=30000)
        self.tracer.save(page, "2fa_dialog")
        human_delay(page)

        # Check if we need to select the authenticator app method
        auth_btn = tfa_dialog.locator('button:has-text("Authenticator app")')
        if auth_btn.is_visible():
            logger.info("Selecting 'Authenticator app' method...")
            human_delay(page, 0.5, 1.5)
            auth_btn.click()

        # Wait for the 2FA code input
        tfa_input = page.locator('input[name="tfa"]')
        tfa_input.wait_for(state="visible", timeout=15000)
        self.tracer.save(page, "2fa_code_input")

        # Prompt user for TOTP code
        totp = input("[?] TOTP code for API key creation: ").strip()

        tfa_input.fill(totp)
        human_delay(page, 0.5, 1.0)

        # Click "Enter" to submit the code
        enter_btn = page.locator(
            '[data-testid="TwoFactorAuthentication"] button:has-text("Enter")'
        )
        enter_btn.click()

        logger.info("TOTP submitted — waiting for key generation...")
        self.tracer.save(page, "2fa_submitted")
        human_delay(page, 2.0, 4.0)

    # ── Credential extraction ────────────────────────────────

    def _extract_credentials(self, page: Page) -> tuple[str, str]:
        """Read the API key and private key from the success dialog."""
        logger.info("Waiting for API key creation success...")

        # Wait for the success notification or the key display
        page.wait_for_selector(
            'div[role="alert"][aria-label="Success"], '
            'div[role="dialog"] input[readonly]',
            timeout=60000,
        )
        self.tracer.save(page, "key_created_success")
        human_delay(page, 1.0, 2.0)

        # Scope to the dialog to avoid matching the table row
        # whose aria-label contains "API key" as a substring.
        dialog = page.locator('div[role="dialog"]')

        api_key = dialog.get_by_role("textbox", name="API key").input_value()
        logger.info("API key extracted: {}...", api_key[:20])

        api_secret = dialog.get_by_role("textbox", name="Private key").input_value()
        logger.info("Private key extracted: {}...", api_secret[:20])

        # Close the success dialog
        close_btn = dialog.locator('button[type="submit"]:has-text("Close")')
        if close_btn.is_visible():
            close_btn.click()
            page.wait_for_timeout(1000)

        self.tracer.save(page, "key_dialog_dismissed")
        logger.info("API key created successfully")

        return api_key, api_secret
