# Coinbase API Integration

> **Audience:** Legal and Compliance team
> **Related:** [Overview](README.md) · [Browser Integration](coinbase-browser-integration.md)

## How It Works

The API integration communicates with Coinbase through their official REST API (`api.coinbase.com`). Each request is authenticated with a short-lived signed token (JWT), created using an API key and a private cryptographic key.

The API key must be created once through Coinbase's website (via an automated browser session). After that, all operations — balance checks, deposit address generation, and withdrawals — happen through direct API calls with no browser involved.

## Supported Actions

| Action | Description |
|---|---|
| **Balance** | Lists all cryptocurrency accounts and their balances |
| **Deposit** | Generates a blockchain address for receiving funds |
| **Withdraw** | Sends funds to an external wallet address |

## Flow Diagrams

### API Key Setup (One-Time)

Before the application can make API calls, it needs an API key. This diagram shows how the application obtains one.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant App as Application
    participant B as Browser
    participant CB as Coinbase

    App->>App: Check for stored API key on disk

    alt API key found on disk
        App->>CB: Test API key (GET /v2/accounts)
        alt Key is valid
            App-->>App: Proceed to operation
        else Key is revoked/disabled
            App->>B: Open Coinbase Settings > API page
            B->>CB: Navigate to API settings
            App->>B: Find key and toggle it on
            Note over U,B: ⏳ USER INPUT REQUIRED
            U->>App: Enter TOTP code (authenticator app)
            App->>B: Submit TOTP code
            B->>CB: Confirm re-enable
            alt Re-enable succeeded
                App-->>App: Proceed to operation
            else Re-enable failed
                App->>App: Go to "No key found" flow below
            end
        end
    else No API key found
        Note over App,CB: Browser login required (see Login Flow)
        App->>B: Open Coinbase Settings > API page
        B->>CB: Navigate to API key creation
        App->>B: Fill key name and permissions
        App->>B: Enable "Transfer" permission
        App->>B: Submit creation form
        Note over U,B: ⏳ USER INPUT REQUIRED
        U->>App: Enter TOTP code (authenticator app)
        App->>B: Submit TOTP code
        B-->>App: Return API key + private key
        App->>App: Encrypt and store credentials in database
    end
```

### Balance Check

```mermaid
sequenceDiagram
    autonumber
    participant App as Application
    participant CB as Coinbase API

    App->>App: Load API key from encrypted database
    App->>App: Sign JWT with private key
    loop For each page of results
        App->>CB: GET /v2/accounts (with JWT)
        CB-->>App: Account list (currency, balance)
    end
    App->>App: Filter out fiat and zero-balance accounts
    App-->>App: Display balances
```

### Deposit (Generate Receive Address)

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant App as Application
    participant CB as Coinbase API

    App->>CB: GET /v2/accounts (list all accounts)
    CB-->>App: Account list
    App->>App: Display available assets

    Note over U,App: ⏳ USER INPUT REQUIRED
    U->>App: Select asset (number or ticker)

    App->>App: Determine network automatically (see Open Questions)

    App->>CB: GET /v2/accounts/{id}/addresses
    CB-->>App: Existing addresses

    alt No address for selected network
        App->>CB: POST /v2/accounts/{id}/addresses
        Note right of CB: Body: {"network": "..."}
        CB-->>App: New address + network + deposit URI
    end

    App-->>U: Display deposit address
```

### Withdraw (Send Funds)

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant App as Application
    participant CB as Coinbase API

    App->>CB: GET /v2/accounts (list funded accounts)
    CB-->>App: Account list
    App->>App: Display assets with balances

    Note over U,App: ⏳ USER INPUT REQUIRED
    U->>App: Select asset (number or ticker)

    Note over U,App: ⏳ USER INPUT REQUIRED
    U->>App: Enter destination wallet address

    Note over U,App: ⏳ USER INPUT REQUIRED
    U->>App: Enter amount to send

    App->>App: Determine network automatically (see Open Questions)

    rect rgb(255, 245, 230)
        Note over U,App: ⏳ TRAVEL RULE DATA — USER INPUT REQUIRED
        U->>App: Is this your own wallet? (yes/no)
        U->>App: Wallet type (self-hosted or exchange)
        U->>App: Beneficiary name
        U->>App: Beneficiary country (2-letter code)
        opt If exchange wallet
            U->>App: Exchange name
            App->>App: Map exchange name to VASP ID (internal lookup)
        end
    end

    App->>CB: POST /v2/accounts/{id}/transactions
    Note right of CB: Body includes: type, recipient<br/>address, amount, currency,<br/>network, idempotency key,<br/>and travel rule data

    CB-->>App: Transaction ID + status
    App-->>U: Display transaction confirmation
```

## User Inputs Summary

| Step | Input | Required? |
|---|---|---|
| Key setup | TOTP code (serves as confirmation to proceed) | Yes |
| Deposit | Asset selection | Yes |
| Withdraw | Asset selection | Yes |
| Withdraw | Destination address | Yes |
| Withdraw | Amount | Yes |
| Withdraw | Own wallet? (yes/no) | Yes |
| Withdraw | Wallet type | Yes |
| Withdraw | Beneficiary name | Yes |
| Withdraw | Beneficiary country | Yes |
| Withdraw | Exchange name | Conditional (exchange wallets only) |

## Authentication

Each API request carries a JWT (JSON Web Token) signed with the user's private EC key. The token:

- Expires after 120 seconds
- Contains the target API endpoint as a claim
- Is signed using the ES256 algorithm (Elliptic Curve)

No browser session or cookies are needed for API operations after the initial key setup.

## Security and Privacy Considerations

1. **API key scope:** The key is created with "Transfer" permission, authorizing the application to move funds. A compromised key could allow unauthorized withdrawals.
2. **Key storage:** The API key and private key are stored in a database, encrypted using cloud-provider-managed encryption keys (e.g., AWS KMS, GCP Cloud KMS). They are never stored as plaintext on disk.
3. **Key provisioning session:** The one-time browser session used to create the API key is held in memory for 1–5 minutes, then discarded. No session state is persisted to disk.
4. **Travel rule data:** Withdrawal operations collect personally identifiable information (beneficiary name and country). This data is sent to Coinbase and may be subject to data protection regulations.
5. **Idempotency:** Each withdrawal generates a unique idempotency key to prevent duplicate transactions. However, there is no confirmation prompt before the API call executes.
6. **Browser exposure during setup:** API key creation requires a browser session. During this step, the user's Coinbase credentials and TOTP code are entered through an automated browser — the same security considerations as the Browser Integration apply during setup.

