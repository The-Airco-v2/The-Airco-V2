"""Airco Secure 2.0 — Stateless CRUD API Gateway."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from airco.db import async_session
from airco.minio_client import delete_objects_by_prefix
from airco.models import Camera as CameraModel
from api.routes import (
    sessions, cameras, persons, employees, attendance,
    alerts, reports, review, employee_intelligence, centrifugo_proxy,
    analytics, exports, overview, exceptions, auth as auth_routes,
    identity_reviews,
)
from api.routes import system
from api.routes.cameras import _go2rtc_add, _stream_name

logger = logging.getLogger(__name__)

app = FastAPI(title="Airco Secure 2.0 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://the-airco.net"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/v2/sessions", tags=["sessions"])
app.include_router(cameras.router, prefix="/api/v2/cameras", tags=["cameras"])
app.include_router(persons.router, prefix="/api/v2/persons", tags=["persons"])
app.include_router(employees.router, prefix="/api/v2/employees", tags=["employees"])
app.include_router(attendance.router, prefix="/api/v2/attendance", tags=["attendance"])
app.include_router(alerts.router, prefix="/api/v2/alerts", tags=["alerts"])
app.include_router(reports.router, prefix="/api/v2/reports", tags=["reports"])
app.include_router(analytics.router, prefix="/api/v2/reports", tags=["analytics"])
app.include_router(exports.router, prefix="/api/v2/reports", tags=["exports"])
app.include_router(overview.router, prefix="/api/v2/overview", tags=["overview"])
app.include_router(review.router, prefix="/api/v2/review", tags=["review"])
app.include_router(identity_reviews.router, prefix="/api/v2/identity-reviews", tags=["identity-reviews"])
app.include_router(exceptions.router, prefix="/api/v2/exceptions", tags=["exceptions"])
app.include_router(employee_intelligence.router, prefix="/api/v2", tags=["employee-intelligence"])
app.include_router(centrifugo_proxy.router, prefix="/api/v2/ws", tags=["websocket"])
app.include_router(system.router, prefix="/api/v2", tags=["system"])
app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])


@app.on_event("startup")
async def sync_cameras_to_go2rtc() -> None:
    """Re-register every camera in go2rtc on startup.

    go2rtc stores streams in memory only — they are lost on restart.
    This ensures all cameras are always available for live streaming
    regardless of go2rtc restart order.
    """
    try:
        delete_objects_by_prefix("face-training-debug/")
        delete_objects_by_prefix("employee-face-training/")
        async with async_session() as db:
            result = await db.execute(select(CameraModel))
            cams = result.scalars().all()
        for cam in cams:
            await _go2rtc_add(_stream_name(cam.name), cam.rtsp_url)
        logger.info("go2rtc startup sync: registered %d camera(s)", len(cams))
    except Exception:
        logger.warning("go2rtc startup sync failed", exc_info=True)


@app.exception_handler(IntegrityError)
async def handle_integrity_error(request: Request, exc: IntegrityError):
    logger.warning("IntegrityError on %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "code": "conflict",
                "message": "Operation conflicts with existing data. The record may be referenced by other resources.",
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    fields = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        fields.append(
            {
                "field": location,
                "message": error.get("msg", "Invalid value"),
            }
        )

    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_error",
                "message": "Request validation failed",
                "fields": fields,
            }
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
