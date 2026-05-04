"""Migrate data from Airco Secure 1.0 (Supabase) to 2.0 (TimescaleDB).

Migrates:
  1. Employees (employees table)
  2. Face templates/embeddings (employee_face_data -> employee_face_templates)
  3. Cameras (cameras table)

Usage:
    SUPABASE_DATABASE_URL=postgresql://... DATABASE_URL=postgresql+asyncpg://... \
        python migrate_v1_data.py
"""

import asyncio
import json
import os
import sys

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from airco.config import settings
from airco.models import Camera, Employee, EmployeeFaceTemplate


def get_v2_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        os.environ.get("DATABASE_URL", settings.database_url),
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def record_to_dict(row) -> dict:
    return dict(row)


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def select_expr(columns: set[str], alias: str, *candidates: str, required: bool = False) -> str:
    for candidate in candidates:
        if candidate in columns:
            return f"{quote_ident(candidate)} AS {quote_ident(alias)}"
    if required:
        raise RuntimeError(f"Missing required source column for {alias}: tried {', '.join(candidates)}")
    return f"NULL AS {quote_ident(alias)}"


def safe_float(value, default: float) -> float:
    if value is None:
        return default
    return float(value)


def normalize_embedding(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return [float(item) for item in value]


def camera_status_to_active(value) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in {"active", "online", "healthy", "enabled", "true", "1"}


def camera_is_entrance(entrance_monitor, camera_type) -> bool:
    if entrance_monitor is not None:
        return bool(entrance_monitor)
    if camera_type is None:
        return False
    return str(camera_type).strip().lower() == "entrance"


async def get_supabase_connection():
    url = os.environ.get("SUPABASE_DATABASE_URL")
    if not url:
        print("ERROR: Set SUPABASE_DATABASE_URL environment variable")
        sys.exit(1)
    return await asyncpg.connect(url)


async def resolve_table_columns(v1_conn, *table_names: str) -> tuple[str | None, set[str]]:
    for table_name in table_names:
        rows = await v1_conn.fetch(
            """
            SELECT table_schema, column_name
            FROM information_schema.columns
            WHERE table_name = $1
            ORDER BY ordinal_position
            """,
            table_name,
        )
        if not rows:
            continue

        schemas = {row["table_schema"] for row in rows}
        preferred_schema = "public" if "public" in schemas else rows[0]["table_schema"]
        columns = {
            row["column_name"]
            for row in rows
            if row["table_schema"] == preferred_schema
        }
        return table_name, columns

    return None, set()


async def migrate_employees(v1_conn, v2_session):
    """Migrate employees from Supabase to TimescaleDB."""
    table_name, columns = await resolve_table_columns(v1_conn, "employees")
    if not table_name:
        raise RuntimeError("Source table not found: employees")

    query = (
        "SELECT "
        f"{select_expr(columns, 'id', 'id', required=True)}, "
        f"{select_expr(columns, 'name', 'name', 'full_name', 'username', required=True)}, "
        f"{select_expr(columns, 'employee_code', 'employee_code', 'employee_id')}, "
        f"{select_expr(columns, 'department', 'department')}, "
        f"{select_expr(columns, 'role', 'role', 'designation')}, "
        f"{select_expr(columns, 'is_active', 'is_active')}, "
        f"{select_expr(columns, 'created_at', 'created_at', 'registration_timestamp')} "
        f"FROM {quote_ident(table_name)}"
    )
    rows = await v1_conn.fetch(query)
    count = 0
    id_map = {}
    for row in rows:
        data = record_to_dict(row)
        department = data.get("department")
        if not department and data.get("role"):
            # v2 has no dedicated role field; preserve it only when department is empty.
            department = data["role"]

        employee_kwargs = {
            "tenant_id": settings.tenant_id,
            "name": data["name"],
            "employee_code": data.get("employee_code") or str(data["id"]),
            "department": department,
            "status": "active" if data.get("is_active", True) else "inactive",
        }
        if data.get("created_at") is not None:
            employee_kwargs["created_at"] = data["created_at"]

        emp = Employee(**employee_kwargs)
        v2_session.add(emp)
        await v2_session.flush()
        id_map[str(data["id"])] = emp.id
        id_map[str(employee_kwargs["employee_code"])] = emp.id
        count += 1

    print(f"  Migrated {count} employees")
    return id_map


async def migrate_face_templates(v1_conn, v2_session, emp_id_map):
    """Migrate face embeddings from Supabase to TimescaleDB."""
    table_name, columns = await resolve_table_columns(
        v1_conn,
        "employee_face_data",
        "employee_embeddings",
        "face_embeddings",
    )
    if not table_name:
        print(
            "  WARNING: No source embedding table found "
            "(checked employee_face_data, employee_embeddings, face_embeddings)"
        )
        return

    query = (
        "SELECT "
        f"{select_expr(columns, 'employee_id', 'employee_id', required=True)}, "
        f"{select_expr(columns, 'embedding', 'embedding', required=True)}, "
        f"{select_expr(columns, 'quality_score', 'quality_score')}, "
        f"{select_expr(columns, 'created_at', 'created_at', 'trained_at')} "
        f"FROM {quote_ident(table_name)}"
    )
    rows = await v1_conn.fetch(query)
    count = 0
    for row in rows:
        data = record_to_dict(row)
        old_emp_id = str(data["employee_id"])
        new_emp_id = emp_id_map.get(old_emp_id)
        if not new_emp_id:
            print(f"  WARNING: No mapping for employee {old_emp_id}, skipping face template")
            continue

        embedding = normalize_embedding(data.get("embedding"))
        if not embedding:
            print(f"  WARNING: Empty embedding for employee {old_emp_id}, skipping face template")
            continue
        if len(embedding) != 512:
            print(
                f"  WARNING: Expected 512-d embedding for employee {old_emp_id}, "
                f"got {len(embedding)}; skipping face template"
            )
            continue

        template_kwargs = {
            "employee_id": new_emp_id,
            "embedding": embedding,
            "angle_label": "frontal",
            "quality_score": safe_float(data.get("quality_score"), 0.8),
        }
        if data.get("created_at") is not None:
            template_kwargs["created_at"] = data["created_at"]

        template = EmployeeFaceTemplate(**template_kwargs)
        v2_session.add(template)
        count += 1

    print(f"  Migrated {count} face templates")


async def migrate_cameras(v1_conn, v2_session):
    """Migrate camera configs from Supabase to TimescaleDB."""
    table_name, columns = await resolve_table_columns(v1_conn, "cameras")
    if not table_name:
        raise RuntimeError("Source table not found: cameras")

    query = (
        "SELECT "
        f"{select_expr(columns, 'name', 'name', required=True)}, "
        f"{select_expr(columns, 'rtsp_url', 'rtsp_url', 'rtsp_link', 'url', required=True)}, "
        f"{select_expr(columns, 'location', 'location')}, "
        f"{select_expr(columns, 'zone', 'zone')}, "
        f"{select_expr(columns, 'is_active', 'is_active')}, "
        f"{select_expr(columns, 'status', 'status')}, "
        f"{select_expr(columns, 'entrance_monitor', 'entrance_monitor')}, "
        f"{select_expr(columns, 'camera_type', 'camera_type')}, "
        f"{select_expr(columns, 'created_at', 'created_at')} "
        f"FROM {quote_ident(table_name)}"
    )
    rows = await v1_conn.fetch(query)
    count = 0
    for row in rows:
        data = record_to_dict(row)
        is_active = data.get("is_active")
        if is_active is None:
            is_active = camera_status_to_active(data.get("status"))

        camera_kwargs = {
            "tenant_id": settings.tenant_id,
            "name": data["name"],
            "rtsp_url": data["rtsp_url"],
            "location": data.get("location"),
            "zone": data.get("zone") or data.get("location"),  # Fall back to location for older schemas.
            "is_entrance": camera_is_entrance(data.get("entrance_monitor"), data.get("camera_type")),
            "is_active": is_active,
        }
        if data.get("created_at") is not None:
            camera_kwargs["created_at"] = data["created_at"]

        cam = Camera(**camera_kwargs)
        v2_session.add(cam)
        count += 1

    print(f"  Migrated {count} cameras")


async def main():
    print("=== Airco Secure v1 -> v2 Data Migration ===\n")

    print("Connecting to Supabase (v1)...")
    v1_conn = await get_supabase_connection()
    session_factory = get_v2_session_factory()

    try:
        print("Connecting to TimescaleDB (v2)...")
        async with session_factory() as v2_session:
            print("\n[1/3] Migrating employees...")
            emp_id_map = await migrate_employees(v1_conn, v2_session)

            print("[2/3] Migrating face templates...")
            await migrate_face_templates(v1_conn, v2_session, emp_id_map)

            print("[3/3] Migrating cameras...")
            await migrate_cameras(v1_conn, v2_session)

            await v2_session.commit()
            print("\nAll data committed to v2 database.")
    finally:
        await v1_conn.close()

    print("\n=== Migration Complete ===")


if __name__ == "__main__":
    asyncio.run(main())
