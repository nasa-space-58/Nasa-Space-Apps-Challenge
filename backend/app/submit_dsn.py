"""Submit DSN ephemeris requests via RabbitMQ; the backend never contacts NASA."""

from shared.dsn import DsnEvent, DsnRequest


async def submit_dsn(
    request: DsnRequest, broker_url: str, timeout_s: float = 600
) -> DsnEvent:
    """Publish a persistent request and await its correlated event."""
    raise NotImplementedError
