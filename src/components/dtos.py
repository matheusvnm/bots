from dataclasses import dataclass


@dataclass
class KrakenCredentials:
    email: str
    password: str
    device_cookie_path: str
