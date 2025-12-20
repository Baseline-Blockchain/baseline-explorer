from __future__ import annotations

import os
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for

from helpers import format_amount, format_timestamp, human_delta
from rpc_client import CONFIG, RPCError, RPC_URL, rpc_call
from services import expand_transaction, fetch_recent_blocks

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("EXPLORER_SECRET_KEY", "baseline-explorer")


@app.context_processor
def inject_helpers() -> dict[str, Any]:
    return {
        "format_amount": format_amount,
        "format_timestamp": format_timestamp,
        "human_delta": human_delta,
        "network_name": CONFIG["display"]["network_name"],
        "RPC_URL": RPC_URL,
    }


@app.route("/")
def index() -> str:
    chain_info = rpc_call("getblockchaininfo")
    mempool_info = rpc_call("getmempoolinfo")
    net_totals = rpc_call("getnettotals")
    latest_height = chain_info["blocks"]
    recent = CONFIG["display"]["recent_blocks"]
    blocks = fetch_recent_blocks(latest_height, recent)
    return render_template(
        "index.html",
        chain=chain_info,
        mempool=mempool_info,
        net=net_totals,
        blocks=blocks,
    )


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
        balance = rpc_call("getaddressbalance", [{"addresses": [address]}])
        utxos = rpc_call("getaddressutxos", [{"addresses": [address]}])
        tx_refs = rpc_call(
            "getaddresstxids",
            [{"addresses": [address], "include_height": True}],
        )
    except RPCError:
        abort(404, f"Unknown address {address}")
    normalized: list[dict[str, Any]] = []
    for ref in tx_refs:
        if isinstance(ref, dict):
            normalized.append(ref)
        else:
            normalized.append({"txid": ref, "height": None, "blockhash": None})
    history: list[dict[str, Any]] = []
    limit = CONFIG["display"]["address_history"]
    for ref in normalized[-limit:][::-1]:
        txid = ref["txid"]
        blockhash = ref.get("blockhash")
        try:
            tx = expand_transaction(txid, block_hash=blockhash)
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
    total_utxo = sum(entry["liners"] for entry in utxos)
    return render_template(
        "address.html",
        address=address,
        balance=balance,
        utxos=utxos,
        history=history,
        total_utxo=total_utxo,
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
