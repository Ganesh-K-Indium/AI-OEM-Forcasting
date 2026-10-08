.PHONY: setup api worker web test test-db test-fast test-e2e types build up down
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
