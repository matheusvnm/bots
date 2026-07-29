import json
import sys
sys.path.insert(0, '/Users/smmarques/Github/scrapping/bots')

from scripts.client import CoinbaseApiClient

networks = CoinbaseApiClient.get_supported_networks("USDC")
print(json.dumps(networks, indent=2))
