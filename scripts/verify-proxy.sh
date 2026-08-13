#!/usr/bin/env bash
# Run the shipped nginx template against locally running services.
#
# There is no Docker daemon in every development environment, and a proxy
# config that is only ever read is not a proxy config that works. This script
# substitutes the same template the container substitutes — same file, same
# variables, only the upstreams differ — and starts a real nginx in front of a
# locally running backend and frontend, so the routing, the body cap, the
# forwarding header and the security headers can all be exercised for real.
#
#   ./scripts/verify-proxy.sh start   # substitute, validate, start nginx
#   ./scripts/verify-proxy.sh stop
#
# Environment:
#   VL_PROXY_PORT   public port for the local proxy   (default 8080)
#   VL_BACKEND      upstream API                      (default 127.0.0.1:8000)
#   VL_FRONTEND     upstream web                      (default 127.0.0.1:3100)
#   VL_EDGE_MAX_BODY_MB                               (default 52)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${VL_PROXY_RUN_DIR:-/tmp/vocallens-proxy}"
PORT="${VL_PROXY_PORT:-8080}"

export VL_LISTEN_PORT="$PORT"
export VL_BACKEND="${VL_BACKEND:-127.0.0.1:8000}"
export VL_FRONTEND="${VL_FRONTEND:-127.0.0.1:3100}"
export VL_EDGE_MAX_BODY_MB="${VL_EDGE_MAX_BODY_MB:-52}"
export VL_ACCESS_LOG="${VL_ACCESS_LOG:-$RUN/logs/access.log}"

case "${1:-start}" in
  start)
    mkdir -p "$RUN/conf" "$RUN/logs" "$RUN/body" "$RUN/tmp"
    # The same substitution the nginx image performs on /etc/nginx/templates.
    envsubst '${VL_LISTEN_PORT} ${VL_BACKEND} ${VL_FRONTEND} ${VL_EDGE_MAX_BODY_MB} ${VL_ACCESS_LOG}' \
      < "$ROOT/deploy/nginx/templates/vocallens.conf.template" \
      > "$RUN/conf/vocallens.conf"

    cat > "$RUN/nginx.conf" <<EOF
worker_processes 1;
error_log $RUN/logs/error.log warn;
pid $RUN/nginx.pid;
events { worker_connections 256; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    client_body_temp_path $RUN/body;
    proxy_temp_path $RUN/tmp;
    fastcgi_temp_path $RUN/tmp/fastcgi;
    uwsgi_temp_path $RUN/tmp/uwsgi;
    scgi_temp_path $RUN/tmp/scgi;
    include $RUN/conf/vocallens.conf;
}
EOF
    nginx -t -c "$RUN/nginx.conf"
    nginx -c "$RUN/nginx.conf"
    echo "proxy listening on http://localhost:$PORT -> api=$VL_BACKEND web=$VL_FRONTEND"
    ;;
  stop)
    [ -f "$RUN/nginx.pid" ] && nginx -c "$RUN/nginx.conf" -s quit 2>/dev/null || true
    sleep 1
    pkill -f "nginx.*$RUN" 2>/dev/null || true
    echo "proxy stopped"
    ;;
  *)
    echo "usage: $0 [start|stop]" >&2
    exit 2
    ;;
esac
