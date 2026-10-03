# Maintainer prerequisites: Node 22+, npm, uv; packaging targets macOS arm64.
# make pack produces a self-contained, ad-hoc signed DMG + ZIP in release/.
# macOS may require Privacy & Security > Open Anyway (not notarized).
# Commit release code first, then make bump [VERSION=minor|major|1.2.3-beta.1].
# bump preserves unrelated staged work and NEVER pushes. Push the printed command
# to trigger GitHub Releases; prerelease versions are marked as prereleases.
# Installed macOS arm64 builds check stable GitHub releases on startup and from
# the app menu. No Apple certificate is needed: the custom installer verifies
# the GitHub ZIP digest, safely extracts contained files/links, and checks bundle
# identity, version, arm64 executable and signature before a confirmed restart.
# ZIP assets require a GitHub SHA-256 digest; no latest-mac.yml is required.
# This verifies integrity against trusted GitHub metadata, not publisher identity.
# Install in a writable Applications folder, not a mounted DMG/translocated app.
# Existing releases without this updater need one manual installation first.
# Installer log: ~/Library/Application Support/Unity Asset Index/updates/install.log
# Previous app: .ual-update-*/previous.app beside the installed bundle; retained
# for manual recovery. Remove obsolete backups only after confirming the update.
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
	node --test tests/release/*.test.cjs
	node tests/test_updates.cjs
	node tests/test_update_install.cjs
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
