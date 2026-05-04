"""Seed test cameras into the database."""

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from airco.config import settings
from airco.models import Camera


DEFAULT_TEST_CAMERAS = [
    {
        "name": "Camera 1 - Entrance",
        "rtsp_url": "rtsp://mediamtx:8554/cam1",
        "location": "Main Gate",
        "zone": "Entrance",
        "is_entrance": True,
    },
    {
        "name": "Camera 2 - Workshop",
        "rtsp_url": "rtsp://mediamtx:8554/cam2",
        "location": "Workshop Floor",
        "zone": "Workshop",
        "is_entrance": False,
    },
    {
        "name": "Camera 3 - Office",
        "rtsp_url": "rtsp://mediamtx:8554/cam3",
        "location": "Office Area",
        "zone": "Office",
        "is_entrance": False,
    },
    {
        "name": "Camera 4 - Parking",
        "rtsp_url": "rtsp://mediamtx:8554/cam4",
        "location": "Parking Lot",
        "zone": "Parking",
        "is_entrance": True,
    },
    {
        "name": "Camera 5 - Warehouse",
        "rtsp_url": "rtsp://mediamtx:8554/cam5",
        "location": "Warehouse",
        "zone": "Warehouse",
        "is_entrance": False,
    },
    {
        "name": "Camera 6 - Canteen",
        "rtsp_url": "rtsp://mediamtx:8554/cam6",
        "location": "Canteen",
        "zone": "Canteen",
        "is_entrance": False,
    },
]


def get_replay_json_path() -> Path:
    default_path = Path(__file__).resolve().parents[2] / "replay.json"
    return Path(os.environ.get("REPLAY_JSON_PATH", str(default_path)))


def load_test_cameras() -> list[dict]:
    replay_json_path = get_replay_json_path()
    if not replay_json_path.exists():
        return DEFAULT_TEST_CAMERAS

    payload = json.loads(replay_json_path.read_text(encoding="utf-8"))
    cameras = payload if isinstance(payload, list) else payload.get("cameras", [])
    if not cameras:
        return DEFAULT_TEST_CAMERAS

    loaded_cameras = []
    for item in cameras[:6]:
        rtsp_url = item.get("rtsp_url") or item.get("rtsp_link") or item.get("url")
        if not rtsp_url:
            continue
        loaded_cameras.append(
            {
                "name": item.get("name") or f"Camera {item.get('camera_id', len(loaded_cameras) + 1)}",
                "rtsp_url": rtsp_url,
                "location": item.get("location"),
                "zone": item.get("zone") or item.get("location"),
                "is_entrance": bool(item.get("entrance_monitor", False)),
            }
        )

    return loaded_cameras or DEFAULT_TEST_CAMERAS


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        os.environ.get("DATABASE_URL", settings.database_url),
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def main():
    test_cameras = load_test_cameras()
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(select(Camera).where(Camera.tenant_id == settings.tenant_id))
        existing_by_name = {camera.name: camera for camera in result.scalars()}

        added = 0
        updated = 0
        for cam_data in test_cameras:
            existing = existing_by_name.get(cam_data["name"])
            if existing is None:
                db.add(Camera(tenant_id=settings.tenant_id, **cam_data))
                added += 1
                print(f"  Added: {cam_data['name']} ({cam_data['zone']})")
                continue

            existing.rtsp_url = cam_data["rtsp_url"]
            existing.location = cam_data["location"]
            existing.zone = cam_data["zone"]
            existing.is_entrance = cam_data["is_entrance"]
            existing.is_active = True
            updated += 1
            print(f"  Updated: {cam_data['name']} ({cam_data['zone']})")

        await db.commit()
    print(f"\nSeed complete. Added {added}, updated {updated}, total target cameras {len(test_cameras)}.")


if __name__ == "__main__":
    asyncio.run(main())
