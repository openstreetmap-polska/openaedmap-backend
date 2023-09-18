.PHONY: build version dev-start dev-stop dev-logs

build:
	docker load < $$(nix-build --no-out-link)

version:
	sed -i -r "s|VERSION = '([0-9.]+)'|VERSION = '\1.$$(date +%y%m%d)'|g" config.py
	sed -i -r "s|VERSION_TIMESTAMP = ([0-9.]+)|VERSION_TIMESTAMP = $$(date +%s)|g" config.py

dev-start:
	docker compose -f docker-compose.dev.yml up -d

dev-stop:
	docker compose -f docker-compose.dev.yml down

dev-logs:
	docker compose -f docker-compose.dev.yml logs -f
