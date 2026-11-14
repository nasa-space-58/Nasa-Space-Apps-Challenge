"""Worker lifecycle and broker connection."""

import asyncio
import logging
import os
from pathlib import Path

from app.consumer import serve

import aio_pika


async def main() -> None:
    """Maintain the worker connection and close it on cancellation.

    RABBITMQ_URL is required. Startup errors propagate to the container runtime.
    The consumer publishes confirmed results before acknowledging requests.
    """
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    try:
        consumer = asyncio.create_task(
            serve(connection, Path(os.environ.get("DATA_DIR", "/data")))
        )
        await consumer
    finally:
        await connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
