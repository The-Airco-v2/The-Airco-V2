"""Publish events from Redis Streams to Centrifugo WebSocket channels."""

from __future__ import annotations

import httpx

from airco.config import settings
from airco.events import build_live_event_envelope, resolve_live_event_channel


class CentrifugoPublisher:
    def __init__(self):
        self.api_url = settings.centrifugo_api_url
        self.api_key = settings.centrifugo_api_key

    async def publish(self, channel: str, data: dict):
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"apikey {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "method": "publish",
                    "params": {
                        "channel": channel,
                        "data": data,
                    },
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Centrifugo publish failed: {resp.status_code} {resp.text}")

    def get_channel(self, stream: str, fields: dict) -> str:
        event_type = str(fields.get("event_type", ""))
        tenant_id = fields.get("tenant_id")
        session_id = fields.get("session_id")
        return resolve_live_event_channel(
            stream=stream,
            event_type=event_type,
            tenant_id=str(tenant_id) if tenant_id is not None else None,
            session_id=str(session_id) if session_id is not None else None,
        )

    def build_envelope(self, fields: dict) -> dict:
        event_type = str(fields.get("event_type", ""))
        tenant_id = fields.get("tenant_id")
        session_id = fields.get("session_id")
        occurred_at = fields.get("occurred_at", fields.get("timestamp"))
        return build_live_event_envelope(
            event_type=event_type,
            tenant_id=str(tenant_id) if tenant_id is not None else None,
            session_id=str(session_id) if session_id is not None else None,
            occurred_at=occurred_at,
            payload=fields,
        )

    async def publish_normalized(self, stream: str, fields: dict):
        channel = self.get_channel(stream, fields)
        envelope = self.build_envelope(fields)
        await self.publish(channel, envelope)
