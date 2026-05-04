from __future__ import annotations

from collections.abc import Mapping


REQUIRED_SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "CENTRIFUGO_API_KEY",
    "CENTRIFUGO_TOKEN_SECRET",
    "SESSION_SECRET",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
)


def render_env(template_text: str, runtime_env: Mapping[str, str]) -> str:
    missing = [key for key in REQUIRED_SECRET_KEYS if not runtime_env.get(key)]
    if missing:
        raise ValueError(f"missing required secrets: {', '.join(missing)}")

    rendered_lines: list[str] = []
    for raw_line in template_text.splitlines():
        if not raw_line or raw_line.lstrip().startswith("#") or "=" not in raw_line:
            rendered_lines.append(raw_line)
            continue
        key, value = raw_line.split("=", 1)
        override = runtime_env.get(key)
        rendered_lines.append(f"{key}={override if override else value}")
    return "\n".join(rendered_lines) + "\n"
