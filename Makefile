UV := $(shell command -v uv 2>/dev/null || echo .tools/uv/bin/uv)
export UV_CACHE_DIR := $(CURDIR)/.cache/uv

.PHONY: fixtures fixtures-check seed-financial env install contracts contracts-check check reliability-check format bootstrap bootstrap-lite dev api worker down
env:
	python3 scripts/setup_env.py

install: env
	$(UV) sync --all-packages --frozen
	pnpm install --frozen-lockfile

contracts:
	$(UV) run --all-packages python scripts/export_contract.py
	pnpm generate:client
	pnpm generate:graphql

contracts-check:
	$(UV) run --all-packages python scripts/export_contract.py --check
	$(UV) run --all-packages python scripts/check_generated.py

check:
	$(UV) run --all-packages ruff check .
	$(UV) run --all-packages ruff format --check .
	$(UV) run --all-packages mypy
	$(UV) run --all-packages pytest --cov=reconcileiq_reconciliation --cov-fail-under=100
	$(MAKE) contracts-check
	$(MAKE) fixtures-check
	$(UV) run --all-packages reconcileiq-eval
	$(UV) run --all-packages python -m reconcileiq_ai.structured_evaluate
	pnpm lint
	pnpm typecheck
	pnpm test
	pnpm format:check

reliability-check:
	RECONCILE_TEST_POSTGRES=1 RECONCILE_TEST_SERVICES=1 $(UV) run --all-packages pytest --cov=reconcileiq_reconciliation --cov=reconcileiq_worker --cov=reconcileiq_database.financial --cov-branch --cov-report=term-missing --cov-report=json:.cache/m05-coverage.json
	$(UV) run --all-packages python scripts/check_reliability_coverage.py

format:
	$(UV) run --all-packages ruff check --fix .
	$(UV) run --all-packages ruff format .
	pnpm format

bootstrap-lite: install
	$(UV) run --all-packages python scripts/bootstrap_lite.py
	pnpm build

bootstrap: install
	docker compose build api
	docker compose build web minio
	docker compose up -d --no-build --wait --wait-timeout 300
	docker compose exec -T api python scripts/database_smoke.py
	docker compose exec -T api python scripts/worker_smoke.py

dev:
	pnpm dev

api:
	$(UV) run --all-packages uvicorn reconcileiq_api.main:app --host 127.0.0.1 --port 8000 --reload

worker:
	RECONCILE_WORKER_ENABLED=true $(UV) run --all-packages celery -A reconcileiq_worker.app:celery_app worker --loglevel=WARNING

down:
	docker compose down

fixtures:
	$(UV) run --all-packages python scripts/financial_fixtures.py

fixtures-check:
	$(UV) run --all-packages python scripts/financial_fixtures.py --check
	$(UV) run --all-packages python scripts/golden_fixtures.py --check

seed-financial:
	$(UV) run --all-packages python -m reconcileiq_database.financial_seed

.PHONY: release-images release-config release-db-check
release-images:
	docker build --target api -f infra/docker/python.Dockerfile -t reconcileiq-api:release-local .
	docker build --target worker -f infra/docker/python.Dockerfile -t reconcileiq-worker:release-local .
	docker build -f infra/docker/web.Dockerfile -t reconcileiq-web:release-local .

release-config:
	$(UV) run --all-packages python scripts/check_release_config.py

release-db-check:
	$(UV) run --all-packages python scripts/verify_release_database.py

.PHONY: benchmark-init observability-up observability-down benchmark-baseline benchmark-sustained benchmark-burst benchmark-faults observability-check
benchmark-init:
	$(UV) run --all-packages python scripts/benchmark_env.py init

observability-up: benchmark-init
	$(UV) run --all-packages python scripts/benchmark_env.py compose up -d --build --wait --wait-timeout 300

observability-down:
	$(UV) run --all-packages python scripts/benchmark_env.py compose down

benchmark-baseline:
	$(UV) run --all-packages python scripts/benchmark_env.py run python scripts/performance_benchmark.py baseline

benchmark-sustained:
	$(UV) run --all-packages python scripts/benchmark_env.py run python scripts/performance_benchmark.py sustained

benchmark-burst:
	$(UV) run --all-packages python scripts/benchmark_env.py run python scripts/performance_benchmark.py burst

benchmark-faults:
	$(UV) run --all-packages python scripts/benchmark_env.py run python scripts/performance_benchmark.py faults

observability-check:
	$(UV) run --all-packages python scripts/benchmark_env.py run python scripts/observability_smoke.py verify
