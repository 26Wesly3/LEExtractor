#!/usr/bin/env bash
# LEExtractor deployment bootstrap (v0.9.13) for the competition host:
# Ubuntu 24.04 · 2 vCPU · 2 GB RAM · 40 GB disk.
#
# Idempotent: re-running pulls the branch, rebuilds the image and restarts the
# container without touching data/, models/, cache/ or .env.
#
#   git clone --branch feature/v0.9.13 https://github.com/26Wesly3/LEExtractor.git /opt/leextractor/repo
#   sudo bash /opt/leextractor/repo/deploy/bootstrap.sh
#
# Overridable: LEEXTRACTOR_ROOT, LEEXTRACTOR_BRANCH, LEEXTRACTOR_REPO,
#              LEEXTRACTOR_NO_BUILD=1 (skip the image build).

set -euo pipefail

ROOT="${LEEXTRACTOR_ROOT:-/opt/leextractor}"
BRANCH="${LEEXTRACTOR_BRANCH:-feature/v0.9.13}"
REPO_URL="${LEEXTRACTOR_REPO:-https://github.com/26Wesly3/LEExtractor.git}"
APP_UID=10001          # matches the non-root user inside the image
HEALTH_PORT=8012

step() { printf '\n=== %s ===\n' "$*"; }
docker_() { if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi; }

step "1/6 directories under $ROOT"
sudo mkdir -p "$ROOT"/{data,models,cache,logs,caddy/data,caddy/config}
sudo chown -R "$APP_UID:$APP_UID" "$ROOT"/data "$ROOT"/models "$ROOT"/cache "$ROOT"/logs
ls -ld "$ROOT"/*

step "2/6 code"
if [ -d "$ROOT/repo/.git" ]; then
    git -C "$ROOT/repo" fetch --all --quiet
    git -C "$ROOT/repo" checkout --quiet "$BRANCH"
    git -C "$ROOT/repo" pull --ff-only --quiet || echo "note: not fast-forward; keeping local HEAD"
else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$ROOT/repo"
fi
git -C "$ROOT/repo" rev-parse HEAD | sudo tee "$ROOT/DEPLOYED_COMMIT" >/dev/null
echo "deployed commit: $(sudo cat "$ROOT/DEPLOYED_COMMIT")  branch: $BRANCH"

step "3/6 compose files"
cp "$ROOT/repo/docker-compose.yml" "$ROOT/docker-compose.yml"
rm -rf "$ROOT/deploy"
cp -r "$ROOT/repo/deploy" "$ROOT/deploy"
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/repo/.env.example" "$ROOT/.env"
    chmod 600 "$ROOT/.env"
    echo "created $ROOT/.env from the template — edit before the public demo"
else
    chmod 600 "$ROOT/.env"
fi

cd "$ROOT"
step "4/6 image build (slow on 2 vCPU: Node bundle + Python deps)"
if [ "${LEEXTRACTOR_NO_BUILD:-0}" = "1" ]; then
    echo "skipped (LEEXTRACTOR_NO_BUILD=1)"
else
    docker_ compose build
    docker_ images | head -5
fi

step "5/6 start"
# Always with the proxy profile: without it compose treats caddy as an
# unwanted service and stops the reverse proxy that serves HTTPS.
docker_ compose --profile proxy up -d
sleep 10
docker_ compose ps

step "6/6 health"
for path in /api/health/live /api/health/ready /api/health; do
    code=$(curl -s -o /tmp/leextractor-body -w '%{http_code}' "http://127.0.0.1:${HEALTH_PORT}${path}" || echo 000)
    printf '%-20s HTTP %s  %s\n' "$path" "$code" "$(head -c 220 /tmp/leextractor-body 2>/dev/null)"
done
echo
echo "host memory / swap:"
free -m | head -3
echo
echo "next: set LEEXTRACTOR_DOMAIN + LEEXTRACTOR_PUBLIC_HOSTS in $ROOT/.env,"
echo "      then: docker compose --profile proxy up -d   (Caddy + HTTPS)"
