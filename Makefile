# Dalston Makefile
#
# Simplifies common development and deployment commands.
# Run `make help` to see all available targets.

.PHONY: help dev dev-minimal dev-gpu dev-observability stop logs logs-all ps \
        build-cpu build-gpu build-engine \
        aws-start aws-stop aws-logs \
        health clean validate test lint

# Default target
help:
	@echo "Dalston Development Commands"
	@echo ""
	@echo "Local Development:"
	@echo "  make dev             - Start full local stack (postgres, redis, minio, gateway, orchestrator, CPU engines)"
	@echo "  make dev-minimal     - Start minimal stack (infra + gateway + faster-whisper only)"
	@echo "  make dev-gpu         - Start with GPU engines (requires NVIDIA GPU)"
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
	@echo ""
	@echo "AWS Deployment:"
	@echo "  make aws-start       - Start on AWS with local infra + GPU"
	@echo "  make aws-stop        - Stop AWS services"
	@echo "  make aws-logs        - Follow logs on AWS"
	@echo ""
	@echo "Testing & Validation:"
	@echo "  make test            - Run all tests"
	@echo "  make lint            - Run linters (ruff, mypy)"
	@echo "  make validate        - Validate compose configurations"
	@echo "  make health          - Check service health"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean           - Remove stopped containers and unused images"

# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

# Start full local stack with all CPU engines
dev:
	docker compose --profile local-infra --profile local-object-storage up -d --build

# Start minimal stack for quick iteration
dev-minimal:
	docker compose --profile local-infra --profile local-object-storage up -d --build \
		gateway orchestrator \
		stt-batch-prepare stt-batch-transcribe-faster-whisper stt-batch-merge

# Start with GPU engines (requires NVIDIA GPU)
dev-gpu:
	docker compose --profile local-infra --profile local-object-storage --profile gpu up -d --build

# Start with observability stack (jaeger, prometheus, grafana)
dev-observability:
	docker compose --profile local-infra --profile local-object-storage --profile observability up -d

# Stop all services (all profiles)
stop:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile observability down

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
# Note: NeMo transcription is GPU-only; faster-whisper handles CPU transcription
build-cpu:
	docker compose build \
		stt-batch-prepare \
		stt-batch-transcribe-faster-whisper \
		stt-batch-align-whisperx-cpu \
		stt-batch-diarize-pyannote-3.1-cpu \
		stt-batch-pii-detect-presidio \
		stt-batch-merge \
		stt-rt-transcribe-parakeet-rnnt-0.6b-cpu

# Build GPU engine variants
build-gpu:
	docker compose --profile gpu build

# Build a specific engine
# Usage: make build-engine ENGINE=stt-batch-transcribe-faster-whisper
build-engine:
ifndef ENGINE
	$(error ENGINE is required. Usage: make build-engine ENGINE=<service-name>)
endif
	docker compose build $(ENGINE)

# Rebuild and restart a specific engine
# Usage: make rebuild ENGINE=stt-batch-transcribe-faster-whisper
rebuild:
ifndef ENGINE
	$(error ENGINE is required. Usage: make rebuild ENGINE=<service-name>)
endif
	docker compose up -d --build $(ENGINE)

# ============================================================
# AWS DEPLOYMENT
# ============================================================

# Start on AWS with local infra + GPU
aws-start:
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
	pytest

# Run tests with coverage
test-cov:
	pytest --cov=dalston --cov-report=html

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

# Remove stopped containers and unused images
clean:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile observability down --remove-orphans
	docker system prune -f

# Deep clean: remove all containers, images, and volumes
clean-all:
	docker compose --profile local-infra --profile local-object-storage --profile gpu --profile observability down --remove-orphans --volumes
	docker system prune -af --volumes

# Show queue depths
queues:
	@docker compose exec -T redis redis-cli KEYS "dalston:queue:*" 2>/dev/null | while read key; do \
		echo "$$key: $$(docker compose exec -T redis redis-cli LLEN $$key 2>/dev/null)"; \
	done || echo "Redis not running"

# Show system status
status:
	@curl -s http://localhost:8000/v1/system/status 2>/dev/null | python -m json.tool || echo "Gateway not running"
