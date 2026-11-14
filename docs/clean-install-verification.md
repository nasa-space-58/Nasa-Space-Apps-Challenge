# Clean installation verification — 2026-11-14

## Tested source and isolation

Source commit: `2707be25259298e95e31a82d464a2fdc3f74f9cf` on
`feature/horizons-ingestion`. A fresh `git clone --no-local` of the local repository
was created at `/tmp/nasa-clean-check.alXwPz/repo`; Git status was clean.
This verifies the local commit, not a GitHub checkout or deployment.

Docker Compose v5.3.1 on Linux ARM64 containers. The separate Compose project
`nasa-clean-alxwpz` received new RabbitMQ, application-data and node_modules
volumes. Existing application containers and volumes were not reused.
`build --no-cache` rebuilt all three application images from the fresh clone.
The tagged base images were already present; this was not a base-image download
or Docker-engine installation test.

An external Compose override removed published ports to avoid colliding with
the existing development environment; service definitions were otherwise the
committed configuration. HTTP checks ran inside the isolated network/containers.

## Commands

Executed from the fresh clone, with the external override:

```yaml
services:
  rabbitmq:
    ports: !reset []
  backend:
    ports: !reset []
  frontend:
    ports: !reset []
```

For brevity, `dc` below means:

```bash
docker compose -p nasa-clean-alxwpz --env-file .env.example \
  -f docker-compose.yml -f /tmp/nasa-clean-check.alXwPz/isolated-compose.yml
```

Checks performed:

```text
dc build --no-cache
dc up -d --wait --wait-timeout 180
dc run --rm --no-deps backend sh -c 'pip check && pytest -q && ruff check app tests /workspace/shared'
dc run --rm --no-deps data-ingestion sh -c 'pip check && pytest -q && ruff check app tests'
dc run --rm --no-deps frontend sh -c 'npm ci --no-fund && npm test && npm run build && npm audit'
./scripts/lock-python.sh
git diff --exit-code -- backend/requirements.lock data-ingestion/requirements.lock
```

## Results

| Check | Result |
|---|---|
| Python installation | Both images install hashed locks successfully |
| Dependency consistency | pip check: no broken requirements in either service |
| Backend | 30 tests passed; Ruff passed |
| Ingestion | 39 tests passed; Ruff passed |
| Frontend | npm ci, 1 test and TypeScript/Vite build passed |
| npm audit | Zero reported vulnerabilities |
| Total tests | 70 passed; no frozen tests changed |
| Backend liveness | HTTP 200; status ok |
| Frontend | HTTP 200 and app root present via localhost |
| Broker connection | Worker logged successful RabbitMQ connection |
| Data isolation | Backend's new /data volume was empty |
| Lock regeneration | No changes to either lockfile |
| Git status | Clean after installation and test artifacts |

The Vite development server rejects an unapproved `Host: frontend` with HTTP
403. The normal localhost host returned HTTP 200 both inside the frontend
container and across the isolated network. No host-allowlist change was needed.

The backend test suite emits one upstream Starlette/AnyIO deprecation warning;
it does not fail the tests. Python package vulnerability auditing was not part
of this check. Clean-install tests use offline Horizons fixtures, not new NASA
requests. Terrain download and geographic coverage remain unverified.

After verification, only the isolated test project's containers, volumes,
network and application images were removed. The main development environment
continues to run. Local build logs are retained under ignored
`data/verification/2026-11-14/`.
