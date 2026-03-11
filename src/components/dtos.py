from dataclasses import asdict, dataclass, field
from decimal import Decimal


@dataclass
class KrakenCredentials:
    email: str
    password: str
    device_cookie_path: str


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
    addresses: list[CryptoNetworkAddress] = field(default_factory=list)

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
        networks: list[CryptoNetwork] = [],
    ):
        self.name = name
        self.asset = asset
        self.balance = balance
        self.market_cap_rank = market_cap_rank
        self.networks = networks

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

    def as_dict(self) -> dict:
        data = {
            "name": self.name,
            "asset": self.asset,
            "market_cap_rank": self.market_cap_rank,
            "networks": [asdict(network) for network in self.networks]
        }

        if self.balance:
            data["balance"] = asdict(self.balance)

        return data

    def __str__(self):
        return f"{self.name} ({self.asset})"
