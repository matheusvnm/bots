import json
from pathlib import Path

from loguru import logger

from services.coinbase.api.client import CoinbaseApiClient


class CoinbaseApiCredentialStore:
    """Manage CDP API key + secret persisted as a JSON file."""

    _FILE_NAME = "api_credentials.json"

    def __init__(self, base_dir: Path):
        """
        Args:
            base_dir: directory for this user, e.g.
                      .context/coinbase-api/user/001/
        """
        self._path = base_dir / self._FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[str, str] | tuple[None, None]:
        """Read credentials from disk.

        Returns:
            (api_key, api_secret) or None if missing / malformed.
        """
        if not self._path.exists():
            return (None, None,)

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            key = data.get("api_key", "")
            secret = data.get("api_secret", "")
            if key and secret:
                return key, secret
            logger.warning("Credential file exists but is incomplete")
            return (None, None,)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read credentials: {}", exc)
            return (None, None,)

    def save(self, api_key: str, api_secret: str) -> None:
        """Persist credentials to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "api_key": api_key,
            "api_secret": api_secret,
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.info("API credentials saved to {}", self._path)

    @staticmethod
    def validate(api_key: str, api_secret: str) -> bool:
        """Make a lightweight API call to check the key."""
        try:
            client = CoinbaseApiClient(api_key=api_key, api_secret=api_secret)
            client.get("/v2/accounts", params={"limit": "1"})
            return True
        except Exception as exc:
            logger.warning("API key validation failed: {}", exc)
            return False

    def get_or_provision(self, tracer, credentials) -> tuple[str, str]:
        """Return valid credentials, provisioning if necessary.

        Args:
            tracer:      PageTracer for screenshot/HTML capture.
            credentials: Credentials DTO (email, password,
                         state_file_path).

        Returns:
            (api_key, api_secret) ready to use.
        """
        from services.coinbase.api.key_provisioner import (
            CoinbaseApiKeyProvisioner,
        )
        provisioner = CoinbaseApiKeyProvisioner(tracer=tracer)

        logger.info("Searching for the API Key")
        api_key, api_secret = self.load()
        if not (api_key and api_secret):
            logger.info("The API keys were not found for this user. First login.")
            api_key, api_secret = provisioner.provision(credentials)
            self.save(api_key, api_secret)
            return api_key, api_secret

        logger.info("Found stored API credentials — validating...")
        if self.validate(api_key, api_secret):
            logger.info("Stored API key is valid")
            return api_key, api_secret

        logger.warning("Stored API key is invalid — attempting to re-create...")
        api_key, api_secret = provisioner.provision(credentials)
        self.save(api_key, api_secret)
        return api_key, api_secret
