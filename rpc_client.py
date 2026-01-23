from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


class RPCError(RuntimeError):
    """Raised when the node RPC returns an error response."""


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    rpc = config.get("rpc", {})
    rpc.setdefault("host", "127.0.0.1")
    rpc.setdefault("port", 8832)
    rpc.setdefault("username", "")
    rpc.setdefault("password", "")
    rpc.setdefault("use_https", False)
    config["rpc"] = rpc
    display = config.get("display", {})
    display.setdefault("recent_blocks", 10)
    display.setdefault("transactions_per_page", 25)
    display.setdefault("address_history", 15)
    display.setdefault("address_per_page", display.get("address_history", 15))
    display.setdefault("blocks_per_page", display.get("recent_blocks", 10))
    display.setdefault("rich_list_per_page", display.get("rich_list_limit", 25))
    display.setdefault("network_name", "Baseline")
    config["display"] = display
    return config


class RPCClient:
    def __init__(self, url: str, auth: tuple[str, str] | None, *, timeout: int = 15):
        self.url = url
        self.auth = auth
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, params: Iterable[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": "baseline-explorer",
            "method": method,
            "params": list(params or []),
        }
        try:
            response = self.session.post(self.url, json=payload, auth=self.auth, timeout=self.timeout)
        except requests.RequestException as exc:  # noqa: BLE001
            raise RPCError(f"Unable to reach Baseline RPC: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            raise RPCError(f"RPC HTTP {response.status_code}: {snippet or 'no response body'}")
        try:
            data = response.json()
        except ValueError as exc:  # requests.exceptions.JSONDecodeError derives from ValueError
            content_type = response.headers.get("content-type", "unknown")
            snippet = (response.text or "").strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            raise RPCError(f"RPC returned non-JSON ({content_type}): {snippet or 'empty body'}") from exc
        if data.get("error"):
            err = data["error"]
            raise RPCError(f"{err.get('message')} (code {err.get('code')})")
        return data["result"]


CONFIG = load_config()
SCHEME = "https" if CONFIG["rpc"].get("use_https") else "http"
RPC_URL = f"{SCHEME}://{CONFIG['rpc']['host']}:{CONFIG['rpc']['port']}"
auth = None
if CONFIG["rpc"]["username"] and CONFIG["rpc"]["password"]:
    auth = (CONFIG["rpc"]["username"], CONFIG["rpc"]["password"])
RPC_CLIENT = RPCClient(RPC_URL, auth)


def rpc_call(method: str, params: list[Any] | None = None) -> Any:
    return RPC_CLIENT.call(method, params)


__all__ = [
    "CONFIG",
    "RPCError",
    "RPC_URL",
    "rpc_call",
]
