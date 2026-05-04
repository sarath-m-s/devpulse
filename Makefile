.PHONY: install dev test lint clean run-init

install:
	pip install -e .

dev:
	pip install -e ".[all,dev]"

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=ghost_pulse --cov-report=term-missing

lint:
	python -m py_compile ghost_pulse/**/*.py ghost_pulse/*.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete; \
	rm -rf .pytest_cache .coverage dist build *.egg-info

run-init:
	ghost init

run-status:
	ghost status
