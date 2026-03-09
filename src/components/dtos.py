from dataclasses import dataclass


@dataclass
class KrakenCredentials:
    email: str
    password: str
    device_cookie_path: str


@dataclass
class CryptoAsset:
    name: str
    value: str | None
    short_name: str | None
    usd_value: str | None
