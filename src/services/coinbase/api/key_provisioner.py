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

        If a key named ``ZEROHASH_API_KEY`` already exists it is
        deleted first so the creation step never collides.

        Returns:
            (api_key, api_secret) where api_key is the full
            ``organizations/.../apiKeys/...`` path and
            api_secret is the EC private key in PEM format.
        """
        with self._auth.login(credentials) as page:
            self._navigate_to_api_page(page)
            self._delete_existing_key(page)
            return self._create_api_key(page)

    def _navigate_to_api_page(self, page: Page) -> None:
        """Navigate to the Coinbase API key management page."""
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

    def _delete_existing_key(self, page: Page) -> None:
        """Delete the existing API key named ``_KEY_PREFIX`` if present.

        The Coinbase UI flow is:
        1. Click the "Gerenciar" (Manage) button on the key row
           (``data-testid="cloud-manage-cell-0"``).
        2. Click "Excluir chave" (Delete key) in the dropdown.
        3. A confirmation modal appears showing the API Key ID.
        4. Copy the displayed ID into the confirmation input.
        5. Click "Excluir" (Delete) — enabled only after the ID matches.
        """
        screen = page.locator('[data-testid="api-keys-management-screen"]')
        existing = screen.locator(f"text={_KEY_PREFIX}").first

        if not existing.is_visible(timeout=3000):
            logger.info(
                "No existing key named '{}' found — skipping deletion", _KEY_PREFIX
            )
            return

        logger.info("Found existing key '{}' — deleting...", _KEY_PREFIX)

        # 1. Click the "Gerenciar" (Manage) button using its data-testid
        manage_btn = page.locator('[data-testid="cloud-manage-cell-0"]')
        manage_btn.wait_for(state="visible", timeout=5000)
        manage_btn.click()
        human_delay(page, 0.5, 1.5)
        self.tracer.save(page, "manage_dropdown_open")

        # 2. Click "Excluir chave" (Delete key) from the dropdown
        delete_option = page.get_by_text("Excluir chave").or_(
            page.get_by_text("Delete key")
        )
        delete_option.wait_for(state="visible", timeout=5000)
        delete_option.click()
        human_delay(page)
        self.tracer.save(page, "delete_modal_open")

        # 3. Extract the API Key ID from the confirmation modal
        #    (data-testid="cloud-delete-modal").
        #    The UUID is shown in a <p> tag inside the modal.
        modal = page.locator('[data-testid="cloud-delete-modal"]')
        modal.wait_for(state="visible", timeout=10000)

        key_id = (
            modal.locator(
                "text=/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
            )
            .first.inner_text()
            .strip()
        )
        logger.info("API Key ID to confirm deletion: {}", key_id)

        # 4. Paste the ID into the confirmation input
        #    (data-testid="delete-step-api-confirmation")
        confirm_input = page.locator('[data-testid="delete-step-api-confirmation"]')
        confirm_input.wait_for(state="visible", timeout=5000)
        confirm_input.fill(key_id)
        human_delay(page, 0.5, 1.0)
        self.tracer.save(page, "delete_id_confirmed")

        # 5. Click "Excluir" (Delete) button — now enabled after ID fill
        delete_btn = modal.locator(
            'button:has-text("Excluir"), button:has-text("Delete")'
        ).last
        delete_btn.wait_for(state="visible", timeout=5000)
        delete_btn.click()
        human_delay(page, 2.0, 4.0)
        self.tracer.save(page, "key_deleted")

        # Wait for the management screen to reload without the deleted key
        page.wait_for_selector(
            '[data-testid="api-keys-management-screen"]',
            timeout=30000,
        )
        human_delay(page, 2.0, 4.0)
        logger.info("Existing key '{}' deleted successfully", _KEY_PREFIX)

    def _create_api_key(self, page: Page) -> tuple[str, str]:
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
