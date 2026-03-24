# Coinbase Integration — Overview

> **Audience:** Legal and Compliance team
> **Purpose:** Describe how our application interacts with Coinbase, what user data it handles, and what disclosures may be required.

## Two Integration Approaches

Our application connects to Coinbase in two ways. Both let users check balances, generate deposit addresses, and (in the API approach) withdraw funds. They differ in *how* they communicate with Coinbase.

| | API Integration | Browser Integration |
|---|---|---|
| **Communication method** | REST API calls authenticated with signed tokens (JWT) | Automated browser that navigates Coinbase's website |
| **Supported actions** | Balance, Deposit, Withdraw | Balance, Deposit (Withdraw not yet implemented) |
| **Initial setup** | Requires creating an API key (done once via browser) | Requires browser login each session (or cached session) |
| **Ongoing authentication** | Signed JWT per request — no browser needed | Full browser login (email, password, 2FA) |
| **User inputs during operation** | Asset selection; for withdrawals: address, amount, travel rule data | Asset selection |
| **Detailed documentation** | [API Integration](coinbase-api-integration.md) | [Browser Integration](coinbase-browser-integration.md) |

## User Data Collected

Both approaches require the user's Coinbase **email** and **password**, received via API call or message from the calling system. The table below lists every piece of user-provided data and when it is requested.

| Data                                                      | When Collected                                          | Used By  |
| --------------------------------------------------------- | ------------------------------------------------------- | -------- |
| Email address                                             | Received via API call / message at operation start      | Both     |
| Password                                                  | Received via API call / message at operation start      | Both     |
| TOTP code (6-digit authenticator code)                    | Login and API key creation                              | Both     |
| Device verification link (from email)                     | First login on a new device                             | Both     |
| Asset selection                                           | Each deposit or withdraw operation                      | Both     |
| Destination wallet address                                | Withdraw                                                | API only |
| Amount to send                                            | Withdraw                                                | API only |
| Travel rule data (beneficiary name, country, wallet type, exchange name) | Withdraw                                                | API only |

## Data Exchanged with Coinbase

### Data sent to Coinbase

- **Credentials:** Email, password, and 2FA codes are submitted through Coinbase's login forms (browser-based).
- **API requests:** Balance queries, deposit address creation, and withdrawal instructions are sent as authenticated REST calls to `api.coinbase.com`.
- **Withdrawal payloads:** Include recipient address, amount, currency, network, and travel rule data (beneficiary identity and wallet type).

### Data received from Coinbase

- **Account information:** Currency codes, account names, and balances.
- **Deposit addresses:** Blockchain addresses and associated network identifiers.
- **Transaction status:** Confirmation of withdrawal submission (transaction ID and status).

## Security and Privacy Considerations

### Credential Handling

- **Email and password** are received at runtime via API calls or messages from the calling system. They are held in memory only for the duration of the operation and are not persisted to disk.
- **API keys** (key identifier + EC private key) are stored in a database, encrypted using cloud-provider-managed encryption keys (e.g., AWS KMS, GCP Cloud KMS). They are never stored as plaintext on disk.
- **Browser sessions** are kept in memory for a few minutes — only long enough to complete the requested operation — then discarded. No session cookies are persisted to disk in production.
- **API key provisioning sessions** (used only during one-time key creation) are kept for 1–5 minutes, then discarded.

### Authentication Mechanisms

| Mechanism                        | Purpose                                            |
| -------------------------------- | -------------------------------------------------- |
| Email + password                 | Coinbase account login                             |
| TOTP (authenticator app)         | Two-factor authentication                          |
| SMS code (fallback)              | Two-factor authentication when TOTP unavailable    |
| Device verification (email link) | New-device confirmation required by Coinbase       |
| JWT signed with EC private key   | Per-request API authentication (API approach only) |

### Disclosure Considerations

The following points may require user-facing disclosures:

1. **Credential handling:** The application receives the user's Coinbase email and password via API calls or messages at runtime. These credentials are held in memory during the operation and are not persisted to disk.
2. **Session lifetime:** Browser sessions exist in memory for a few minutes during operation, then are discarded. No persistent session cookies are written to disk in production.
3. **API key permissions:** The provisioned API key includes "Transfer" permission, which authorizes fund movements. Users should understand this grants the application the ability to send funds on their behalf.
4. **API key storage:** API keys are stored in a database encrypted with cloud-provider-managed keys. Users should understand that the application retains long-lived API credentials on their behalf.
5. **Travel rule data collection:** Withdrawals require the user to provide beneficiary identity information (name, country, wallet type). This data is transmitted to Coinbase as part of regulatory compliance (Travel Rule).
6. **Browser automation:** The browser integration controls a real browser instance. The user's credentials are entered programmatically into Coinbase's login forms during the session.
7. **Network logging:** The application logs HTTP request and response bodies for debugging. These logs may contain session tokens, account data, and transaction details.
8. **Clipboard access:** The browser integration reads the system clipboard to retrieve deposit addresses. No data is written to the clipboard by the application.

### Data Retention

- **Browser sessions:** Held in memory for a few minutes during operation, then discarded. Not written to disk in production.
- **API key provisioning sessions:** Held in memory for 1–5 minutes during one-time key creation, then discarded.
- **API keys:** Stored encrypted in the database until the user revokes access or the key is deleted. Encryption uses cloud-provider-managed keys.
- **Debug logs:** Written to `logs/` for operational monitoring. May contain session tokens and account data; should be managed with appropriate retention policies.

## Open Questions

The following items are not yet finalized across both approaches. See individual documents for details.

| # | Question | Affects |
|---|---|---|
| 1 | **How should the blockchain network be determined?** Deposits and withdrawals require a network (e.g., Ethereum, Solana, Base). The selection method is not yet defined — options include defaulting to the native network, letting the calling system specify it, or inferring from context. | Both |
| 2 | **What happens if automatic network selection fails?** Should the operation fail, or should the system request clarification from the caller? | Both |
| 3 | **How should exchange names be mapped to VASP IDs?** For withdrawals to exchange wallets, the user provides an exchange name (e.g., "Binance", "Kraken"). The application must map this to the VASP ID required by Coinbase's Travel Rule API. A full list of exchanges exists but the mapping logic is not yet implemented. | API only |

---

For detailed flow diagrams and step-by-step descriptions, see:

- [API Integration](coinbase-api-integration.md)
- [Browser Integration](coinbase-browser-integration.md)
