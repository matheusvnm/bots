import json
import sys
sys.path.insert(0, '/Users/smmarques/Github/scrapping/bots')

from scripts.client import CoinbaseApiClient

ORIGIN_API_KEY = "organizations/09e8ce03-eff3-45bc-a4ae-5c7390de8297/apiKeys/a5546208-2b91-41fc-9256-9a3c0c976bc7"
ORIGIN_PRIVATE_KEY = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIEaI+eUzq77V4cAW5nBeog9MvmztNECRu0w6LmF5lqKxoAoGCCqGSM49\nAwEHoUQDQgAESovzxq7zqjiZ0wnURlBHuUcCLZbMONQ3NTiE8KYVXDcOcbgWCN5O\nFegSRzeU0sDbhrSwOsv6yM9/KPL5CH34TA==\n-----END EC PRIVATE KEY-----\n"

client = CoinbaseApiClient(ORIGIN_API_KEY, ORIGIN_PRIVATE_KEY)

accounts = client.list_accounts()
print(json.dumps(accounts, indent=2))
