import json
import socket
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class FroniusClientError(RuntimeError):
    """A request to, or response from, the Fronius API failed."""


class FroniusClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def get(self, endpoint: str, **query: str) -> Mapping[str, Any]:
        url = self.base_url + endpoint.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            exc.close()
            raise FroniusClientError(f"GET {endpoint} failed: {exc}") from exc
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise FroniusClientError(f"GET {endpoint} failed: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FroniusClientError(f"GET {endpoint} returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise FroniusClientError(f"GET {endpoint} returned a non-object JSON value")

        head = payload.get("Head")
        status = head.get("Status") if isinstance(head, Mapping) else None
        code = status.get("Code") if isinstance(status, Mapping) else None
        if code not in (None, 0):
            reason = status.get("Reason", "unknown API error")
            raise FroniusClientError(f"GET {endpoint} returned API status {code}: {reason}")
        return payload

    def _optional(self, endpoint: str, **query: str) -> Optional[Mapping[str, Any]]:
        try:
            return self.get(endpoint, **query)
        except FroniusClientError:
            return None

    def get_live_payloads(self) -> Dict[str, Optional[Mapping[str, Any]]]:
        """Fetch required live data and independently optional components."""
        return {
            "power_flow": self.get("GetPowerFlowRealtimeData.fcgi"),
            "inverter_realtime": self._optional(
                "GetInverterRealtimeData.cgi",
                Scope="System",
                DataCollection="CumulationInverterData",
            ),
            "storage": self._optional("GetStorageRealtimeData.cgi", Scope="System"),
            "ohmpilot": self._optional("GetOhmPilotRealtimeData.cgi", Scope="System"),
        }
