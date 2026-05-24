"""Thin async client for the RunPod GraphQL API.

Covers only the pod-lifecycle calls we need for the boot controller:
- get_pod        - query current state
- resume_pod     - bring a STOPPED pod back online
- stop_pod       - park a RUNNING pod (volume retained, no GPU billing)

This client does NOT create or terminate pods. We assume a pod has
been created once via the RunPod console and the API only flips it
between RUNNING and STOPPED on session start/stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PodState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    EXITED = "EXITED"
    TERMINATED = "TERMINATED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class PodInfo:
    pod_id: str
    state: PodState
    gpu_count: int
    public_ip: str | None
    raw: dict[str, Any]


class RunPodError(RuntimeError):
    """RunPod API call failed or returned an unexpected payload."""


class RunPodClient:
    """Async wrapper around the RunPod GraphQL endpoint."""

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.runpod.io/graphql",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("RUNPOD_API_KEY must be set")
        self._api_key = api_key
        self._api_url = api_url
        self._timeout = timeout

    async def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._api_url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RunPodError(f"RunPod API HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        if "errors" in body and body["errors"]:
            raise RunPodError(f"RunPod GraphQL errors: {body['errors']}")
        if "data" not in body:
            raise RunPodError(f"RunPod response missing data: {body}")
        return body["data"]

    async def get_pod(self, pod_id: str) -> PodInfo:
        query = """
        query Pod($podId: String!) {
            pod(input: { podId: $podId }) {
                id
                desiredStatus
                lastStatusChange
                gpuCount
                vcpuCount
                memoryInGb
                volumeInGb
                containerDiskInGb
                imageName
                machine {
                    gpuDisplayName
                }
                runtime {
                    ports {
                        ip
                        isIpPublic
                        privatePort
                        publicPort
                        type
                    }
                }
            }
        }
        """
        data = await self._post(query, {"podId": pod_id})
        pod = data.get("pod")
        if not pod:
            raise RunPodError(f"Pod {pod_id} not found")
        state_raw = (pod.get("desiredStatus") or "").upper()
        try:
            state = PodState(state_raw)
        except ValueError:
            state = PodState.UNKNOWN
        public_ip: str | None = None
        runtime = pod.get("runtime") or {}
        for port in runtime.get("ports") or []:
            if port.get("isIpPublic") and port.get("ip"):
                public_ip = port["ip"]
                break
        return PodInfo(
            pod_id=pod_id,
            state=state,
            gpu_count=int(pod.get("gpuCount") or 0),
            public_ip=public_ip,
            raw=pod,
        )

    async def resume_pod(self, pod_id: str, gpu_count: int = 1) -> dict[str, Any]:
        mutation = """
        mutation Resume($input: PodResumeInput!) {
            podResume(input: $input) {
                id
                desiredStatus
            }
        }
        """
        variables = {"input": {"podId": pod_id, "gpuCount": gpu_count}}
        return await self._post(mutation, variables)

    async def stop_pod(self, pod_id: str) -> dict[str, Any]:
        mutation = """
        mutation Stop($input: PodStopInput!) {
            podStop(input: $input) {
                id
                desiredStatus
            }
        }
        """
        variables = {"input": {"podId": pod_id}}
        return await self._post(mutation, variables)
