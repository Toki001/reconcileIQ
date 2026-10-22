UV := $(shell command -v uv 2>/dev/null || echo .tools/uv/bin/uv)
export UV_CACHE_DIR := $(CURDIR)/.cache/uv

.PHONY: fixtures fixtures-check seed-financial env install contracts contracts-check check format bootstrap bootstrap-lite dev api worker down
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
	$(UV) run --all-packages pytest --cov=reconcileiq_reconciliation --cov-fail-under=90
	$(MAKE) contracts-check
	$(MAKE) fixtures-check
	$(UV) run --all-packages reconcileiq-eval
	pnpm lint
	pnpm typecheck
	pnpm test
	pnpm format:check

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

seed-financial:
	$(UV) run --all-packages python -m reconcileiq_database.financial_seed
