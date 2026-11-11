Replace this directory with an existing testnet wallet folder (not a new wallet).
Expected keystore: wallet/keystore.json; optional wallet/address.txt and wallet/.password.
Never commit keystore.json, .password, private keys or recovery phrases. Git ignores them.
On Render, use /var/data/wallet/keystore.json and WALLET_PASSWORD as an env secret.
Simulated settlement needs no private key; Base Sepolia settlement is not yet implemented.
