"""Worker lifecycle and broker connection."""

import asyncio
import logging
import os
from pathlib import Path

from app.consumer import (
    serve,
    serve_dsn,
    serve_flight_ephemeris,
    serve_space_weather,
)

import aio_pika


async def main() -> None:
    """Maintain the worker connection and close it on cancellation.

    RABBITMQ_URL is required. Startup errors propagate to the container runtime.
    The consumer publishes confirmed results before acknowledging requests.
    """
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    try:
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        # Analysis ingestion, DSN and space weather requests use separate channels.
        await asyncio.gather(
            serve(connection, data_dir),
            serve_dsn(connection, data_dir),
            serve_space_weather(connection, data_dir),
            serve_flight_ephemeris(connection, data_dir),
        )
    finally:
        await connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
