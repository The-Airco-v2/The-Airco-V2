"""Identity consumer — reads track/crop events, manages canonical persons."""

import asyncio
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.config import settings
from airco.db import async_session
from airco.events import StreamNames
from airco.redis_streams import consume_stream
from identity_consumer.face_matcher import FaceMatcher
from identity_consumer.cross_camera import CrossCameraMerger, CameraTopology
from identity_consumer.person_manager import PersonManager
from identity_consumer.gallery_maintenance import GalleryMaintenance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("identity-consumer")

GROUP = "identity-group"
TRACK_CONSUMER = f"identity-tracks-{os.getpid()}"
CROP_CONSUMER = f"identity-crops-{os.getpid()}"


def _normalize_selector(value: object) -> str:
    if not isinstance(value, str):
        return "standard"
    normalized = value.strip().lower()
    if normalized == "ultimate_reid":
        return "ultimate"
    if normalized in {"standard", "ultimate"}:
        return normalized
    return "standard"


def _should_skip_track_processing(*, uses_ultimate_path: bool) -> bool:
    return uses_ultimate_path


def _should_skip_crop_processing(*, uses_ultimate_path: bool) -> bool:
    # Ultimate owns track lifecycle, but crop-driven identity matching must still run
    # so canonical SessionPerson rows can become identified employees.
    return False


async def _session_uses_ultimate_path(
    db,
    *,
    session_id: uuid.UUID | None,
    cache: dict[str, bool],
) -> bool:
    if session_id is None:
        return False
    cache_key = str(session_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from sqlalchemy import select
    from airco.models import Session

    result = await db.execute(
        select(Session.config).where(Session.id == session_id).limit(1)
    )
    config = result.scalar_one_or_none() or {}
    uses_ultimate = _normalize_selector((config or {}).get("reid_profile")) == "ultimate"
    cache[cache_key] = uses_ultimate
    return uses_ultimate

async def _run_track_consumer(
    *,
    face_matcher: FaceMatcher,
    merger: CrossCameraMerger,
):
    async with async_session() as db:
        manager = PersonManager(db, face_matcher, merger)
        await manager.load_active_bindings()
        selector_cache: dict[str, bool] = {}

        async def handler(msg_id: str, fields: dict):
            from datetime import datetime, timezone

            event_type = fields.get("event_type", "")
            session_id = uuid.UUID(fields["session_id"]) if "session_id" in fields else None
            camera_id = uuid.UUID(fields["camera_id"]) if "camera_id" in fields else None
            track_id = int(fields.get("track_id", 0))
            ts = datetime.now(timezone.utc)

            try:
                if _should_skip_track_processing(
                    uses_ultimate_path=await _session_uses_ultimate_path(
                        db,
                        session_id=session_id,
                        cache=selector_cache,
                    )
                ):
                    return
                if event_type == "track_started":
                    bbox = fields.get("bbox", [])
                    conf = float(fields.get("confidence", 0))
                    await manager.handle_track_started(session_id, camera_id, track_id, bbox, conf, ts)
                elif event_type == "track_observed":
                    bbox = fields.get("bbox", [])
                    await manager.handle_track_observed(session_id, camera_id, track_id, bbox, ts)
                elif event_type == "track_ended":
                    await manager.handle_track_ended(session_id, camera_id, track_id, ts)
                await db.commit()
            except Exception:
                logger.exception("Track handler error for %s, rolling back", msg_id)
                await db.rollback()

        logger.info("Identity track consumer starting...")
        await consume_stream(
            stream=StreamNames.TRACKS,
            group=GROUP,
            consumer=TRACK_CONSUMER,
            handler=handler,
        )


async def _run_crop_consumer(
    *,
    face_matcher: FaceMatcher,
    merger: CrossCameraMerger,
):
    async with async_session() as db:
        manager = PersonManager(db, face_matcher, merger)
        await manager.load_active_bindings()
        selector_cache: dict[str, bool] = {}

        async def handler(msg_id: str, fields: dict):
            import base64
            import io
            from datetime import datetime, timezone

            import numpy as np
            import tritonclient.grpc.aio as grpcclient
            from PIL import Image

            event_type = fields.get("event_type", "")
            if event_type not in ("face_crop", "body_crop"):
                return

            session_id = uuid.UUID(fields["session_id"]) if "session_id" in fields else None
            camera_id = uuid.UUID(fields["camera_id"]) if "camera_id" in fields else None
            track_id = int(fields.get("track_id", 0))
            ts = datetime.now(timezone.utc)
            if _should_skip_crop_processing(
                uses_ultimate_path=await _session_uses_ultimate_path(
                    db,
                    session_id=session_id,
                    cache=selector_cache,
                )
            ):
                return
            crop_b64 = fields.get("crop_b64", "")
            if not crop_b64:
                return
            crop_bytes = base64.b64decode(crop_b64)

            img = Image.open(io.BytesIO(crop_bytes)).resize((112, 112) if event_type == "face_crop" else (128, 256))
            img_np = np.array(img, dtype=np.float32).transpose(2, 0, 1)[np.newaxis] / 255.0

            model_name = "arcface" if event_type == "face_crop" else "osnet"
            triton = grpcclient.InferenceServerClient(url=settings.triton_url)
            inp = grpcclient.InferInput("input", img_np.shape, "FP32")
            inp.set_data_from_numpy(img_np)
            result = await triton.infer(model_name, [inp])
            embedding = result.as_numpy("output").flatten().tolist()

            quality_score = float(fields.get("quality_score", 0.5))
            try:
                await manager.handle_crop(
                    session_id, camera_id, track_id,
                    embedding, quality_score, event_type, ts,
                )
                await db.commit()
            except Exception:
                logger.exception("Crop handler error for %s, rolling back", msg_id)
                await db.rollback()

        logger.info("Identity crop consumer starting...")
        await consume_stream(
            stream=StreamNames.CROPS,
            group=GROUP,
            consumer=CROP_CONSUMER,
            handler=handler,
        )


async def _load_topology_from_db() -> CameraTopology:
    """Load CameraPair rows from DB and build a CameraTopology.

    Falls back to an empty topology if the table has no rows yet.  Operators
    configure pairs via the camera settings UI (or by setting Camera.zone so
    all same-zone cameras are treated as same_space automatically).
    """
    from sqlalchemy import select
    from airco.db import async_session
    from airco.models import CameraPair, Camera

    topology = CameraTopology()
    async with async_session() as db:
        # Explicit camera pairs (operator-configured)
        result = await db.execute(select(CameraPair))
        for pair in result.scalars().all():
            topology.add_pair(
                str(pair.camera_a_id),
                str(pair.camera_b_id),
                overlap_type=pair.overlap_type,
                transition_min=pair.transition_min_sec,
                transition_max=pair.transition_max_sec,
                same_space=pair.same_space,
            )

        # Auto-generate same_space pairs for cameras sharing the same zone
        result = await db.execute(
            select(Camera).where(Camera.zone.isnot(None), Camera.is_active == True)
        )
        by_zone: dict[str, list[str]] = {}
        for cam in result.scalars().all():
            by_zone.setdefault(cam.zone, []).append(str(cam.id))
        for zone_cameras in by_zone.values():
            for i, cam_a in enumerate(zone_cameras):
                for cam_b in zone_cameras[i + 1:]:
                    # Don't overwrite explicit CameraPair configuration
                    topology.add_pair(
                        cam_a, cam_b,
                        overlap_type="overlapping",
                        transition_min=0.0,
                        transition_max=300.0,
                        same_space=True,
                        overwrite=False,
                    )

    return topology


async def _run_gallery_maintenance():
    """Periodic background task for gallery cleanup."""
    from airco.models import SessionPerson
    from sqlalchemy import select

    maintenance = GalleryMaintenance(ttl_days=7)

    while True:
        try:
            await asyncio.sleep(6 * 3600)  # Every 6 hours

            async with async_session() as db:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)

                stmt = select(SessionPerson).where(
                    SessionPerson.recognition_state == "unknown",
                    SessionPerson.is_active == True,
                    SessionPerson.merged_into_session_person_id.is_(None),
                )
                result = await db.execute(stmt)
                expired_count = 0
                for person in result.scalars().all():
                    if maintenance.should_expire(person, now):
                        person.is_active = False
                        expired_count += 1
                await db.commit()

                if expired_count:
                    logger.info("Gallery maintenance: expired %d stale unknown persons", expired_count)

        except Exception:
            logger.exception("Gallery maintenance error")


async def main():
    topology = await _load_topology_from_db()
    face_matcher = FaceMatcher(similarity_threshold=0.55)
    merger = CrossCameraMerger(topology)
    logger.info("Topology loaded: %d camera pairs registered", len(topology._pairs))
    await asyncio.gather(
        _run_track_consumer(
            face_matcher=face_matcher,
            merger=merger,
        ),
        _run_crop_consumer(
            face_matcher=face_matcher,
            merger=merger,
        ),
        _run_gallery_maintenance(),
    )


if __name__ == "__main__":
    asyncio.run(main())
