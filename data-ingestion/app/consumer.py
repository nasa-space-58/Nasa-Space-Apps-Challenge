"""RabbitMQ consumer: persistent confirmed completion before request ACK."""

import asyncio
import logging
from pathlib import Path

import aio_pika
from pydantic import ValidationError
from shared.ingestion import IngestionRequest, IngestionEvent, declare_topology
from app.jobs import process_job, RequestConflict


async def handle_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; let transport errors propagate without ACK.

    Durable local results make redelivery safe when completion publication or its
    ACK is interrupted. Unexpected processing failures stop the consumer visibly.
    """
    try:
        if len(message.body) > 65536:
            raise ValueError("Request message exceeds 64 KiB")
        request = IngestionRequest.model_validate_json(message.body)
    except (ValidationError, ValueError):
        logging.warning("Rejecting invalid ingestion message to dead-letter queue")
        await message.reject(requeue=False)
        return
    try:
        event = await asyncio.to_thread(process_job, request, data_dir)
    except RequestConflict as exc:
        event = IngestionEvent(
            request_id=request.request_id,
            status="failed",
            error_code="request_conflict",
            error_message=str(exc),
        )
    await exchange.publish(
        aio_pika.Message(
            body=event.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=str(request.request_id),
            message_id=f"{request.request_id}:{event.status}",
        ),
        routing_key=str(request.request_id),
        mandatory=True,
    )
    await message.ack()
    logging.info("Ingestion %s: %s", request.request_id, event.status)


async def serve(connection, data_dir: Path) -> None:
    """Process one delivery at a time and rely on broker redelivery on disconnect."""
    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    await channel.set_qos(prefetch_count=1)
    queue, exchange = await declare_topology(channel)
    logging.info("Broker connected; ingestion consumer ready")
    async with queue.iterator() as messages:
        async for message in messages:
            await handle_message(message, exchange, data_dir)


async def serve_dsn(connection, data_dir: Path) -> None:
    """Consume DSN station requests on a separate channel, one at a time."""
    from shared.dsn import declare_dsn_topology
    from app.dsn import handle_dsn_message

    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    await channel.set_qos(prefetch_count=1)
    queue, exchange = await declare_dsn_topology(channel)
    logging.info("Broker connected; DSN station consumer ready")
    async with queue.iterator() as messages:
        async for message in messages:
            await handle_dsn_message(message, exchange, data_dir)


async def serve_space_weather(connection, data_dir: Path) -> None:
    """Consume NOAA SWPC snapshot requests on a separate channel, one at a time."""
    from shared.space_weather import declare_space_weather_topology
    from app.space_weather import handle_space_weather_message

    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    await channel.set_qos(prefetch_count=1)
    queue, exchange = await declare_space_weather_topology(channel)
    logging.info("Broker connected; space weather consumer ready")
    async with queue.iterator() as messages:
        async for message in messages:
            await handle_space_weather_message(message, exchange, data_dir)
