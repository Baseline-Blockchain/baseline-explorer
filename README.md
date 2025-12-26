## Baseline Explorer

A lightweight Flask UI that hits a local Baseline node via JSON-RPC to show:

- Latest blocks, per-block details, and live mempool stats
- Paginated Recent Transactions view with block links, sizes, fees, and confirmations
- Rich List powered by the node RPC `getrichlist`
- Transaction pages (with decoded inputs/outputs and granular fees)
- Dedicated scheduled send list plus address balance/history/UTXO views and a universal search box

### Quick start

1. Confirm Python 3.11+ is on your path (`python --version` should succeed).
2. Ensure a Baseline node is running on `127.0.0.1:8832`; update `config.json` only if your RPC host/port differ.
3. Install deps (one-time): `python -m pip install -r requirements.txt`
4. Launch the explorer from this folder:
   ```bash
   python app.py
   ```
5. Open http://127.0.0.1:5000 to browse your chain.

Read-only RPC methods power this read-only UI, so credentials can stay empty unless your node rejects unauthenticated reads.
