"""Submit SWPC snapshot requests via RabbitMQ; the backend never contacts NOAA."""

from shared.space_weather import SpaceWeatherEvent, SpaceWeatherRequest


async def submit_space_weather(
    request: SpaceWeatherRequest, broker_url: str, timeout_s: float = 300
) -> SpaceWeatherEvent:
    """Publish a persistent request and await its correlated event."""
    raise NotImplementedError
