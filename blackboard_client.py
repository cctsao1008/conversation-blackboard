from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BlackboardError(RuntimeError):
    def __init__(self, status: int, error: str):
        super().__init__(f"blackboard HTTP {status}: {error}")
        self.status = status
        self.error = error


@dataclass(frozen=True)
class BlackboardIdentity:
    source: str
    instance: str
    label: str | None


class BlackboardClient:
    """Small vendor-neutral adapter for the conversation-blackboard HTTP API."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                error = body.get("error", "http_error")
            except Exception:
                error = "http_error"
            raise BlackboardError(exc.code, error) from None

    def whoami(self) -> BlackboardIdentity:
        data = self._request("GET", "/api/whoami")
        return BlackboardIdentity(
            source=data["source"],
            instance=data["instance"],
            label=data.get("label"),
        )

    def channels(self) -> list[dict]:
        return self._request("GET", "/api/channels")["channels"]

    def messages(
        self,
        *,
        after: int = 0,
        channel: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        query = {"after": after, "limit": limit}
        if channel is not None:
            query["channel"] = channel
        path = "/api/messages?" + urlencode(query)
        return self._request("GET", path)["messages"]

    def post(
        self,
        *,
        channel: str,
        body: str,
        kind: str = "message",
        reply_to: int | None = None,
    ) -> dict:
        payload = {
            "channel": channel,
            "kind": kind,
            "body": body,
            "reply_to": reply_to,
        }
        return self._request("POST", "/api/messages", payload)["message"]
