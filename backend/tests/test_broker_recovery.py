"""Bounded broker-startup retries; no weakening of ingestion delivery semantics."""

import asyncio
from unittest.mock import AsyncMock

import aio_pika
import pytest
from app import submit_ingestion as module


def test_connects_once_with_per_attempt_timeout(monkeypatch):
    connection = object()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(module.aio_pika, "connect_robust", connect)
    assert asyncio.run(module.connect_broker("amqp://fixture")) is connection
    connect.assert_awaited_once_with("amqp://fixture", timeout=5)


def test_recovers_transient_refusal_and_timeout(monkeypatch):
    connection = object()
    connect = AsyncMock(
        side_effect=[
            aio_pika.exceptions.AMQPConnectionError("refused"),
            TimeoutError(),
            connection,
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(module.aio_pika, "connect_robust", connect)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    assert (
        asyncio.run(module.connect_broker("amqp://fixture", attempts=3, delay_s=2))
        is connection
    )
    assert connect.await_count == 3
    assert sleep.await_count == 2
    sleep.assert_awaited_with(2)


def test_exhaustion_raises_last_failure_without_extra_sleep(monkeypatch):
    failure = aio_pika.exceptions.AMQPConnectionError("still unavailable")
    connect = AsyncMock(side_effect=failure)
    sleep = AsyncMock()
    monkeypatch.setattr(module.aio_pika, "connect_robust", connect)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(aio_pika.exceptions.AMQPConnectionError) as error:
        asyncio.run(module.connect_broker("amqp://fixture", attempts=2))
    assert error.value is failure
    assert connect.await_count == 2
    assert sleep.await_count == 1


@pytest.mark.parametrize("failure", [ValueError("bad URL"), asyncio.CancelledError()])
def test_configuration_errors_and_cancellation_are_not_retried(monkeypatch, failure):
    connect = AsyncMock(side_effect=failure)
    monkeypatch.setattr(module.aio_pika, "connect_robust", connect)
    with pytest.raises(type(failure)):
        asyncio.run(module.connect_broker("fixture"))
    assert connect.await_count == 1


@pytest.mark.parametrize(
    "options", [{"attempts": 0}, {"delay_s": -1}, {"connect_timeout_s": 0}]
)
def test_invalid_retry_budget_fails_before_connecting(monkeypatch, options):
    connect = AsyncMock()
    monkeypatch.setattr(module.aio_pika, "connect_robust", connect)
    with pytest.raises(ValueError):
        asyncio.run(module.connect_broker("fixture", **options))
    connect.assert_not_awaited()
