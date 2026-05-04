"""Service entrypoint for the ultimate adapter shell."""

from __future__ import annotations

import asyncio

from ultimate_adapter.control_consumer import main as run_control_consumer
from ultimate_adapter.service_runtime import run_runtime_supervisor


async def main() -> None:
    await asyncio.gather(
        run_control_consumer(),
        run_runtime_supervisor(),
    )


if __name__ == "__main__":
    asyncio.run(main())
