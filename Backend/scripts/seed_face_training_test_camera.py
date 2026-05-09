"""Add or update the local face-training test-video camera."""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from airco.config import settings
from airco.models import Camera


CAMERA_NAME = os.getenv("FACE_TRAINING_TEST_CAMERA_NAME", "Face Training Test Video")
RTSP_URL = os.getenv(
    "FACE_TRAINING_TEST_RTSP_URL",
    "ffmpeg:/media/Testing.mp4#video=h264#input=file",
)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        os.environ.get("DATABASE_URL", settings.database_url),
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def main() -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(
            select(Camera).where(
                Camera.tenant_id == settings.tenant_id,
                Camera.name == CAMERA_NAME,
            )
        )
        camera = result.scalar_one_or_none()
        if camera is None:
            camera = Camera(
                tenant_id=settings.tenant_id,
                name=CAMERA_NAME,
                rtsp_url=RTSP_URL,
                location="Test Video",
                zone="Face Training Lab",
                is_entrance=False,
                is_active=True,
            )
            db.add(camera)
            action = "Added"
        else:
            camera.rtsp_url = RTSP_URL
            camera.location = "Test Video"
            camera.zone = "Face Training Lab"
            camera.is_entrance = False
            camera.is_active = True
            action = "Updated"

        await db.commit()
        print(f"{action}: {CAMERA_NAME} -> {RTSP_URL}")


if __name__ == "__main__":
    asyncio.run(main())
