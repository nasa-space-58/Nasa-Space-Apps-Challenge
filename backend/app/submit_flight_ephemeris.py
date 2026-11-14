"""Submit flight ephemeris requests via RabbitMQ; the backend never contacts JPL."""

from shared.flight_ephemeris import FlightEphemerisEvent, FlightEphemerisRequest


async def submit_flight_ephemeris(
    request: FlightEphemerisRequest, broker_url: str, timeout_s: float = 300
) -> FlightEphemerisEvent:
    """Publish a persistent request and await its correlated event."""
    raise NotImplementedError
