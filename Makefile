.PHONY: bootstrap format lint test test-portable package clean-install verify verify-portable release install-user clean

bootstrap:
	./scripts/bootstrap-dev.sh

format: bootstrap
	.venv/bin/python -m ruff check --fix src tests packaging scripts

lint: bootstrap
	.venv/bin/python -m ruff check src tests packaging scripts

test: bootstrap
	.venv/bin/python -m pytest tests -q

test-portable: bootstrap
	./scripts/verify.sh --no-bootstrap --portable --skip-build

package: bootstrap
	rm -rf build dist
	.venv/bin/python -m build --no-isolation
	.venv/bin/python -m twine check dist/*

clean-install: package
	.venv/bin/python scripts/verify_clean_install.py

verify:
	./scripts/verify.sh

verify-portable:
	./scripts/verify.sh --portable

release:
	./scripts/publish_release.sh

install-user:
	./scripts/install-user.sh

clean:
	rm -rf build dist .pytest_cache .ruff_cache .coverage htmlcov
	find src tests packaging scripts -type d -name __pycache__ -prune -exec rm -rf {} +
