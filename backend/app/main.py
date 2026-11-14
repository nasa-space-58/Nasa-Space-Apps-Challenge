"""Service health endpoint."""
from fastapi import FastAPI

app = FastAPI(title="Lunar Landing Navigator")


@app.get("/health")
def health() -> dict[str, str]:
    """Return process liveness without external requests."""
    raise NotImplementedError
