from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class Credentials:
    email: str
    password: str
    state_file_path: Path


@dataclass
class CryptoNetworkAddress:
    address: str
    tag: str | None = None


@dataclass
class CryptoNetworkFee:
    fee: Decimal
    fee_percentage: Decimal


@dataclass(order=True)
class CryptoNetwork:
    name: str
    sort_weight: int
    confirmations: int
    confirmation_time: str
    minimum_amount: Decimal | None
    maximum_amount: Decimal | None
    fee: CryptoNetworkFee | None = None

    def __post_init__(self):
        self.sort_index = self.sort_weight

@dataclass
class CryptoBalance:
    value: Decimal
    usd_value: Decimal

    def __str__(self):
        return f"{self.value}: {self.usd_value} USD"

@dataclass(init=False)
class CryptoAsset:
    def __init__(
        self,
        name: str,
        asset: str,
        market_cap_rank: int = 999_999,
        balance: CryptoBalance | None = None,
    ):
        self.name = name
        self.asset = asset
        self.balance = balance
        self.market_cap_rank = market_cap_rank

    def __lt__(self, other: "CryptoAsset") -> bool:
        if self.balance and other.balance:
            if self.balance.usd_value != self.balance.usd_value:
                return self.balance.usd_value > self.balance.usd_value

        return self.market_cap_rank < other.market_cap_rank

    def __gt__(self, other: "CryptoAsset") -> bool:
        if self.balance and other.balance:
            if self.balance.usd_value != self.balance.usd_value:
                return self.balance.usd_value < self.balance.usd_value

        return self.market_cap_rank > other.market_cap_rank

    def __str__(self):
        return f"{self.name} ({self.asset})"
