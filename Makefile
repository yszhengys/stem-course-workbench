.PHONY: run frontend check ruff database lint api start-all stop-all status clean-cache worker worker-start worker-stop worker-restart
.PHONY: docker-buildx-prepare docker-buildx-clean docker-buildx-reset
.PHONY: docker-push docker-push-latest docker-release docker-build-local tag export-docs
.PHONY: release-test release-stack release-stack-down

# Get version from pyproject.toml
VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)

# Image names for both registries (fork namespace — do not push to upstream lfnovo images)
DOCKERHUB_IMAGE := yszhengys/stem-course-workbench
GHCR_IMAGE := ghcr.io/yszhengys/stem-course-workbench

# Build platforms
PLATFORMS := linux/amd64,linux/arm64

database:
	docker compose up -d surrealdb

run:
	@echo "⚠️  Warning: Starting frontend only. For full functionality, use 'make start-all'"
	cd frontend && npm run dev

frontend:
	cd frontend && npm run dev

lint:
	uv run python -m mypy .

ruff:
	ruff check . --fix

# === Docker Build Setup ===
docker-buildx-prepare:
	@docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --use --name multi-platform-builder --driver docker-container
	@docker buildx use multi-platform-builder

docker-buildx-clean:
	@echo "🧹 Cleaning up buildx builders..."
	@docker buildx rm multi-platform-builder 2>/dev/null || true
	@docker ps -a | grep buildx_buildkit | awk '{print $$1}' | xargs -r docker rm -f 2>/dev/null || true
	@echo "✅ Buildx cleanup complete!"

docker-buildx-reset: docker-buildx-clean docker-buildx-prepare
	@echo "✅ Buildx reset complete!"

# === Release Testing (see .github/RELEASE_PROCESS.md) ===

# Automated image gate: fresh install + upgrade against real images.
# Usage: make release-test TAG=1.12.0 OLD_TAG=1.11.0
release-test:
	@test -n "$(TAG)" || (echo "usage: make release-test TAG=<new> [OLD_TAG=<previous>]"; exit 1)
	bash scripts/release-test/release-image-test.sh all \
		"$(DOCKERHUB_IMAGE):$(TAG)" \
		$(if $(OLD_TAG),"$(DOCKERHUB_IMAGE):$(OLD_TAG)")

# Browsable RC stack for manual verification (optionally with a data dump).
# Usage: make release-stack TAG=1.12.0 [DUMP=/tmp/dev-dump.surql]
release-stack:
	@test -n "$(TAG)" || (echo "usage: make release-stack TAG=<tag> [DUMP=<dump.surql>]"; exit 1)
	bash scripts/release-test/rc-stack.sh up "$(TAG)" $(DUMP)

release-stack-down:
	bash scripts/release-test/rc-stack.sh down "$(or $(TAG),unused)"

# === Docker Build Targets ===

# Build production image for local platform only (no push)
docker-build-local:
	@echo "🔨 Building production image locally ($(shell uname -m))..."
	docker build \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):local \
		.
	@echo "✅ Built $(DOCKERHUB_IMAGE):$(VERSION) and $(DOCKERHUB_IMAGE):local"
	@echo "Run with: docker run -p 5055:5055 -p 8502:8502 $(DOCKERHUB_IMAGE):local"

# Build and push version tags ONLY (no latest) for both regular and single images
docker-push: docker-buildx-prepare
	@echo "📤 Building and pushing version $(VERSION) to both registries..."
	@echo "🔨 Building regular image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):$(VERSION) \
		--push \
		.
	@echo "🔨 Building single-container image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		--push \
		.
	@echo "✅ Pushed version $(VERSION) to both registries (latest NOT updated)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION)"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single"

# Update v1-latest tags to current version (both regular and single images)
docker-push-latest: docker-buildx-prepare
	@echo "📤 Updating v1-latest tags to version $(VERSION)..."
	@echo "🔨 Building regular image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):v1-latest \
		-t $(GHCR_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):v1-latest \
		--push \
		.
	@echo "🔨 Building single-container image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(DOCKERHUB_IMAGE):v1-latest-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):v1-latest-single \
		--push \
		.
	@echo "✅ Updated v1-latest to version $(VERSION)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single → v1-latest-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single → v1-latest-single"

# Full release: push version AND update latest tags
docker-release: docker-push-latest
	@echo "✅ Full release complete for version $(VERSION)"

tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Creating tag v$$version"; \
	git tag "v$$version"; \
	git push origin "v$$version"


dev:
	docker compose -f examples/docker-compose-dev.yml --project-directory . up --build

full:
	docker compose -f examples/docker-compose-full-local.yml --project-directory . up --build


api:
	uv run --env-file .env run_api.py

.PHONY: worker worker-start worker-stop worker-restart

worker: worker-start

worker-start:
	@echo "Starting surreal-commands worker..."
	uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}"

worker-stop:
	@echo "Stopping surreal-commands worker..."
	pkill -f "surreal-commands-worker" || true

worker-restart: worker-stop
	@sleep 2
	@$(MAKE) worker-start

# === Service Management ===
start-all:
	@echo "🚀 Starting Open Notebook (Database + API + Worker + Frontend)..."
	@test -f .env || (echo "❌ No .env file — run: cp .env.example .env  (host runs need SURREAL_URL=ws://127.0.0.1:8000/rpc)"; exit 1)
	@echo "📊 Starting SurrealDB..."
	@docker compose up -d surrealdb
	@sleep 3
	@echo "🔧 Starting API backend..."
	@uv run --env-file .env run_api.py &
	@sleep 3
	@echo "⚙️ Starting background worker..."
	@uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
	@sleep 2
	@echo "🌐 Starting Next.js frontend..."
	@echo "✅ All services started!"
	@echo "📱 Frontend: http://localhost:3000"
	@echo "🔗 API: http://localhost:5055"
	@echo "📚 API Docs: http://localhost:5055/docs"
	cd frontend && npm run dev

stop-all:
	@echo "🛑 Stopping all Open Notebook services..."
	@# Kill API parents first: uvicorn --reload respawns its child if only the
	@# port-holder dies. Then release the standard frontend/API ports. Caveat:
	@# the name-based pkill also matches other checkouts of this repo on the
	@# same host — when several checkouts share a machine, prefer stopping via
	@# dev-init.sh (Ctrl+C), which only touches what it started.
	@pkill -f "run_api.py" || true
	@pkill -f "uvicorn api.main:app" || true
	@lsof -t -i :5055 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
	@lsof -t -i :3000 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
	@pkill -f "surreal-commands-worker" || true
	@docker compose down
	@echo "✅ All services stopped!"

status:
	@echo "📊 Open Notebook Service Status:"
	@echo "Database (SurrealDB):"
	@docker compose ps surrealdb 2>/dev/null || echo "  ❌ Not running"
	@echo "API Backend:"
	@lsof -t -i :5055 -sTCP:LISTEN >/dev/null 2>&1 && echo "  ✅ Running (port 5055)" || echo "  ❌ Not running"
	@echo "Background Worker:"
	@pgrep -f "surreal-commands-worker" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
	@echo "Next.js Frontend:"
	@lsof -t -i :3000 -sTCP:LISTEN >/dev/null 2>&1 && echo "  ✅ Running (port 3000)" || echo "  ❌ Not running"

# === Documentation Export ===
export-docs:
	@echo "📚 Exporting documentation..."
	@uv run python scripts/export_docs.py
	@echo "✅ Documentation export complete!"

# === Cleanup ===
clean-cache:
	@echo "🧹 Cleaning cache directories..."
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -type f -delete 2>/dev/null || true
	@find . -name "*.pyo" -type f -delete 2>/dev/null || true
	@find . -name "*.pyd" -type f -delete 2>/dev/null || true
	@echo "✅ Cache directories cleaned!"