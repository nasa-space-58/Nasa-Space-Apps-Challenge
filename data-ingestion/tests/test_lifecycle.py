import asyncio
from unittest.mock import AsyncMock
import pytest
from app import main as worker


def test_worker_closes_connection_on_cancellation(monkeypatch):
    connection = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setenv("RABBITMQ_URL", "amqp://example:example@rabbitmq/")
    monkeypatch.setattr("aio_pika.connect_robust", connect)

    async def exercise():
        task = asyncio.create_task(worker.main())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    connect.assert_awaited_once_with("amqp://example:example@rabbitmq/")
    connection.close.assert_awaited_once()


def test_worker_requires_broker_configuration(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    with pytest.raises(KeyError, match="RABBITMQ_URL"):
        asyncio.run(worker.main())
