"""Worker lifecycle and broker connection."""
import asyncio
import logging
import os

import aio_pika


async def main() -> None:
    """Maintain the worker connection and close it on cancellation.

    RABBITMQ_URL is required. Startup errors propagate to the container runtime.
    Ingestion consumers are implemented in Step 1.
    """
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    try:
        logging.info("Broker connected; ingestion processing is pending Step 1")
        await asyncio.Future()
    finally:
        await connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
