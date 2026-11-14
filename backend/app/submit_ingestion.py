"""Submit ingestion requests through RabbitMQ, without external data access."""


async def submit(request, broker_url: str, timeout_s: float = 600):
    """Publish persistently and await this request's durable result queue."""
    raise NotImplementedError


def main():
    """Read an ingestion JSON envelope and print its correlated result."""
    raise NotImplementedError


if __name__ == '__main__':
    main()
