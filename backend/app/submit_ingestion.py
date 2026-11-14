"""Submit via RabbitMQ; the backend never contacts NASA or writes data files."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import aio_pika
from shared.ingestion import (
    IngestionRequest,
    IngestionEvent,
    REQUEST_QUEUE,
    declare_topology,
)


async def connect_broker(broker_url: str, attempts: int = 10, delay_s: float = 2, connect_timeout_s: float = 5):
    """Bound initial connection retries while the broker starts or restarts."""
    raise NotImplementedError


async def submit(request: IngestionRequest, broker_url: str, timeout_s: float = 600):
    """Send a persistent request and await a correlated durable reply.

    A timeout does not cancel ingestion. Resubmitting the identical envelope is
    safe. Completion delivery is at-least-once; request_id is the deduplication key.
    """
    connection = await aio_pika.connect_robust(broker_url)
    try:
        channel = await connection.channel(
            publisher_confirms=True, on_return_raises=True
        )
        _, exchange = await declare_topology(channel)
        request_id = str(request.request_id)
        reply = await channel.declare_queue(
            f"lunar.ingestion.reply.{request_id}.{uuid4().hex}",
            durable=True,
            arguments={"x-expires": 86400000},
        )
        await reply.bind(exchange, routing_key=request_id)
        await channel.default_exchange.publish(
            aio_pika.Message(
                request.model_dump_json().encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                correlation_id=request_id,
                message_id=request_id,
            ),
            routing_key=REQUEST_QUEUE,
            mandatory=True,
        )
        async with asyncio.timeout(timeout_s):
            async with reply.iterator() as messages:
                async for message in messages:
                    event = IngestionEvent.model_validate_json(message.body)
                    if event.request_id != request.request_id:
                        await message.reject(requeue=False)
                        continue
                    await message.ack()
                    return event
    finally:
        await connection.close()


def main():
    """Read a shared-volume-mounted envelope; print ready/failed and exit nonzero on failure."""
    parser = argparse.ArgumentParser(
        description="Submit an ingestion request to RabbitMQ"
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--timeout-s", type=float, default=600)
    args = parser.parse_args()
    request = IngestionRequest.model_validate_json(args.request.read_text())
    event = asyncio.run(submit(request, os.environ["RABBITMQ_URL"], args.timeout_s))
    print(json.dumps(event.model_dump(mode="json"), indent=2))
    if event.status != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
