.PHONY: dev api web test test-web e2e build-web password sync

dev:            ## API + web app on 127.0.0.1, opens the browser
	./scripts/os-dev.sh

api:            ## API only (127.0.0.1:8800)
	uv run summer-hunter os serve --port 8800

web:            ## web app only, proxied to the local API
	cd apps/web && SH_API_URL=http://127.0.0.1:8800 pnpm exec next dev --hostname 127.0.0.1

password:       ## set the Summer Hunter OS password (stored hashed in .env)
	uv run summer-hunter os set-password

sync:           ## push the local database to the hosted copy and pull edits made online
	uv run summer-hunter os sync

test:           ## Python test suite
	uv run pytest -q

test-web:       ## frontend component tests
	cd apps/web && pnpm exec vitest run

e2e:            ## end-to-end tests (starts its own API and web server on a test database)
	cd apps/web && pnpm exec playwright test

build-web:
	cd apps/web && pnpm build
