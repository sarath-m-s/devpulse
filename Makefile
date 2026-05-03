.PHONY: install dev test lint clean run-init

install:
	pip install -e .

dev:
	pip install -e ".[all,dev]"

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=devpulse --cov-report=term-missing

lint:
	python -m py_compile devpulse/**/*.py devpulse/*.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete; \
	rm -rf .pytest_cache .coverage dist build *.egg-info

run-init:
	devpulse init

run-status:
	devpulse status
