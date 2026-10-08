.PHONY: dev-up dev-down dev-logs docker-up docker-down docker-restart docker-reset docker-backend docker-web docker-logs docker-ps setup api worker web test test-db test-fast test-e2e types build up down
PY=backend/.venv/bin
setup:
	cd backend && python3.13 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,foundation]"
	cd frontend && npm install
api:
	cd backend && PYTHONPATH=. $(abspath $(PY))/uvicorn app.main:app --reload
web:
	cd frontend && npm run dev
test-db:  ## throw-away PostgreSQL+pgvector for the tests on :5544
	-docker rm -f oem-testdb >/dev/null 2>&1
	docker run -d --name oem-testdb -e POSTGRES_USER=oem -e POSTGRES_PASSWORD=oem -e POSTGRES_DB=oem_test -p 5544:5432 pgvector/pgvector:pg16
test-fast:
	cd backend && PYTHONPATH=. $(abspath $(PY))/pytest -q -m "not slow"
test-e2e:
	cd backend && PYTHONPATH=. $(abspath $(PY))/pytest -q -m slow tests/test_e2e_api.py
test: test-fast test-e2e
types:
	cd frontend && npm run gen:types
build:
	cd frontend && npm run typecheck && npm run build
up:
	docker compose up --build
down:
	docker compose down

# ---- Docker stack (postgres, redis, api, worker, frontend) -------------------------------------------------------
docker-up:       ## build + start everything in the background → http://localhost:3000
	docker compose up -d --build
	@docker compose ps
docker-down:     ## stop and remove containers; DATA IS KEPT (named volumes)
	docker compose down
docker-restart:  ## stop, rebuild, start — data kept (use after pulling / editing code)
	docker compose down && docker compose up -d --build
docker-reset:    ## DELETE ALL DATA (workspaces, users, forecasts) and start from scratch
	docker compose down -v && docker compose up -d --build
docker-backend:  ## rebuild only api + worker after backend changes
	docker compose up -d --build api worker
docker-web:      ## rebuild only the frontend after UI changes
	docker compose up -d --build frontend
docker-logs:     ## follow api + worker logs
	docker compose logs -f api worker
docker-ps:
	docker compose ps

# ---- Fast dev loop in Docker: code is mounted, saves hot-reload, no rebuilds ----------------------------------------
DEV=docker compose -f docker-compose.yml -f docker-compose.dev.yml
dev-up:          ## postgres + redis + api (--reload) + worker (watchfiles) + Next dev server on :3000
	-docker compose stop frontend
	$(DEV) up -d postgres redis api worker web-dev
	@echo "UI http://localhost:3000 (first start installs npm packages ~1 min) · API http://localhost:8000/docs"
dev-down:        ## stop the dev stack; data kept
	$(DEV) down
dev-logs:
	$(DEV) logs -f api worker web-dev
