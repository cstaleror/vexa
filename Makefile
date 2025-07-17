.PHONY: all setup env force-env build-bot-image build up down ps logs test migrate makemigrations init-db stamp-db migrate-or-init migration-status check_docker

# Default target: Sets up everything and starts the services
all: env build-bot-image build up migrate-or-init

# Target to set up only the environment without Docker
setup-env: env
	@echo "Environment setup complete."
	@echo "The 'env' target handles .env creation/preservation:"
	@echo "  - If .env exists, it is preserved."
	@echo "  - If .env does not exist, it is created from env-example."
	@echo "To force an overwrite of an existing .env file, use 'make force-env'."

# Target to perform all initial setup steps
setup: setup-env build-bot-image
	@echo "Setup complete."

# Check if Docker daemon is running
check_docker:
	@echo "---> Checking if Docker is running..."
	@if ! docker info > /dev/null 2>&1; then \
	    echo "ERROR: Docker is not running. Please start Docker Desktop or Docker daemon first."; \
	    exit 1; \
	fi
	@echo "---> Docker is running."

# Include .env file if it exists for environment variables 
-include .env

# Create .env file from example
env:
	@echo "---> Setting up environment..."
	@if [ -f .env ]; then \
	    echo "*** .env file already exists. Keeping existing file. ***"; \
	    echo "*** To force recreation, delete .env first or use 'make force-env'. ***"; \
	else \
	    cp env-example .env; \
	    echo "*** .env file created from env-example. Please review and add your OPENAI_API_KEY. ***"; \
	fi

# Force create .env file from example (overwrite existing)
force-env:
	@echo "---> Creating .env file (forcing overwrite)..."
	@cp env-example .env
	@echo "*** .env file created from env-example. Please review and add your OPENAI_API_KEY. ***"

# Default bot image tag if not specified in .env
BOT_IMAGE_NAME ?= vexa-bot:dev

# Build the standalone vexa-bot image
build-bot-image: check_docker
	@if [ -f .env ]; then \
	    ENV_BOT_IMAGE_NAME=$$(grep BOT_IMAGE_NAME .env | cut -d= -f2); \
	    if [ -n "$$ENV_BOT_IMAGE_NAME" ]; then \
	        echo "---> Building $$ENV_BOT_IMAGE_NAME image (from .env)..."; \
	        docker build -t $$ENV_BOT_IMAGE_NAME -f services/vexa-bot/core/Dockerfile ./services/vexa-bot/core; \
	    else \
	        echo "---> Building $(BOT_IMAGE_NAME) image (BOT_IMAGE_NAME not found in .env)..."; \
	        docker build -t $(BOT_IMAGE_NAME) -f services/vexa-bot/core/Dockerfile ./services/vexa-bot/core; \
	    fi; \
	else \
	    echo "---> Building $(BOT_IMAGE_NAME) image (.env file not found)..."; \
	    docker build -t $(BOT_IMAGE_NAME) -f services/vexa-bot/core/Dockerfile ./services/vexa-bot/core; \
	fi

# Build Docker Compose service images
build: check_docker
	@echo "---> Building Docker images..."
	@docker compose build

# Start services in detached mode
up: check_docker
	@echo "---> Starting Docker Compose services..."
	@docker compose up -d

# Stop services
down: check_docker
	@echo "---> Stopping Docker Compose services..."
	@docker compose down

# Show container status
ps: check_docker
	@docker compose ps

# Tail logs for all services
logs:
	@docker compose logs -f

# Run the interaction test script
test: check_docker
	@echo "---> Running test script..."
	@echo "---> API Documentation URLs:"
	@if [ -f .env ]; then \
	    API_PORT=$$(grep -E '^[[:space:]]*API_GATEWAY_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
	    ADMIN_PORT=$$(grep -E '^[[:space:]]*ADMIN_API_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
	    [ -z "$$API_PORT" ] && API_PORT=8056; \
	    [ -z "$$ADMIN_PORT" ] && ADMIN_PORT=8057; \
	    echo "    Main API:  http://localhost:$$API_PORT/docs"; \
	    echo "    Admin API: http://localhost:$$ADMIN_PORT/docs"; \
	else \
	    echo "    Main API:  http://localhost:8056/docs"; \
	    echo "    Admin API: http://localhost:8057/docs"; \
	fi
	@chmod +x run_vexa_interaction.sh
	@./run_vexa_interaction.sh

# --- Database Migration Commands ---

# Smart migration: detects if database is fresh, legacy, or already Alembic-managed.
migrate-or-init: check_docker
	@echo "---> Starting smart database migration/initialization..."; \
	set -e; \
	if ! docker-compose ps -q postgres | grep -q .; then \
	    echo "ERROR: PostgreSQL container is not running. Please run 'make up' first."; \
	    exit 1; \
	fi; \
	echo "---> Waiting for database to be ready..."; \
	count=0; \
	while ! docker-compose exec -T postgres pg_isready -U postgres -d vexa -q; do \
	    if [ $$count -ge 12 ]; then \
	        echo "ERROR: Database did not become ready in 60 seconds."; \
	        exit 1; \
	    fi; \
	    echo "Database not ready, waiting 5 seconds..."; \
	    sleep 5; \
	    count=$$((count+1)); \
	done; \
	echo "---> Database is ready. Checking its state..."; \
	if docker-compose exec -T postgres psql -U postgres -d vexa -t -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version';" | grep -q 1; then \
	    echo "STATE: Alembic-managed database detected."; \
	    echo "ACTION: Running standard migrations to catch up to 'head'..."; \
	    $(MAKE) migrate; \
	elif docker-compose exec -T postgres psql -U postgres -d vexa -t -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'meetings';" | grep -q 1; then \
	    echo "STATE: Legacy (non-Alembic) database detected."; \
	    echo "ACTION: Stamping at 'base' and migrating to 'head' to bring it under Alembic control..."; \
	    docker-compose exec -T transcription-collector alembic -c /app/alembic.ini stamp base; \
	    $(MAKE) migrate; \
	else \
	    echo "STATE: Fresh, empty database detected."; \
	    echo "ACTION: Creating schema directly from models and stamping at revision dc59a1c03d1f..."; \
	    docker-compose exec -T transcription-collector python -c "import asyncio; from shared_models.database import init_db; asyncio.run(init_db())"; \
	    docker-compose exec -T transcription-collector alembic -c /app/alembic.ini stamp dc59a1c03d1f; \
	fi; \
	echo "---> Smart database migration/initialization complete!"

# Apply all pending migrations to bring database to latest version
migrate: check_docker
	@echo "---> Applying database migrations..."
	@if ! docker-compose ps postgres | grep -q "Up"; then \
	    echo "ERROR: PostgreSQL container is not running. Please run 'make up' first."; \
	    exit 1; \
	fi
	@echo "---> Running alembic upgrade head..."
	@docker-compose exec -T transcription-collector alembic -c /app/alembic.ini upgrade head

# Create a new migration file based on model changes
makemigrations: check_docker
	@if [ -z "$(M)" ]; then \
	    echo "Usage: make makemigrations M=\"your migration message\""; \
	    echo "Example: make makemigrations M=\"Add user profile table\""; \
	    exit 1; \
	fi
	@echo "---> Creating new migration: $(M)"
	@if ! docker-compose ps postgres | grep -q "Up"; then \
	    echo "ERROR: PostgreSQL container is not running. Please run 'make up' first."; \
	    exit 1; \
	fi
	@docker-compose exec -T transcription-collector alembic -c /app/alembic.ini revision --autogenerate -m "$(M)"

# Initialize the database (first time setup) - creates tables and stamps with latest revision
init-db: check_docker
	@echo "---> Initializing database and stamping with Alembic..."
	@docker-compose run --rm transcription-collector python -c "import asyncio; from shared_models.database import init_db; asyncio.run(init_db())"
	@docker-compose run --rm transcription-collector alembic -c /app/alembic.ini stamp head
	@echo "---> Database initialized and stamped."

# Stamp existing database with current version (for existing installations)
stamp-db: check_docker
	@echo "---> Stamping existing database with current migration version..."
	@if ! docker-compose ps postgres | grep -q "Up"; then \
	    echo "ERROR: PostgreSQL container is not running. Please run 'make up' first."; \
	    exit 1; \
	fi
	@docker-compose exec -T transcription-collector alembic -c /app/alembic.ini stamp head
	@echo "---> Database stamped successfully!"

# Show current migration status
migration-status: check_docker
	@echo "---> Checking migration status..."
	@if ! docker-compose ps postgres | grep -q "Up"; then \
	    echo "ERROR: PostgreSQL container is not running. Please run 'make up' first."; \
	    exit 1; \
	fi
	@echo "---> Current database version:"
	@docker-compose exec -T transcription-collector alembic -c /app/alembic.ini current
	@echo "---> Migration history:"
	@docker-compose exec -T transcription-collector alembic -c /app/alembic.ini history --verbose