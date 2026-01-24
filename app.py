from __future__ import annotations

import os
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for

from helpers import format_amount, format_amount_us, format_hashrate, format_timestamp, human_delta
from rpc_client import CONFIG, RPCError, RPC_URL, rpc_call
from services import (
    expand_transaction,
    fetch_chain_tips,
    fetch_mempool_stats,
    fetch_recent_blocks,
    fetch_recent_transactions,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("EXPLORER_SECRET_KEY", "baseline-explorer")


@app.context_processor
def inject_helpers() -> dict[str, Any]:
    return {
        "format_amount": format_amount,
        "format_amount_us": format_amount_us,
        "format_hashrate": format_hashrate,
        "format_timestamp": format_timestamp,
        "human_delta": human_delta,
        "network_name": CONFIG["display"]["network_name"],
        "RPC_URL": RPC_URL,
    }


@app.route("/")
def index() -> str:
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    chain_info = rpc_call("getblockchaininfo")
    mempool_info = rpc_call("getmempoolinfo")
    mining_info = rpc_call("getmininginfo")
    supply_info = None
    supply_error = None
    try:
        supply_info = rpc_call("getcirculatingsupply")
    except RPCError as exc:
        app.logger.warning("Unable to fetch supply info: %s", exc)
        supply_error = str(exc)
    latest_height = chain_info["blocks"]
    per_page = max(1, int(CONFIG["display"].get("blocks_per_page", CONFIG["display"]["recent_blocks"])))
    offset = (page - 1) * per_page
    paged_latest = latest_height - offset
    blocks = fetch_recent_blocks(paged_latest, per_page) if paged_latest >= 0 else []
    has_next = (latest_height - (offset + per_page)) >= 0
    return render_template(
        "index.html",
        chain=chain_info,
        mempool=mempool_info,
        mining=mining_info,
        supply=supply_info,
        supply_error=supply_error,
        blocks=blocks,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        per_page=per_page,
    )


@app.route("/transactions")
def transactions() -> str:
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    per_page = CONFIG["display"]["transactions_per_page"]
    offset = (page - 1) * per_page
    latest_height = rpc_call("getblockchaininfo")["blocks"]
    raw_transactions = fetch_recent_transactions(latest_height, per_page + 1, offset)
    has_next = len(raw_transactions) > per_page
    return render_template(
        "transactions.html",
        transactions=raw_transactions[:per_page],
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        per_page=per_page,
    )


@app.route("/richlist")
def richlist() -> str:
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    per_page = CONFIG["display"].get("rich_list_per_page", 25)
    per_page = max(1, int(per_page))
    offset = (page - 1) * per_page
    error = None
    try:
        rpc_entries = rpc_call("getrichlist", [per_page + 1, offset])
    except RPCError as exc:
        app.logger.warning("Unable to fetch rich list: %s", exc)
        rpc_entries = []
        error = str(exc)
    has_next = len(rpc_entries) > per_page
    rich_rows = []
    for idx, entry in enumerate(rpc_entries[:per_page], start=offset + 1):
        address = entry.get("address") if isinstance(entry, dict) else None
        balance = None
        if isinstance(entry, dict):
            balance = entry.get("balance_liners")
        rich_rows.append({"rank": idx, "address": address, "balance": balance})
    return render_template(
        "richlist.html",
        richlist=rich_rows,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        per_page=per_page,
        error=error,
    )


@app.route("/orphans")
def orphans() -> str:
    tips = fetch_chain_tips()
    return render_template("orphans.html", tips=tips)


@app.route("/mempool")
def mempool() -> str:
    stats = fetch_mempool_stats()
    return render_template("mempool.html", stats=stats)


@app.route("/block/<block_hash>")
def block_detail(block_hash: str) -> str:
    try:
        block = rpc_call("getblock", [block_hash, True])
    except RPCError:
        abort(404, f"Unknown block {block_hash}")
    block["time_human"] = format_timestamp(block["time"])
    block["age"] = human_delta(block["time"])
    transactions = block.get("tx", [])
    return render_template("block.html", block=block, transactions=transactions)


@app.route("/block-height/<int:height>")
def block_by_height(height: int) -> str:
    try:
        block_hash = rpc_call("getblockhash", [height])
    except RPCError:
        abort(404, f"Unknown block height {height}")
    return redirect(url_for("block_detail", block_hash=block_hash))


@app.route("/tx/<txid>")
def tx_detail(txid: str) -> str:
    block_hint = request.args.get("block")
    try:
        tx = expand_transaction(txid, block_hash=block_hint)
    except RPCError:
        abort(404, f"Unknown transaction {txid}")
    best = rpc_call("getblockchaininfo")
    confirmations = tx.get("confirmations", 0)
    block_hash = tx.get("blockhash")
    fee_liners = tx.get("fee_liners")
    if fee_liners is None:
        fee = tx.get("fee")
        fee_liners = int(round(float(fee) * 100_000_000)) if fee is not None else 0
    fee_bline = fee_liners / 100_000_000
    return render_template(
        "tx.html",
        tx=tx,
        block_hash=block_hash or block_hint,
        confirmations=confirmations,
        tip_height=best["blocks"],
        fee_liners=fee_liners,
        fee_bline=fee_bline,
    )


@app.route("/address/<address>")
def address_detail(address: str) -> str:
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    per_page = max(
        1, int(CONFIG["display"].get("address_per_page", CONFIG["display"]["address_history"]))
    )
    try:
        balance = rpc_call("getaddressbalance", [{"addresses": [address]}])
        # Page txids directly: only fetch what we need (+1 to detect next page)
        limit = per_page + 1
        offset = (page - 1) * per_page
        tx_refs: list[Any] = rpc_call(
            "getaddresstxids",
            [{"addresses": [address], "include_height": True, "limit": limit, "offset": offset}],
        )
    except RPCError:
        abort(404, f"Unknown address {address}")
    # Normalize only the fetched page window
    normalized: list[dict[str, Any]] = []
    for ref in tx_refs:
        if isinstance(ref, dict):
            normalized.append(ref)
        else:
            normalized.append({"txid": ref, "height": None, "blockhash": None})

    # We fetched per_page + 1 to detect next page
    has_next = len(normalized) > per_page
    window = normalized[:per_page]
    history: list[dict[str, Any]] = []
    tx_cache: dict[tuple[str, str | None], dict[str, Any]] = {}
    for ref in window:
        txid = ref["txid"]
        blockhash = ref.get("blockhash")
        try:
            tx = expand_transaction(txid, block_hash=blockhash, tx_cache=tx_cache)
        except RPCError as exc:
            app.logger.warning("Skipping tx %s for %s: %s", txid, address, exc)
            continue
        received = sum(vout.value for vout in tx["decoded_outputs"] if vout.address == address)
        sent = sum(vin.value or 0 for vin in tx["decoded_inputs"] if vin.address == address and vin.value)
        entry_block = tx.get("blockhash") or blockhash
        history.append(
            {
                "txid": txid,
                "blockhash": entry_block,
                "time": tx.get("time"),
                "received": received,
                "sent": sent,
                "net": received - sent,
                "confirmations": tx.get("confirmations", 0),
            }
        )
    return render_template(
        "address.html",
        address=address,
        balance=balance,
        history=history,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        per_page=per_page,
    )


@app.route("/search", methods=["POST"])
def search() -> Any:
    query = request.form.get("query", "").strip()
    if not query:
        return redirect(url_for("index"))
    if query.isdigit():
        return redirect(url_for("block_by_height", height=int(query)))
    if len(query) == 64:
        try:
            rpc_call("getblock", [query, True])
            return redirect(url_for("block_detail", block_hash=query))
        except RPCError:
            pass
        try:
            expand_transaction(query)
            return redirect(url_for("tx_detail", txid=query))
        except RPCError:
            pass
    return redirect(url_for("address_detail", address=query))


@app.errorhandler(RPCError)
def handle_rpc_error(exc: RPCError):
    return render_template("error.html", message=str(exc)), 500


@app.errorhandler(404)
def handle_not_found(exc):
    return render_template("error.html", message=str(exc)), 404


def main() -> None:
    print(f"Baseline Explorer listening on http://127.0.0.1:5000 (RPC target {RPC_URL})")
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
