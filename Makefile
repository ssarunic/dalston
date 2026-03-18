# Dalston Makefile
#
# Simplifies common development and deployment commands.
# Run `make help` to see all available targets.

.PHONY: help dev dev-minimal dev-gpu dev-riva dev-observability stop logs logs-all ps \
        build-cpu build-gpu build-engine deploy-web \
        aws-start aws-stop aws-logs \
        health clean clean-local validate test lint test-openai-sdk-live \
        test-elevenlabs-sdk-live test-e2e runtime-freshness runtime-freshness-required \
        sync-test-stack docker-gc-soft docker-gc-hard docker-gc-auto

# Python interpreter used for pytest-driven targets.
# Prefer local virtualenv when present for consistent dependency resolution.
PYTHON_TEST ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3.12; fi)
PYTEST_CMD = $(PYTHON_TEST) -m pytest

# Default target
help:
	@echo "Dalston Development Commands"
	@echo ""
	@echo "Local Development:"
	@echo "  make dev             - Start full local stack (postgres, redis, minio, gateway, orchestrator, CPU engines)"
	@echo "  make dev-minimal     - Start minimal stack (infra + gateway + transcribe + align + merge)"
	@echo "  make dev-gpu         - Start with GPU engines (requires NVIDIA GPU)"
	@echo "  make dev-riva        - Start with Riva NIM engines (requires NVIDIA GPU)"
	@echo "  make dev-observability - Start with monitoring stack (jaeger, prometheus, grafana)"
	@echo "  make stop            - Stop all services"
	@echo "  make logs            - Follow gateway logs"
	@echo "  make logs-all        - Follow all service logs"
	@echo "  make ps              - Show running services"
	@echo ""
	@echo "Building:"
	@echo "  make build-cpu       - Build CPU engine variants (for Mac development)"
	@echo "  make build-gpu       - Build GPU engine variants (requires NVIDIA GPU)"
	@echo "  make build-engine ENGINE=<name> - Build a specific engine"
	@echo "  make deploy-web      - Rebuild gateway with latest web console changes"
	@echo ""
	@echo "AWS Deployment:"
	@echo "  make aws-start       - Start on AWS with local infra + GPU"
	@echo "  make aws-stop        - Stop AWS services"
	@echo "  make aws-logs        - Follow logs on AWS"
	@echo ""
	@echo "Testing & Validation:"
	@echo "  make test            - Run all tests"
	@echo "  make test-e2e        - Run e2e suite with freshness + GC guards"
	@echo "  make test-openai-sdk-live - Run live OpenAI SDK parity tests (requires DALSTON_API_KEY)"
	@echo "  make test-elevenlabs-sdk-live - Run live ElevenLabs SDK parity tests (requires DALSTON_API_KEY)"
	@echo "  make runtime-freshness - Check running container revision freshness"
	@echo "  make sync-test-stack - Rebuild currently running services with current git revision"
	@echo "  make docker-gc-auto  - Docker GC with soft/hard auto escalation"
	@echo "  make lint            - Run linters (ruff, mypy)"
	@echo "  make validate        - Validate compose configurations"
	@echo "  make health          - Check service health"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean           - Remove stopped containers and unused images"
	@echo "  make clean-local     - Kill local Python processes (orchestrator, gateway)"

# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

# Kill local Python processes that would conflict with Docker services
# This prevents zombie processes from previous sessions stealing Redis events
clean-local:
	@echo "Killing local dalston processes..."
	@pkill -f "dalston\.orchestrator" 2>/dev/null || true
	@pkill -f "dalston\.gateway" 2>/dev/null || true
	@pkill -f "dalston\.session_router" 2>/dev/null || true
	@echo "Done."

# Start full local stack with all CPU engines
dev: clean-local
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose --profile local-infra --profile local-object-storage up -d --build

# Start minimal stack for quick iteration
dev-minimal: clean-local
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose --profile local-infra --profile local-object-storage up -d --build \
		gateway orchestrator \
		stt-prepare stt-transcribe-faster-whisper-cpu stt-align-phoneme-cpu

# Start with GPU engines (requires NVIDIA GPU)
dev-gpu: clean-local
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose --profile local-infra --profile local-object-storage --profile gpu up -d --build

# Start with Riva NIM engines (requires NVIDIA GPU for NIM sidecar)
dev-riva: clean-local
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose --profile local-infra --profile local-object-storage --profile riva up -d --build

# Start with observability stack (jaeger, prometheus, grafana)
dev-observability:
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose --profile local-infra --profile local-object-storage --profile observability up -d

# Stop all services (all profiles)
stop:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile riva --profile observability down

# Follow gateway logs
logs:
	docker compose logs -f gateway

# Follow all service logs
logs-all:
	docker compose logs -f

# Show running services
ps:
	docker compose ps

# ============================================================
# BUILDING
# ============================================================

# Build CPU engine variants (for Mac development)
build-cpu:
	docker compose build \
		stt-prepare \
		stt-transcribe-faster-whisper \
		stt-transcribe-nemo-cpu \
		stt-transcribe-onnx-cpu \
		stt-transcribe-faster-whisper-cpu \
		stt-align-phoneme-cpu \
		stt-diarize-pyannote-4.0-cpu \
		stt-diarize-nemo-msdd-cpu \
		stt-pii-detect-presidio

# Build GPU engine variants
build-gpu:
	docker compose --profile gpu build

# Build a specific engine
# Usage: make build-engine ENGINE=stt-transcribe-faster-whisper
build-engine:
ifndef ENGINE
	$(error ENGINE is required. Usage: make build-engine ENGINE=<service-name>)
endif
	docker compose build $(ENGINE)

# Rebuild and restart a specific engine
# Usage: make rebuild ENGINE=stt-transcribe-faster-whisper
rebuild:
ifndef ENGINE
	$(error ENGINE is required. Usage: make rebuild ENGINE=<service-name>)
endif
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose up -d --build $(ENGINE)

# Rebuild gateway with latest web console changes
deploy-web:
	docker compose build gateway
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose up -d gateway

# ============================================================
# AWS DEPLOYMENT
# ============================================================

# Start on AWS with local infra + GPU
aws-start:
	@DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) \
		docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
		--env-file .env.aws \
		--profile local-infra --profile gpu up -d

# Stop AWS services
aws-stop:
	docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
		--env-file .env.aws \
		--profile local-infra --profile gpu down

# Follow logs on AWS
aws-logs:
	docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
		--env-file .env.aws logs -f

# Show AWS service status
aws-ps:
	docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
		--env-file .env.aws ps

# ============================================================
# TESTING & VALIDATION
# ============================================================

# Run all tests
test:
	$(PYTEST_CMD)

# Run end-to-end suite with runtime freshness and disk guards
test-e2e: runtime-freshness-required docker-gc-auto
	$(PYTEST_CMD) -m e2e tests/e2e

# Run persisted live OpenAI SDK compatibility tests (M61)
# Requires DALSTON_API_KEY. Optional: DALSTON_OPENAI_BASE_URL
test-openai-sdk-live: runtime-freshness-required docker-gc-auto
	@if [ -z "$$DALSTON_API_KEY" ]; then \
		echo "DALSTON_API_KEY is required"; \
		exit 1; \
	fi
	@DALSTON_OPENAI_BASE_URL=$${DALSTON_OPENAI_BASE_URL:-http://127.0.0.1:8000/v1} \
		$(PYTEST_CMD) -q tests/integration/test_openai_sdk_contract.py
	@DALSTON_OPENAI_BASE_URL=$${DALSTON_OPENAI_BASE_URL:-http://127.0.0.1:8000/v1} \
		$(PYTEST_CMD) -q -m e2e tests/e2e/test_openai_sdk.py

# Run persisted live ElevenLabs SDK compatibility tests (M62)
# Requires DALSTON_API_KEY. Optional: DALSTON_ELEVENLABS_BASE_URL
test-elevenlabs-sdk-live: runtime-freshness-required docker-gc-auto
	@if [ -z "$$DALSTON_API_KEY" ]; then \
		echo "DALSTON_API_KEY is required"; \
		exit 1; \
	fi
	@DALSTON_ELEVENLABS_BASE_URL=$${DALSTON_ELEVENLABS_BASE_URL:-http://127.0.0.1:8000} \
		$(PYTEST_CMD) -q tests/integration/test_elevenlabs_sdk_contract.py
	@DALSTON_ELEVENLABS_BASE_URL=$${DALSTON_ELEVENLABS_BASE_URL:-http://127.0.0.1:8000} \
		$(PYTEST_CMD) -q -m e2e tests/e2e/test_elevenlabs_sdk.py

# Run tests with coverage
test-cov:
	$(PYTEST_CMD) --cov=dalston --cov-report=html

# Run linters
lint:
	ruff check dalston/ engines/ tests/
	mypy dalston/

# Format code
fmt:
	ruff format dalston/ engines/ tests/
	ruff check --fix dalston/ engines/ tests/

# Validate compose configurations
validate:
	@echo "Validating base compose..."
	@docker compose config > /dev/null
	@echo "Validating local-infra profile..."
	@docker compose --profile local-infra config > /dev/null
	@echo "Validating local-object-storage profile..."
	@docker compose --profile local-object-storage config > /dev/null
	@echo "Validating gpu profile..."
	@docker compose --profile gpu config > /dev/null
	@echo "Validating riva profile..."
	@docker compose --profile riva config > /dev/null
	@echo "Validating AWS override..."
	@S3_BUCKET=test AWS_REGION=eu-west-2 docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml config > /dev/null
	@echo "All compose configurations valid"

# Check service health
health:
	@echo "=== Gateway ==="
	@curl -s http://localhost:8000/health 2>/dev/null | python -m json.tool || echo "Not running"
	@echo ""
	@echo "=== Redis ==="
	@docker compose exec -T redis redis-cli ping 2>/dev/null || echo "Not running"
	@echo ""
	@echo "=== Postgres ==="
	@docker compose exec -T postgres pg_isready 2>/dev/null || echo "Not running"
	@echo ""
	@echo "=== MinIO ==="
	@curl -s http://localhost:9000/minio/health/live 2>/dev/null && echo "OK" || echo "Not running"

# ============================================================
# UTILITIES
# ============================================================

# Runtime freshness checks for Docker stack
runtime-freshness:
	@scripts/check-runtime-freshness.sh

runtime-freshness-required:
	@scripts/check-runtime-freshness.sh --require-running

# Rebuild currently running services with current git revision marker
sync-test-stack:
	@services="$$(docker compose ps --status running --services | tr '\n' ' ' | xargs)"; \
	if [ -z "$$services" ]; then \
		echo "No running services found. Start stack first (for example: make dev-minimal)."; \
		exit 1; \
	fi; \
	echo "Rebuilding running services: $$services"; \
	DALSTON_RUNTIME_REVISION=$$(git rev-parse HEAD) docker compose up -d --build $$services

# Docker garbage collection modes
docker-gc-soft:
	@scripts/docker-gc.sh soft

docker-gc-hard:
	@scripts/docker-gc.sh hard

docker-gc-auto:
	@scripts/docker-gc.sh auto

# Remove stopped containers and unused images
clean:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile riva --profile observability down --remove-orphans
	docker system prune -f

# Deep clean: remove all containers, images, and volumes
clean-all:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile riva --profile observability down --remove-orphans --volumes
	docker system prune -af --volumes

# Show queue depths
queues:
	@docker compose exec -T redis redis-cli KEYS "dalston:queue:*" 2>/dev/null | while read key; do \
		echo "$$key: $$(docker compose exec -T redis redis-cli LLEN $$key 2>/dev/null)"; \
	done || echo "Redis not running"

# Show system status
status:
	@curl -s http://localhost:8000/v1/system/status 2>/dev/null | python -m json.tool || echo "Gateway not running"
