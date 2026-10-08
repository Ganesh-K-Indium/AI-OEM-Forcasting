.PHONY: setup api worker web test test-fast test-e2e types build up down
PY=backend/.venv/bin
setup:
	cd backend && python3.13 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,foundation]"
	cd frontend && npm install
api:
	cd backend && PYTHONPATH=. $(abspath $(PY))/uvicorn app.main:app --reload
web:
	cd frontend && npm run dev
test-fast:
	cd backend && PYTHONPATH=. $(abspath $(PY))/pytest -q -m "not slow and not postgres"
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
