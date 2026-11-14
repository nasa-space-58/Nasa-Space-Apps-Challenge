"""Submit DSN ephemeris requests via RabbitMQ; the backend never contacts NASA."""

import asyncio
from uuid import uuid4

import aio_pika
from shared.dsn import DSN_REQUEST_QUEUE, DsnEvent, DsnRequest, declare_dsn_topology

from app.submit_ingestion import connect_broker


async def submit_dsn(
    request: DsnRequest, broker_url: str, timeout_s: float = 600
) -> DsnEvent:
    """Publish a persistent request and await its correlated event.

    A timeout does not cancel acquisition. Resubmitting the identical request is
    safe; request_id is the worker's deduplication key.
    """
    connection = await connect_broker(broker_url)
    try:
        channel = await connection.channel(
            publisher_confirms=True, on_return_raises=True
        )
        _, exchange = await declare_dsn_topology(channel)
        request_id = str(request.request_id)
        reply = await channel.declare_queue(
            f"lunar.dsn.reply.{request_id}.{uuid4().hex}",
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
            routing_key=DSN_REQUEST_QUEUE,
            mandatory=True,
        )
        async with asyncio.timeout(timeout_s):
            async with reply.iterator() as messages:
                async for message in messages:
                    event = DsnEvent.model_validate_json(message.body)
                    if event.request_id != request.request_id:
                        await message.reject(requeue=False)
                        continue
                    await message.ack()
                    return event
    finally:
        await connection.close()
