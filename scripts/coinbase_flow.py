import json
import uuid

from scripts.client import CoinbaseApiClient


VINICIUS_KEY_FIRST_WAVE = "organizations/09e8ce03-eff3-45bc-a4ae-5c7390de8297/apiKeys/a5546208-2b91-41fc-9256-9a3c0c976bc7"
VINICIUS_PRIVATE_KEY_FIRST_WAVE = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIEaI+eUzq77V4cAW5nBeog9MvmztNECRu0w6LmF5lqKxoAoGCCqGSM49\nAwEHoUQDQgAESovzxq7zqjiZ0wnURlBHuUcCLZbMONQ3NTiE8KYVXDcOcbgWCN5O\nFegSRzeU0sDbhrSwOsv6yM9/KPL5CH34TA==\n-----END EC PRIVATE KEY-----\n"

VINICIUS_KEY_SECOND_WAVE = "organizations/09e8ce03-eff3-45bc-a4ae-5c7390de8297/apiKeys/a5546208-2b91-41fc-9256-9a3c0c976bc7"
VINICIUS_PRIVATE_KEY_SECOND_WAVE = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIEaI+eUzq77V4cAW5nBeog9MvmztNECRu0w6LmF5lqKxoAoGCCqGSM49\nAwEHoUQDQgAESovzxq7zqjiZ0wnURlBHuUcCLZbMONQ3NTiE8KYVXDcOcbgWCN5O\nFegSRzeU0sDbhrSwOsv6yM9/KPL5CH34TA==\n-----END EC PRIVATE KEY-----\n"

MAX_KEYS = "organizations/152ee017-0138-424c-b5e1-f272e8aee8ef/apiKeys/2d987ab8-fa93-4b6f-bc08-ecf791169b29"
MAX_PRIVATE_KEY = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIIJK3iencAfXsYAK3UDi/AM5M4njk7WUIBZNxLSsoZw8oAoGCCqGSM49\nAwEHoUQDQgAE6o/KKqaNbVoW2TJWsMVjFGpTgKUS1KlVSpATyg/dUOZ6Jlo9t/r+\nKN4boN93HoclcMtGdy6tccKZiaTQgUFrrw==\n-----END EC PRIVATE KEY-----\n"

PERSONAL_KEYS = "organizations/5cff49c1-a97f-4c00-8aa9-6eadaa6f3422/apiKeys/dde40163-a279-4e95-8139-a62855aa023c"
PERSONAL_PRIVATE_KEY = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIJhBqejb5f9eKmpJ7eJa2n88vSZog3HBolZDeqQG6ZJjoAoGCCqGSM49\nAwEHoUQDQgAEEkLHwNdz/drL4O3ca56wxoi3DGPObJS3nDpEbsljp5O2/LbeooEi\nZlYJyzdSqiN3Ocn8arzNx/TDriUmuL/Dbg==\n-----END EC PRIVATE KEY-----\n"


ZERO_HASH_VASP = "c65e3227-2089-4d8b-ba76-5e2aec4715b3"
KRAKEN_VASP = "b87b7a01-7972-4e8c-a783-3b7ffdf8c016"

ZERO_HASH_ADDRESS_ETHEREUM = "0x34E40BC1d5939F919d59AE78FcB93E7274957890"
PERSONAL_ADDRESS_ETHEREUM = "0x4A81DdF15e10b7eF0249b358f62D6b57D0C89258"

PERSONAL_ADDRESS_SOLANA = "9LFUw6mhsYEuuVDhJVkPbBZX1zn74KVACEqwMZhZUGd2"




ORIGIN_API_KEY = MAX_KEYS
ORIGIN_PRIVATE_KEY = MAX_PRIVATE_KEY
TARGET_VASP = KRAKEN_VASP
TARGET_ADDRESS = ZERO_HASH_ADDRESS_ETHEREUM


TARGET_AMOUNT = "2"
TARGET_COIN = "USDC"
TARGET_NETWORK = "ethereum"


client = CoinbaseApiClient(ORIGIN_API_KEY, ORIGIN_PRIVATE_KEY)

def main():
    print("Listing accounts...")
    accounts = client.list_accounts()
    #print(json.dumps(accounts, indent=2))

    usdc_account = next((a for a in accounts if a["balance"]["currency"] == "USDC"), None)
    if not usdc_account:
        print("No USDC account found")
        return  

    print(f"Sending {TARGET_AMOUNT} {TARGET_COIN} to {TARGET_ADDRESS} on {TARGET_NETWORK}...")

    travel_rule_data = {
        "is_self": "IS_SELF_FALSE",
        "beneficiary_wallet_type": "WALLET_TYPE_EXCHANGE",
        "beneficiary_name": "Sandro Matheus Vila Nova Marques",
        "beneficiary_address": {"country": "BR"},
        "beneficiary_financial_institution": TARGET_VASP,
        "transfer_purpose": "TRANSFER_PURPOSE_OTHER_SERVICES",
    }

    response = client.send_money(
        account_id=usdc_account["id"],
        to=TARGET_ADDRESS,
        amount=TARGET_AMOUNT,
        currency=TARGET_COIN,
        network=TARGET_NETWORK,
        idem=str(uuid.uuid4()),
        #travel_rule_data=travel_rule_data
    )
    print("Response:")
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()


