# publish_docker

Phase 1 Docker wrapper for `publish_service`.

## Scope

- wraps the existing `publish_service` logic
- exposes asynchronous job submission and query APIs
- runs a single in-process worker
- runs Chrome under Xvfb inside Docker

## Environment

- runtime settings come from `publish_docker/.env.example`
- `DEEPSEEK_API_KEY` is expected to be supplied by the repository root `.env`
- the compose file loads both `../.env` and `publish_docker/.env.example`

## Endpoints

- `GET /api/v1/health`
- `POST /api/v1/jobs/publish`
- `GET /api/v1/jobs/{job_id}`

## Run

```bash
docker compose -f publish_docker/docker-compose.yml build
docker compose -f publish_docker/docker-compose.yml up -d
```

Default host port: `18000`

## Health check

```bash
curl http://127.0.0.1:18000/api/v1/health
```

## Stop

```bash
docker compose -f publish_docker/docker-compose.yml down
```

## Notes

- Cookie acquisition is not part of this wrapper.
- Phase 1 is single-worker and sequential by design.
- Logs are written to the mounted `logs/` directory.
- If the host already has a service on port `8000`, keep the default `HOST_SERVICE_PORT=18000` or set another free host port.
