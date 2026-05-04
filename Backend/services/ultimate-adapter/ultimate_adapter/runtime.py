"""Adapter-owned runtime wiring between the extracted Ultimate core and v2 outputs."""

from __future__ import annotations

from datetime import datetime

from ultimate_adapter.output_adapter import UltimateOutputAdapter
from ultimate_adapter.ultimate_core import UltimateAdapterCoreFacade, UltimateCleanupResult, UltimateFrameResult


class UltimateSessionRuntime:
    """Thin orchestration layer for processing frames through Ultimate and publishing v2 events."""

    def __init__(
        self,
        *,
        facade: UltimateAdapterCoreFacade,
        output_adapter: UltimateOutputAdapter,
    ):
        self.facade = facade
        self.output_adapter = output_adapter

    async def process_frame(
        self,
        db,
        *,
        frame,
        detections=None,
        observed_at: datetime | None = None,
    ) -> UltimateFrameResult:
        result = self.facade.process_frame(frame, detections=detections)
        await self.output_adapter.publish_frame_result(
            db,
            result=result,
            frame=frame,
            observed_at=observed_at,
        )
        return result

    async def cleanup(
        self,
        db,
        *,
        observed_at: datetime | None = None,
    ) -> UltimateCleanupResult:
        result = self.facade.cleanup()
        await self.output_adapter.publish_cleanup(
            db,
            result=result,
            observed_at=observed_at,
        )
        return result

    async def shutdown(
        self,
        db,
        *,
        observed_at: datetime | None = None,
    ) -> UltimateCleanupResult:
        result = self.facade.shutdown()
        await self.output_adapter.publish_cleanup(
            db,
            result=result,
            observed_at=observed_at,
        )
        return result
