# Maintainer prerequisites: Node 22+, npm, uv; packaging targets macOS arm64.
# make pack produces a self-contained, ad-hoc signed DMG + ZIP in release/.
# macOS may require Privacy & Security > Open Anyway (not notarized).
# Commit release code first, then make bump [VERSION=minor|major|1.2.3-beta.1].
# bump preserves unrelated staged work and NEVER pushes. Push the printed command
# to trigger GitHub Releases; prerelease versions are marked as prereleases.
.DEFAULT_GOAL := dev
VERSION ?= patch
PYTHON := .build/python/cpython-3.12.11-macos-aarch64-none/bin/python3

.PHONY: dev build python test pack bump graph

# Requires graphifyy (pip install graphifyy); AST-only, no model/API calls.
graph:
	graphify extract . --code-only --no-cluster
	graphify cluster-only . --no-label

dev:
	npm run dev

build:
	npm run build

python:
	bash scripts/prepare-python.sh

test: python
	npm run typecheck
	node tests/test_desktop_bootstrap.cjs
	$(PYTHON) -m unittest discover -s tests

pack:
	npm run dist
	node scripts/verify-package.cjs

bump:
	npm version "$(VERSION)" --no-git-tag-version --allow-same-version
	@v=$$(node -p "require('./package.json').version") && \
	  git commit --only --allow-empty -m "chore(release): v$$v" -- package.json package-lock.json && \
	  git tag -a "v$$v" -m "v$$v" && \
	  echo "Release commit + tag created. Push with: git push --atomic origin $$(git branch --show-current) v$$v"
