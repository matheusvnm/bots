import json
import uuid
import sys
sys.path.insert(0, '/Users/smmarques/Github/scrapping/bots')

from scripts.client import CoinbaseApiClient

ORIGIN_API_KEY = "organizations/09e8ce03-eff3-45bc-a4ae-5c7390de8297/apiKeys/a5546208-2b91-41fc-9256-9a3c0c976bc7"
ORIGIN_PRIVATE_KEY = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIEaI+eUzq77V4cAW5nBeog9MvmztNECRu0w6LmF5lqKxoAoGCCqGSM49\nAwEHoUQDQgAESovzxq7zqjiZ0wnURlBHuUcCLZbMONQ3NTiE8KYVXDcOcbgWCN5O\nFegSRzeU0sDbhrSwOsv6yM9/KPL5CH34TA==\n-----END EC PRIVATE KEY-----\n"

TARGET_ADDRESS = "0x34E40BC1d5939F919d59AE78FcB93E7274957890"
TARGET_AMOUNT = "1"
TARGET_COIN = "USDC"
TARGET_NETWORK = "ethereum"

client = CoinbaseApiClient(ORIGIN_API_KEY, ORIGIN_PRIVATE_KEY)

accounts = client.list_accounts()
usdc_account = next((a for a in accounts if a["balance"]["currency"] == "USDC"), None)

if not usdc_account:
    print("No USDC account found")
else:
    # Try claiming it's a self-hosted wallet
    travel_rule_data = {
        "beneficiary_wallet_type": "WALLET_TYPE_SELF_HOSTED",
        "is_self": "IS_SELF_TRUE",
        "beneficiary_name": "My Wallet",
        "beneficiary_country": "US",
        "transfer_purpose": "TRANSFER_PURPOSE_OTHER_SERVICES",
        "relationship_with_beneficiary": "Self",
    }

    print(f"Testing WITH self-hosted wallet travel rule data...")
    response = client.send_money(
        account_id=usdc_account["id"],
        to=TARGET_ADDRESS,
        amount=TARGET_AMOUNT,
        currency=TARGET_COIN,
        network=TARGET_NETWORK,
        idem=str(uuid.uuid4()),
        travel_rule_data=travel_rule_data,
    )
    print("Response:")
    print(json.dumps(response, indent=2))
