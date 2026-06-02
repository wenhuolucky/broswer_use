# Publish Docker Phase 1 Design

## Metadata

- Date: 2026-06-02
- Scope: `publish_service` only
- Goal: package the current article publishing service into Docker without affecting the existing local service code path
- Non-goals:
  - do not containerize login / cookie acquisition
  - do not introduce cross-account parallel execution in phase 1
  - do not add distributed queueing or persistence in phase 1

## Problem Statement

The current `publish_service` is callable as a FastAPI service, but it is not yet suitable for isolated Docker deployment as a self-contained publishing unit. The main blockers are:

1. browser execution is tightly coupled to a local GUI Chromium environment
2. requests execute publishing inline instead of via a worker model
3. the current service has no queue abstraction for later concurrency expansion
4. browser runtime assumptions are Windows-oriented and need a container runtime wrapper

Phase 1 solves these issues by introducing an isolated Docker-facing wrapper service under `/publish_docker` while keeping the existing `publish_service` implementation reusable.

## Design Summary

Create a new directory `publish_docker/` that contains a Docker-oriented wrapper service with:

1. a new FastAPI entrypoint for job submission and job status queries
2. an in-memory job store
3. a single in-process background worker
4. a Docker runtime with Chrome + Xvfb
5. direct reuse of the existing `PublishService.publish(...)` implementation

This phase intentionally keeps execution single-worker and single-task-at-a-time. The wrapper introduces the correct architecture boundary now, so phase 2 can add cross-account concurrency without redesigning the service shape.

## Why This Shape

This design avoids modifying the existing `publish_service` request semantics in-place and avoids entangling Docker concerns with the current local development flow. It also avoids the false simplicity of "just put uvicorn in a container", because the browser publishing path is long-running and browser-dependent. A job-based wrapper is a better fit for container deployment and future worker scaling.

## Directory Layout

The new files live under:

```text
publish_docker/
  Dockerfile
  docker-compose.yml
  entrypoint.sh
  .env.example
  README.md
  app/
    __init__.py
    api.py
    models.py
    queue_store.py
    server.py
    settings.py
    worker.py
```

The existing directories below remain authoritative for publishing behavior and are imported by the new wrapper:

- `publish_service/`
- `platforms/`
- `browser_utils.py`

## Runtime Architecture

Phase 1 runs as a single container with two logical components inside the same Python process:

1. HTTP API
2. background worker

The API accepts requests and places jobs into an in-memory `asyncio.Queue`.

The worker consumes jobs one-by-one and calls the current `PublishService.publish(...)` method. This ensures:

- single active browser task at a time
- no CDP port contention inside phase 1
- no clipboard contention across concurrent publish jobs
- a clean extension point for later multi-worker designs

## API Design

### 1. Submit Publish Job

`POST /api/v1/jobs/publish`

Request payload uses multipart form fields aligned with the existing service:

- `title`: required
- `content`: required
- `cookie_file`: optional, mutually exclusive with `cookie_text`
- `cookie_text`: optional, mutually exclusive with `cookie_file`
- `cover_image`: optional
- `cover_image_url`: optional

Response:

```json
{
  "code": 202,
  "job_id": "uuid",
  "status": "queued",
  "message": "job accepted"
}
```

### 2. Query Job Status

`GET /api/v1/jobs/{job_id}`

Response fields:

- `job_id`
- `status`: `queued` | `running` | `succeeded` | `failed`
- `created_at`
- `started_at`
- `finished_at`
- `result`: publish result when finished
- `error`: normalized error string when failed

### 3. Health Endpoint

`GET /api/v1/health`

Response fields:

- `status`
- `worker_state`: `idle` or `busy`
- `queue_size`
- `version`

## Job Lifecycle

Each submitted request becomes a `PublishJob` record in the in-memory store.

State transitions:

1. `queued`
2. `running`
3. `succeeded` or `failed`

Jobs are retained in memory for a bounded time window so callers can fetch results after completion. Phase 1 keeps this simple with TTL-based retention and background cleanup.

## Worker Model

The worker is started during FastAPI startup. It blocks on the queue, processes one job at a time, and updates job state as it progresses.

Execution rules:

1. exactly one active publish job at any time
2. all browser work is isolated to the worker
3. worker catches exceptions and records normalized failure output
4. API thread does not wait for browser completion

This model intentionally serializes work even if multiple jobs are queued. That is acceptable for phase 1 because the goal is Docker runtime correctness, not throughput.

## Reuse of Existing Publish Logic

The wrapper will instantiate and call the current `PublishService` directly instead of rewriting publish behavior.

That means phase 1 preserves:

- cookie parsing rules
- markdown-to-rich-text conversion
- Toutiao browser automation flow
- existing request logging structure
- existing cover image handling

The wrapper adds orchestration only.

## Docker Runtime Design

The container runtime must supply a browser-capable Linux environment for the existing Playwright flow.

Phase 1 image contents:

- Python runtime
- system dependencies for Chrome
- Google Chrome
- Xvfb
- Chinese fonts
- project Python dependencies from `requirements.txt`

The container startup script will:

1. start `Xvfb :99`
2. export `DISPLAY=:99`
3. start the wrapper API with uvicorn

No remote browser UI is included in phase 1. Specifically:

- no noVNC
- no x11vnc
- no login automation service

## Docker Build and Compose Layout

`publish_docker/docker-compose.yml` will build from the repository root so the wrapper can include both the new directory and the existing service code:

- build context: repository root
- dockerfile path: `publish_docker/Dockerfile`

The container exposes:

- `8000` for HTTP API

Volumes:

- host `logs/` mapped into container logs path
- optional runtime directory for temporary files if needed

## Environment Variables

Phase 1 standardizes runtime through environment variables:

- `SERVICE_HOST=0.0.0.0`
- `SERVICE_PORT=8000`
- `LOG_LEVEL=INFO`
- `BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome`
- `PUBLISH_QUEUE_MAX_SIZE=20`
- `JOB_RETENTION_HOURS=24`

These values live in `publish_docker/.env.example`.

## Required Code Adaptations

Phase 1 is designed to minimize impact on existing code, but a few compatibility adjustments are expected:

1. the wrapper service must convert multipart input into the same call shape expected by `PublishService.publish(...)`
2. browser path resolution must work in Linux via `BROWSER_EXECUTABLE_PATH`
3. service binding must use `0.0.0.0` inside Docker

No functional rewrite of the browser publishing logic is planned in this phase.

## Failure Handling

Expected failure cases:

- invalid or expired cookie
- Chrome startup failure
- clipboard / rich-text paste failure in the editor
- article publish validation failure in Toutiao
- external network timeout

Failure handling rules:

1. worker marks the job as `failed`
2. normalized error text is stored on the job record
3. request-level logs remain in `logs/requests/{request_id}.log`
4. temporary files are cleaned after job execution

## Logging

Phase 1 preserves existing structured logging from `publish_service`.

Additional wrapper-level logging is added for:

- job accepted
- job started
- job completed
- job failed
- queue depth changes

Wrapper logs should include both:

- `job_id`
- underlying `request_id` when available

This keeps queue-level orchestration and publish-level diagnostics correlated.

## Testing Strategy

Phase 1 testing is focused on deployment correctness rather than load testing.

Required verification:

1. build the Docker image successfully
2. start the container successfully
3. `GET /api/v1/health` returns healthy state
4. submit a publish job and receive `202`
5. query job status until terminal state
6. confirm the worker launches Chrome under Xvfb
7. confirm logs are written to mounted log directory

Deferred from phase 1:

- cross-account concurrency testing
- multi-worker scheduling
- queue persistence across restarts

## Risks and Constraints

Phase 1 has known limits:

1. only one active publish job at a time
2. in-memory queue means queued jobs are lost on container restart
3. rich-text publishing still depends on browser behavior in Toutiao's editor
4. the image will be relatively large because it includes Chrome and Xvfb

These are accepted constraints for this phase.

## Phase 2 Compatibility

This design intentionally prepares for phase 2 cross-account concurrency by introducing the correct seams now:

- queue abstraction
- explicit worker module
- job state model
- Docker entrypoint independent from current local startup path

Phase 2 can evolve by:

1. adding `account_key` to job metadata
2. replacing the in-memory queue/store with Redis or equivalent
3. running multiple worker containers
4. enforcing same-account serialization while allowing cross-account parallelism

No phase 1 contract blocks that evolution.

## Acceptance Criteria

Phase 1 is complete when:

1. `publish_docker/` exists and is self-contained
2. Docker image builds successfully from repository root
3. container starts Chrome-capable runtime with Xvfb
4. API accepts publish jobs asynchronously
5. a single worker processes queued jobs sequentially
6. the wrapper reuses existing `publish_service` publish logic
7. existing local `publish_service` startup path remains untouched
