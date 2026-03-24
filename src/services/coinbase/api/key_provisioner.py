from loguru import logger
from patchright.sync_api import Page

from components.dtos import Credentials
from components.trace import PageTracer
from components.utils import human_delay
from services.coinbase.constants import CoinbasePages
from services.coinbase.login import CoinbaseAuthenticator

_KEY_PREFIX = "ZEROHASH_API_KEY"


class CoinbaseApiKeyProvisioner:
    """Create (or re-enable) a CDP API key via the Coinbase UI."""

    def __init__(self, tracer: PageTracer):
        self.tracer = tracer
        self._auth = CoinbaseAuthenticator(tracer)

    def provision(self, credentials: Credentials) -> tuple[str, str]:
        """Log in and create a new API key.

        Returns:
            (api_key, api_secret) where api_key is the full
            ``organizations/.../apiKeys/...`` path and
            api_secret is the EC private key in PEM format.
        """
        with self._auth.login(credentials) as page:
            return self._create_api_key(page)

    def try_reenable(
        self,
        credentials: Credentials,
        api_key: str,
    ) -> bool:
        """Re-enable a disabled key on the management page.

        Returns True if the toggle was flipped back on.
        """
        with self._auth.login(credentials) as page:
            return self._reenable_key(page, api_key)

    def _reenable_key(self, page: Page, api_key: str) -> bool:
        """Find *api_key* in the table and flip its toggle on."""
        page.goto(
            CoinbasePages.SETTINGS_API,
            wait_until="domcontentloaded",
        )
        human_delay(page, 2.0, 4.0)
        page.wait_for_selector(
            '[data-testid="api-keys-management-screen"]',
            timeout=30000,
        )
        self.tracer.save(page, "reenable_management_page")

        short_id = api_key.rsplit("/", 1)[-1][:4]

        # Walk every toggle row
        idx = 0
        while True:
            tid = f"cloud-body-cell-toggleActive-{idx}"
            toggle = page.locator(f'[data-testid="{tid}"]')
            if not toggle.count():
                break

            # Check whether this row belongs to our key by
            # looking at the key-id cell in the same row.
            row = page.locator(f'[data-testid="cloud-key-row-{idx}"]')
            row_text = row.inner_text()
            if short_id in row_text:
                checked = toggle.get_attribute("aria-checked")
                if checked == "false":
                    logger.info(
                        "Key {} is disabled — re-enabling",
                        short_id,
                    )
                    toggle.click()

                    # Toggling requires 2FA confirmation
                    self._handle_2fa(page)
                    page.wait_for_timeout(2000)
                    self.tracer.save(page, "key_reenabled")
                    return True
                logger.info("Key {} is already enabled", short_id)
                return False
            idx += 1

        logger.warning("Could not find key {} in the table", short_id)
        return False

    def _create_api_key(self, page: Page) -> tuple[str, str]:
        # 1. Navigate to API key management page
        logger.info("Navigating to API key management page...")
        page.goto(
            CoinbasePages.SETTINGS_API,
            wait_until="domcontentloaded",
        )
        human_delay(page, 2.0, 4.0)
        page.wait_for_selector(
            '[data-testid="api-keys-management-screen"]',
            timeout=30000,
        )
        self.tracer.save(page, "api_management_page")

        logger.info("Opening create-key modal...")
        human_delay(page)
        page.click('[data-testid="cloud-keys-create-cta"]')
        page.wait_for_selector('[data-testid="cloud-create-modal"]', timeout=15000)
        self.tracer.save(page, "create_modal_open")
        human_delay(page)

        logger.info("Filling key name: {}", _KEY_PREFIX)
        page.fill('[data-testid="create-step-name-input"]', _KEY_PREFIX)
        human_delay(page, 0.5, 1.5)

        modal = page.locator('[data-testid="cloud-create-modal"]')
        logger.info("Selecting portfolio...")
        modal.locator('button[aria-haspopup="listbox"]').click()
        human_delay(page, 0.5, 1.0)
        page.locator('[role="option"]').first.click()
        self.tracer.save(page, "portfolio_selected")
        human_delay(page, 0.5, 1.5)

        transfer = modal.locator("text=Transfer (initiate transfer of funds)")
        if transfer.is_visible():
            transfer.click()
            logger.info("Transfer permission enabled")
        self.tracer.save(page, "permissions_set")
        human_delay(page)

        logger.info("Submitting key creation...")
        submit = modal.locator('button[data-variant="primary"]').last
        submit.wait_for(state="attached", timeout=5000)
        page.wait_for_function(
            """() => {
                const btns = document.querySelectorAll(
                    '[data-testid="cloud-create-modal"]'
                    + ' button[data-variant="primary"]'
                );
                const btn = btns[btns.length - 1];
                return btn && !btn.disabled;
            }""",
            timeout=10000,
        )
        submit.click()

        self._handle_2fa(page)

        return self._extract_credentials(page)

    def _handle_2fa(self, page: Page) -> None:
        """Walk through the two-factor verification screens."""
        logger.info("Waiting for 2FA prompt...")
        page.wait_for_selector(
            '[data-testid="step-twoFactorDetails-active"], '
            '[data-testid="passkey-auth"], '
            '[data-testid="code-inputs-container"]',
            timeout=30000,
        )
        self.tracer.save(page, "2fa_prompt")
        human_delay(page)

        if page.locator('[data-testid="step-twoFactorDetails-active"]').is_visible():
            logger.info("Clicking 'Complete 2FA' button...")
            human_delay(page, 0.5, 1.5)
            page.locator(
                '[data-testid="step-twoFactorDetails-active"] '
                "button[data-variant='primary']"
            ).click()

        page.wait_for_selector(
            '[data-testid="two-factor-button-TOTP"], '
            '[data-testid="code-inputs-container"]',
            timeout=30000,
        )
        self.tracer.save(page, "2fa_method_selection")

        if page.locator('[data-testid="two-factor-button-TOTP"]').is_visible():
            logger.info("Selecting TOTP method")
            human_delay(page, 0.5, 1.5)
            page.click('[data-testid="two-factor-button-TOTP"]')

        page.wait_for_selector(
            '[data-testid="code-inputs-container"]',
            timeout=30000,
        )
        self.tracer.save(page, "totp_input")

        totp = input("[?] TOTP code for API key creation: ").strip()

        page.click("#one-time-code")
        for digit in totp:
            page.keyboard.type(digit)
            page.wait_for_timeout(60)

        logger.info("TOTP submitted — waiting for key generation...")
        self.tracer.save(page, "totp_submitted")
        human_delay(page, 2.0, 4.0)

    def _extract_credentials(self, page: Page) -> tuple[str, str]:
        """Read the API key name and PEM from the success modal."""
        page.wait_for_selector('[data-testid="step-success-active"]', timeout=60000)
        self.tracer.save(page, "key_created_success")
        logger.info("API key created successfully")

        success = page.locator('[data-testid="step-success-active"]')

        api_key = (
            success.locator("p").filter(has_text="organizations/").inner_text().strip()
        )
        logger.info("API key name: {}", api_key)

        raw_secret = (
            success.locator("p")
            .filter(has_text="BEGIN EC PRIVATE KEY")
            .inner_text()
            .strip()
        )
        api_secret = raw_secret.replace("\\n", "\n")

        success.locator("button[data-variant='primary']").click()
        page.wait_for_timeout(1000)
        self.tracer.save(page, "key_modal_dismissed")

        return api_key, api_secret
