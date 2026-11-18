import asyncio
import logging
import os

from app.scheduler import configured_tick


async def main():
    interval = max(1, int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "900")))
    while True:
        try:
            await configured_tick()
        except Exception:
            # Exception bodies/tracebacks may contain connection URLs or worker output.
            logging.getLogger(__name__).error("Scheduler tick failed; retrying on next interval")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
