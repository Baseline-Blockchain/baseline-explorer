from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from helpers import address_from_script, double_sha256, format_timestamp, human_delta
from rpc_client import CONFIG, RPCError, rpc_call


def fetch_block_by_height(height: int) -> dict[str, Any]:
    block_hash = rpc_call("getblockhash", [height])
    header = rpc_call("getblockheader", [block_hash, True])
    header["height"] = height
    header["hash"] = block_hash
    header["time_human"] = format_timestamp(header["time"])
    header["age"] = human_delta(header["time"])
    # Size/weight are not available from headers; keep placeholders for template compatibility
    header.setdefault("size", None)
    header.setdefault("weight", None)
    return header


def fetch_recent_blocks(latest_height: int, count: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for offset in range(count):
        height = latest_height - offset
        if height < 0:
            break
        blocks.append(fetch_block_by_height(height))
    return blocks


@dataclass
class TxOutput:
    index: int
    value: int
    address: str | None
    script: str


@dataclass
class TxInput:
    txid: str
    vout: int
    value: int | None
    address: str | None
    is_coinbase: bool


@lru_cache(maxsize=512)
def get_transaction(txid: str, block_hash: str | None = None) -> dict[str, Any]:
    params: list[Any] = [txid, True]
    if block_hash:
        params.append(block_hash)
    try:
        return rpc_call("getrawtransaction", params)
    except RPCError:
        if not block_hash:
            raise
        return parse_transaction_from_block(txid, block_hash)


def expand_transaction(
    txid: str, *, block_hash: str | None = None, tx_cache: dict[tuple[str, str | None], dict[str, Any]] | None = None
) -> dict[str, Any]:
    key = (txid, block_hash)
    tx = tx_cache.get(key) if tx_cache is not None else None
    if tx is None:
        tx = get_transaction(txid, block_hash)
        if tx_cache is not None:
            tx_cache[key] = tx
    inputs: list[TxInput] = []
    for vin in tx.get("vin", []):
        if "coinbase" in vin:
            inputs.append(TxInput(txid="coinbase", vout=-1, value=None, address=None, is_coinbase=True))
            continue
        prev_key = (vin["txid"], None)
        prev = tx_cache.get(prev_key) if tx_cache is not None else None
        if prev is None:
            prev = get_transaction(vin["txid"])
            if tx_cache is not None:
                tx_cache[prev_key] = prev
        prev_out = prev["vout"][vin["vout"]] if vin["vout"] < len(prev["vout"]) else {}
        value = prev_out.get("value")
        script = prev_out.get("scriptPubKey", "")
        address = address_from_script(script) if script else None
        inputs.append(
            TxInput(
                txid=vin["txid"],
                vout=vin["vout"],
                value=value,
                address=address,
                is_coinbase=False,
            )
        )
    outputs: list[TxOutput] = []
    for vout in tx.get("vout", []):
        script = vout.get("scriptPubKey", "")
        address = address_from_script(script) if script else None
        outputs.append(
            TxOutput(
                index=vout["n"],
                value=vout["value"],
                address=address,
                script=script,
            )
        )
    tx["decoded_inputs"] = inputs
    tx["decoded_outputs"] = outputs
    return tx


def fetch_recent_transactions(
    latest_height: int, limit: int, offset: int, *, include_mempool: bool = False
) -> list[dict[str, Any]]:
    """Return reversed chronological list of transactions with pagination."""
    transactions: list[dict[str, Any]] = []
    height = latest_height
    while len(transactions) < limit + offset and height >= 0:
        block = fetch_block_by_height(height)
        for txid in block.get("tx", []):
            if len(transactions) >= limit + offset:
                break
            try:
                tx = expand_transaction(txid, block_hash=block["hash"])
            except RPCError:
                continue
            inputs_sum = sum(inp.value or 0 for inp in tx["decoded_inputs"] if inp.value)
            outputs_sum = sum(out.value for out in tx["decoded_outputs"])
            transactions.append(
                {
                    "txid": txid,
                    "block": block,
                    "time": block["time"],
                    "height": height,
                    "confirmations": block.get("confirmations", 0),
                    "size": tx.get("size"),
                    "fee": inputs_sum - outputs_sum if inputs_sum else None,
                    "input_sum": inputs_sum,
                    "output_sum": outputs_sum,
                }
            )
        height -= 1
    return transactions[offset : offset + limit]


def parse_transaction_from_block(txid: str, block_hash: str) -> dict[str, Any]:
    """Fallback parser for transactions (e.g., coinbase) not served by RPC."""
    block_meta = rpc_call("getblock", [block_hash, True])
    raw_block = rpc_call("getblock", [block_hash, False])
    data = bytes.fromhex(raw_block)
    offset = 80  # skip header
    tx_count, offset = read_varint(data, offset)
    for _ in range(tx_count):
        tx_info, offset = parse_transaction_at(data, offset)
        if tx_info["txid"] == txid:
            tx_info["blockhash"] = block_hash
            tx_info["time"] = block_meta.get("time")
            tx_info["confirmations"] = block_meta.get("confirmations", 0)
            return tx_info
    raise RPCError(f"Transaction {txid} not found in block {block_hash}")


def parse_transaction_at(buf: bytes, offset: int) -> tuple[dict[str, Any], int]:
    start = offset
    offset += 4  # version
    vin_count, offset = read_varint(buf, offset)
    vin: list[dict[str, Any]] = []
    for _ in range(vin_count):
        prev_tx = buf[offset : offset + 32][::-1].hex()
        offset += 32
        prev_vout = int.from_bytes(buf[offset : offset + 4], "little")
        offset += 4
        script_len, offset = read_varint(buf, offset)
        script = buf[offset : offset + script_len]
        offset += script_len
        sequence = int.from_bytes(buf[offset : offset + 4], "little")
        offset += 4
        if prev_tx == "00" * 32 and prev_vout == 0xFFFFFFFF:
            vin.append({"coinbase": script.hex(), "sequence": sequence})
        else:
            vin.append({"txid": prev_tx, "vout": prev_vout, "sequence": sequence})
    vout_count, offset = read_varint(buf, offset)
    vout: list[dict[str, Any]] = []
    for n in range(vout_count):
        value = int.from_bytes(buf[offset : offset + 8], "little")
        offset += 8
        script_len, offset = read_varint(buf, offset)
        script = buf[offset : offset + script_len]
        offset += script_len
        vout.append(
            {
                "n": n,
                "value": value,
                "scriptPubKey": script.hex(),
            }
        )
    lock_time = int.from_bytes(buf[offset : offset + 4], "little")
    offset += 4
    raw_tx = buf[start:offset]
    txid = double_sha256(raw_tx)[::-1].hex()
    return (
        {
            "txid": txid,
            "vin": vin,
            "vout": vout,
            "locktime": lock_time,
            "size": len(raw_tx),
            "hex": raw_tx.hex(),
        },
        offset,
    )


def read_varint(buf: bytes, offset: int) -> tuple[int, int]:
    prefix = buf[offset]
    offset += 1
    if prefix < 0xFD:
        return prefix, offset
    if prefix == 0xFD:
        value = int.from_bytes(buf[offset : offset + 2], "little")
        return value, offset + 2
    if prefix == 0xFE:
        value = int.from_bytes(buf[offset : offset + 4], "little")
        return value, offset + 4
    value = int.from_bytes(buf[offset : offset + 8], "little")
    return value, offset + 8


__all__ = [
    "TxInput",
    "TxOutput",
    "expand_transaction",
    "fetch_recent_blocks",
    "fetch_recent_transactions",
    "fetch_chain_tips",
    "fetch_mempool_stats",
]


def fetch_chain_tips() -> list[dict[str, Any]]:
    return rpc_call("getchaintips")


def fetch_mempool_stats() -> dict[str, Any]:
    raw = rpc_call("getrawmempool", [True])
    if not raw:
        return {
            "count": 0,
            "total_size": 0,
            "total_fees": 0,
            "min_fee": 0,
            "max_fee": 0,
            "median_fee": 0,
            "buckets": {}
        }

    fees = []
    total_size = 0
    total_fee_val = 0.0

    for txid, entry in raw.items():
        size = entry["size"]
        fee_coins = entry["fee"]
        fee_sats = fee_coins * 100_000_000
        rate = fee_sats / size if size > 0 else 0
        fees.append(rate)
        total_size += size
        total_fee_val += fee_coins

    fees.sort()
    count = len(fees)

    stats = {
        "count": count,
        "total_size": total_size,
        "total_fees": total_fee_val,
        "min_fee": fees[0],
        "max_fee": fees[-1],
        "median_fee": fees[count // 2],
    }

    # Histogram buckets
    buckets = {
        "0-1": 0, "1-2": 0, "2-5": 0, "5-10": 0, "10-20": 0, "20+": 0
    }
    ordered_keys = ["0-1", "1-2", "2-5", "5-10", "10-20", "20+"]

    for r in fees:
        if r < 1: buckets["0-1"] += 1
        elif r < 2: buckets["1-2"] += 1
        elif r < 5: buckets["2-5"] += 1
        elif r < 10: buckets["5-10"] += 1
        elif r < 20: buckets["10-20"] += 1
        else: buckets["20+"] += 1

    stats["buckets"] = {k: buckets[k] for k in ordered_keys}
    return stats
