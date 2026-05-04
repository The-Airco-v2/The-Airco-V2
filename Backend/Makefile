ENV_FILE ?= .env.production
MODE ?= incremental
OUTPUT_FILE ?= .env.production
COMPOSE ?= $(shell if docker compose version >/dev/null 2>&1; then printf '%s' 'docker compose'; elif docker-compose --version >/dev/null 2>&1; then printf '%s' 'docker-compose'; else printf '%s' 'docker compose'; fi)
COMPOSE_FILE ?= docker-compose.yml
INFRA_SERVICES := timescaledb redis minio centrifugo go2rtc mediamtx triton

.PHONY: up down logs test migrate seed rebuild ps compose-config render-prod-env up-infra wait-core deploy-apps health-check

up:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d

down:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down

logs:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f --tail=100

test:
	python -m pip install -e "shared[test]" && pytest tests -v

migrate:
	bash scripts/run_migrations.sh $(ENV_FILE)

seed:
	python scripts/seed_cameras.py

rebuild:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) build --no-cache

ps:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) ps

compose-config:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) config

render-prod-env:
	./scripts/render_env.py --template .env.production.template --output $(OUTPUT_FILE)

up-infra:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d $(INFRA_SERVICES)

wait-core:
	bash scripts/wait_for_core_services.sh $(ENV_FILE)

deploy-apps:
	bash scripts/deploy_apps.sh $(MODE) $(ENV_FILE)

health-check:
	bash scripts/health_check.sh $(ENV_FILE)

# Individual service logs
log-%:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f --tail=100 $*
