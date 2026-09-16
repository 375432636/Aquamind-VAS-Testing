#!/usr/bin/env bash
set -Eeuo pipefail
: "${APP_REVISION:?Set the tested commit SHA}"
[[ "$APP_REVISION" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid commit SHA'; exit 1; }
export DEPLOY_ROOT="${DEPLOY_ROOT:-/home/deep-edge/aquamind-vas-testing}"
export APP_UID="$(id -u)" APP_GID="$(id -g)"
export APP_IMAGE="aquamind-vas-testing:${APP_REVISION}"
mkdir -p "$DEPLOY_ROOT/data/reports" "$DEPLOY_ROOT/releases"
[[ -s "$DEPLOY_ROOT/private/web.env" ]] || { echo 'Missing deployment login configuration'; exit 1; }
exec 9>"$DEPLOY_ROOT/deploy.lock"
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
previous="$DEPLOY_ROOT/current.env"
old_image='' old_revision=''
if [[ -f "$previous" ]]; then
  old_image=$(sed -n 's/^APP_IMAGE=//p' "$previous")
  old_revision=$(sed -n 's/^APP_REVISION=//p' "$previous")
fi
# Docker's local layer cache is retained; no global image/container cleanup.
# Debian and PyPI are reachable directly on 5090; the proxy can fail or stall
# package downloads. Scope the bypass to this build and preserve other proxies.
build_no_proxy="${NO_PROXY:-${no_proxy:-localhost,127.0.0.1}},deb.debian.org,pypi.org,files.pythonhosted.org"
docker build --network host --build-arg HTTP_PROXY --build-arg HTTPS_PROXY \
  --build-arg "NO_PROXY=$build_no_proxy" --build-arg "no_proxy=$build_no_proxy" \
  -t "$APP_IMAGE" .
compose=(docker compose -f deploy/compose.yaml)
if ! "${compose[@]}" up -d --wait --wait-timeout 120; then
  echo 'New service did not become healthy.'
  if [[ -n "$old_image" && -n "$old_revision" && -f "$DEPLOY_ROOT/releases/$old_revision.compose.yaml" ]]; then
    APP_IMAGE="$old_image" APP_REVISION="$old_revision" docker compose -f "$DEPLOY_ROOT/releases/$old_revision.compose.yaml" up -d --wait --wait-timeout 120
    echo "Restored $old_revision"
  fi
  exit 1
fi
cp deploy/compose.yaml "$DEPLOY_ROOT/releases/$APP_REVISION.compose.yaml"
printf 'APP_IMAGE=%s\nAPP_REVISION=%s\n' "$APP_IMAGE" "$APP_REVISION" > "$previous.tmp"
mv "$previous.tmp" "$previous"
echo "Deployed $APP_REVISION"
curl --fail --silent http://127.0.0.1:19225/healthz
