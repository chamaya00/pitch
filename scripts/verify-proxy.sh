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
#   ./scripts/verify-proxy.sh start       # substitute, validate, start nginx
#   ./scripts/verify-proxy.sh start-tls   # the same, terminating TLS
#   ./scripts/verify-proxy.sh stop
#
# `start-tls` mounts the TLS entry point instead of the plain one and generates
# a self-signed certificate for localhost, so the HTTPS path — the redirect,
# HSTS, the protocols, and everything a secure context unlocks in the browser —
# can be exercised without a domain or a certificate authority. A self-signed
# certificate is the *only* difference from the shipped configuration; the
# template, the shared body and the substitution are identical.
#
# Environment:
#   VL_PROXY_PORT   public port for the local proxy   (default 8080)
#   VL_TLS_PORT     TLS port for start-tls            (default 8443)
#   VL_BACKEND      upstream API                      (default 127.0.0.1:8000)
#   VL_FRONTEND     upstream web                      (default 127.0.0.1:3100)
#   VL_EDGE_MAX_BODY_MB                               (default 52)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${VL_PROXY_RUN_DIR:-/tmp/vocallens-proxy}"
PORT="${VL_PROXY_PORT:-8080}"

TLS_PORT="${VL_TLS_PORT:-8443}"

export VL_LISTEN_PORT="$PORT"
export VL_BACKEND="${VL_BACKEND:-127.0.0.1:8000}"
export VL_FRONTEND="${VL_FRONTEND:-127.0.0.1:3100}"
export VL_EDGE_MAX_BODY_MB="${VL_EDGE_MAX_BODY_MB:-52}"
export VL_ACCESS_LOG="${VL_ACCESS_LOG:-$RUN/logs/access.log}"
# Where the shared body lands here. Under Docker it is /etc/nginx/conf.d.
export VL_INCLUDE="$RUN/conf/_vocallens.inc"

# Every variable either template uses. Passing the list explicitly is what stops
# envsubst from eating nginx's own `$host`, `$scheme` and friends.
SUBST='${VL_LISTEN_PORT} ${VL_TLS_PORT} ${VL_BACKEND} ${VL_FRONTEND} ${VL_EDGE_MAX_BODY_MB} ${VL_ACCESS_LOG} ${VL_INCLUDE} ${VL_SERVER_NAME} ${VL_TLS_CERT} ${VL_TLS_KEY} ${VL_HSTS} ${VL_ACME_WEBROOT}'

substitute() {
    # $1: the entry-point template to use.
    mkdir -p "$RUN/conf" "$RUN/logs" "$RUN/body" "$RUN/tmp" "$RUN/acme"
    envsubst "$SUBST" < "$ROOT/deploy/nginx/common/_vocallens.inc.template" \
      > "$RUN/conf/_vocallens.inc"
    envsubst "$SUBST" < "$1" > "$RUN/conf/vocallens.conf"
}

# `http2 on;` needs nginx >= 1.25.1. The pinned image is 1.27; a development
# machine may be older. Rather than quietly exercising a different file than the
# one shipped, drop the single directive and say so — everything else, including
# TLS itself, is still the shipped configuration.
drop_http2_if_unsupported() {
    local version
    version="$(nginx -v 2>&1 | sed 's/.*nginx\///; s/ .*//')"
    if [ "$(printf '%s\n1.25.1\n' "$version" | sort -V | head -1)" != "1.25.1" ]; then
        if grep -q '^\s*http2 on;' "$RUN/conf/vocallens.conf"; then
            sed -i '/^\s*http2 on;/d' "$RUN/conf/vocallens.conf"
            echo "note: local nginx $version predates \`http2 on;\` — directive removed for this run."
            echo "      HTTP/2 is therefore NOT exercised locally. The shipped image (1.27) supports it."
        fi
    fi
}

# A certificate for localhost, and nothing else. Self-signed on purpose: this
# proves the configuration terminates TLS, not that any authority vouches for
# it. Regenerated on each run, so an expired certificate can never be the
# confusing failure somebody spends an hour on.
generate_certificate() {
    mkdir -p "$RUN/tls"
    openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
      -keyout "$RUN/tls/privkey.pem" -out "$RUN/tls/fullchain.pem" \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null
    chmod 600 "$RUN/tls/privkey.pem"
}

# The surrounding nginx.conf the image supplies and this script must stand in
# for. One copy, both modes, so HTTP and TLS are exercised in the same context.
write_nginx_conf() {
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
}

case "${1:-start}" in
  start)
    substitute "$ROOT/deploy/nginx/templates/vocallens.conf.template"
    write_nginx_conf
    nginx -t -c "$RUN/nginx.conf"
    nginx -c "$RUN/nginx.conf"
    echo "proxy listening on http://localhost:$PORT -> api=$VL_BACKEND web=$VL_FRONTEND"
    ;;
  start-tls)
    export VL_TLS_PORT="$TLS_PORT"
    export VL_SERVER_NAME="${VL_SERVER_NAME:-localhost}"
    export VL_TLS_CERT="$RUN/tls/fullchain.pem"
    export VL_TLS_KEY="$RUN/tls/privkey.pem"
    export VL_ACME_WEBROOT="${VL_ACME_WEBROOT:-$RUN/acme}"
    export VL_HSTS="${VL_HSTS:-max-age=31536000; includeSubDomains}"

    substitute "$ROOT/deploy/nginx/templates-tls/vocallens.conf.template"
    generate_certificate
    write_nginx_conf
    drop_http2_if_unsupported
    nginx -t -c "$RUN/nginx.conf"
    nginx -c "$RUN/nginx.conf"
    echo "proxy listening on https://localhost:$TLS_PORT (self-signed) and http://localhost:$PORT (redirects)"
    echo "  -> api=$VL_BACKEND web=$VL_FRONTEND"
    ;;
  stop)
    [ -f "$RUN/nginx.pid" ] && nginx -c "$RUN/nginx.conf" -s quit 2>/dev/null || true
    sleep 1
    pkill -f "nginx.*$RUN" 2>/dev/null || true
    echo "proxy stopped"
    ;;
  *)
    echo "usage: $0 [start|start-tls|stop]" >&2
    exit 2
    ;;
esac
