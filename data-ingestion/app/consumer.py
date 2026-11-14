"""RabbitMQ request consumer; completion precedes acknowledgement."""
from pathlib import Path
from app.jobs import process_job


async def handle_message(message, exchange, data_dir: Path) -> None:
    """Validate, process, publish confirmed completion, then acknowledge."""
    raise NotImplementedError


async def serve(connection, data_dir: Path) -> None:
    """Run the durable consumer with bounded concurrency until cancellation."""
    raise NotImplementedError
