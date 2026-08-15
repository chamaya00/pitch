"""The deployment boundary, asserted against the files that define it.

Step 10.5 moved the public edge from "three published container ports" to a
single nginx proxy. The properties that makes safe are not properties of any
Python module — they live in `docker-compose.yml` and in the nginx template — so
this suite reads those two files and asserts them.

Why that is worth doing rather than trusting a review: the trusted-proxy setting
is only sound *because* the backend has no published port. Measured during this
step, against the real stack — with `RATE_LIMIT_TRUSTED_PROXIES=1` and a limit
of two identities, five requests sent **directly** to the backend with a forged
`X-Forwarded-For` created five identities, while the same five through the proxy
created none. Republishing the backend port would silently restore that bypass,
and nothing else in the repository would notice. This file notices.

The nginx *behaviour* — routing, the body cap actually rejecting, the headers
actually arriving — is verified separately by running the real proxy against the
real services; see `scripts/verify-proxy.sh`. What is asserted here is that the
configuration which ships says what the deployment needs it to say.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
TEMPLATE = ROOT / "deploy" / "nginx" / "templates" / "vocallens.conf.template"

#: The one service allowed to be reachable from outside.
PUBLIC_SERVICE = "proxy"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    parsed = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, Any]:
    return dict(compose["services"])


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def directives(template: str) -> str:
    """The template with its comments removed.

    Assertions about what is *absent* have to look at directives only. The
    template explains at length why there is no CSP and no HSTS, so a naive
    ``"Content-Security-Policy" not in template`` fails on the very comment
    documenting its absence — which is how the first version of these tests
    failed.
    """
    return "\n".join(line for line in template.splitlines() if not line.lstrip().startswith("#"))


def environment(service: dict[str, Any]) -> dict[str, str]:
    """Compose's `environment:` as a mapping, whichever form it was written in."""
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    pairs = (str(item).split("=", 1) for item in raw)
    return {p[0]: (p[1] if len(p) > 1 else "") for p in pairs}


# --- Network exposure -------------------------------------------------------


def test_only_the_proxy_publishes_a_port(services: dict[str, Any]) -> None:
    """The whole trust model rests on this.

    Measured: with the backend reachable directly and one trusted proxy hop
    configured, a forged ``X-Forwarded-For`` gets a fresh rate-limit bucket per
    invented address. The limit is real only while the proxy is the sole
    possible peer.
    """
    published = {name for name, svc in services.items() if svc.get("ports")}

    assert published == {PUBLIC_SERVICE}, f"these services publish a port: {sorted(published)}"


def test_the_database_is_not_reachable_from_the_host(services: dict[str, Any]) -> None:
    """A published PostgreSQL port bypasses every ownership predicate in the API."""
    assert not services["db"].get("ports")


def test_the_backend_is_internal_but_reachable_by_the_proxy(services: dict[str, Any]) -> None:
    assert not services["backend"].get("ports")
    assert "8000" in [str(p) for p in services["backend"].get("expose", [])]


def test_the_proxy_publishes_a_single_http_port(services: dict[str, Any]) -> None:
    ports = services[PUBLIC_SERVICE]["ports"]

    assert len(ports) == 1
    assert str(ports[0]).endswith(":80")


def test_the_proxy_depends_on_what_it_proxies(services: dict[str, Any]) -> None:
    assert set(services[PUBLIC_SERVICE]["depends_on"]) == {"backend", "frontend"}


# --- Trusted proxy ----------------------------------------------------------


def test_exactly_one_proxy_hop_is_trusted(services: dict[str, Any]) -> None:
    """One, because the proxy writes exactly one entry and discards the rest.

    A larger number would start trusting entries no proxy in this deployment
    wrote — which is to say, entries the client supplied.
    """
    assert environment(services["backend"])["RATE_LIMIT_TRUSTED_PROXIES"] == "1"


def test_the_proxy_overwrites_the_forwarded_header(template: str, directives: str) -> None:
    """``$remote_addr``, never ``$proxy_add_x_forwarded_for``.

    The latter *appends* to whatever the client sent, carrying a
    client-controlled value into the backend. Setting the header discards it.
    """
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", template)
    assert "$proxy_add_x_forwarded_for" not in directives


def test_the_backend_is_told_the_scheme_and_host(template: str) -> None:
    for header in ("X-Forwarded-Proto", "X-Forwarded-Host", "Host"):
        assert re.search(rf"proxy_set_header\s+{header}\s", template), header


# --- The body cap -----------------------------------------------------------


def edge_cap_mb(services: dict[str, Any]) -> int:
    raw = environment(services[PUBLIC_SERVICE])["VL_EDGE_MAX_BODY_MB"]
    match = re.search(r"(\d+)", raw)
    assert match, raw
    return int(match.group(1))


def app_cap_mb(services: dict[str, Any]) -> int:
    raw = environment(services["backend"])["MAX_AUDIO_SIZE_MB"]
    match = re.search(r"(\d+)", raw)
    assert match, raw
    return int(match.group(1))


def test_the_edge_cap_is_configured_at_all(template: str) -> None:
    assert re.search(r"client_max_body_size\s+\$\{VL_EDGE_MAX_BODY_MB\}m\s*;", template)


def test_the_edge_cap_equals_the_application_cap(services: dict[str, Any]) -> None:
    """Equal, and the reason is a measurement rather than a preference.

    With the edge cap 2 MiB *larger*, a 51 MiB upload was buffered by nginx,
    forwarded, then refused mid-stream by ``MaxBodySizeMiddleware`` — and nginx
    reported that as **502 Bad Gateway**, replacing the documented
    ``FILE_TOO_LARGE`` envelope with an HTML page. Any gap between the two caps
    is a band of request sizes that gets a worse answer than before the proxy
    existed.
    """
    assert edge_cap_mb(services) == app_cap_mb(services)


def test_the_edge_cap_derives_from_the_application_setting(services: dict[str, Any]) -> None:
    """Written as a reference, so the two cannot drift when one is overridden."""
    assert "MAX_AUDIO_SIZE_MB" in environment(services[PUBLIC_SERVICE])["VL_EDGE_MAX_BODY_MB"]


def test_the_edge_refusal_is_the_documented_envelope(template: str) -> None:
    """A client must not have to learn that some 413s are HTML."""
    assert "error_page 413" in template
    assert '"error_code":"FILE_TOO_LARGE"' in template
    assert '"status":"failed"' in template
    assert "The uploaded file is larger than this server accepts." in template


def test_the_body_is_buffered_before_the_upstream_is_contacted(template: str) -> None:
    """What makes an oversized upload cost no worker, no identity and no quota."""
    assert re.search(r"proxy_request_buffering\s+on\s*;", template)


def test_the_edge_message_matches_the_application_message() -> None:
    """Read from the middleware itself, so a reworded message fails here."""
    from app.core.middleware import _TOO_LARGE_MESSAGE

    assert _TOO_LARGE_MESSAGE in TEMPLATE.read_text(encoding="utf-8")


# --- Routing ----------------------------------------------------------------


def test_the_api_and_the_web_app_are_routed_to_their_own_upstreams(template: str) -> None:
    assert re.search(r"location\s+/api/\s*\{[^}]*proxy_pass\s+http://\$\{VL_BACKEND\};", template)
    assert re.search(r"location\s+/\s*\{[^}]*proxy_pass\s+http://\$\{VL_FRONTEND\};", template)


def test_the_frontend_is_built_against_the_public_origin(services: dict[str, Any]) -> None:
    """The browser reaches the API through the proxy, not on the backend's port."""
    args = services["frontend"]["build"]["args"]
    assert "PUBLIC_ORIGIN" in str(args["NEXT_PUBLIC_API_URL"])
    assert "8000" not in str(args["NEXT_PUBLIC_API_URL"])


# --- Health -----------------------------------------------------------------


def test_the_proxy_answers_its_own_health_check(template: str) -> None:
    """No upstream, no database, no identity minted, no quota spent.

    A health check that minted an identity would be a rate-limit consumer that
    runs every ten seconds forever.
    """
    match = re.search(r"location\s+=\s+/healthz\s*\{(.*?)\n    \}", template, re.S)
    assert match, "no /healthz location"
    body = match.group(1)
    assert "return 200" in body
    assert "proxy_pass" not in body


def test_every_service_has_a_health_check(services: dict[str, Any]) -> None:
    for name in ("db", "backend", PUBLIC_SERVICE):
        assert services[name].get("healthcheck"), name


# --- Headers and TLS --------------------------------------------------------


@pytest.fixture(scope="module")
def server_level(directives: str) -> str:
    """The server block's own directives, before the first ``location``.

    Headers must be set here to apply to *every* response. Asserting against
    the whole file instead was a test without teeth: deleting the server-level
    ``X-Content-Type-Options`` left an identical line inside a ``location`` and
    the test still passed. Worse, that nested line was itself a bug — nginx
    replaces rather than merges inherited ``add_header`` directives, so the
    413 went out missing two of the three headers.
    """
    start = directives.index("server {")
    end = directives.index("location", start)
    return directives[start:end]


@pytest.mark.parametrize(
    "header",
    ["X-Content-Type-Options nosniff", "Referrer-Policy", "X-Frame-Options DENY"],
)
def test_the_safe_headers_are_set_for_every_response(server_level: str, header: str) -> None:
    assert re.search(rf"add_header\s+{header}.*always\s*;", server_level)


def test_no_location_silently_drops_the_inherited_headers(directives: str) -> None:
    """nginx's ``add_header`` inheritance is replace, not merge.

    One ``add_header`` inside any ``location`` discards every header set on the
    server for responses from that block. Rather than remember to repeat all
    three everywhere, no location sets any.
    """
    first_location = directives.index("location")
    assert "add_header" not in directives[first_location:]


def test_no_false_transport_security_claim(directives: str) -> None:
    """This proxy speaks HTTP. Advertising HSTS from it would be a lie."""
    assert "Strict-Transport-Security" not in directives
    assert "ssl_certificate" not in directives
    assert "listen 443" not in directives


def test_the_edge_sets_no_content_security_policy(directives: str) -> None:
    """The web app owns the policy, and a second one here would fight it.

    A browser handed two ``Content-Security-Policy`` headers enforces both, so a
    resource must satisfy each independently. The app's policy admits its inline
    scripts by a nonce minted per request (Step 10.9); nothing written in this
    file can know that nonce, so any policy set here would forbid exactly those
    scripts. Measured in the equivalent case — the policy served without the
    nonce reaching the HTML — Chromium refused all ten script elements and the
    page never hydrated.
    """
    assert "Content-Security-Policy" not in directives


def test_the_web_app_ships_a_nonce_based_policy() -> None:
    """The other half of the decision above, asserted so it cannot quietly go.

    If `proxy.ts` were deleted the edge would still be correct, this file's
    other assertions would still pass, and the deployment would simply serve no
    policy at all. That is the failure this test exists to catch, from the side
    that decided not to set one here.
    """
    proxy = ROOT / "frontend" / "proxy.ts"

    assert proxy.exists(), "the web app's Content-Security-Policy is gone"
    source = proxy.read_text(encoding="utf-8")
    assert "Content-Security-Policy" in source
    assert "createNonce" in source


def test_the_access_log_does_not_record_client_addresses(template: str) -> None:
    """nginx's default `combined` format starts with ``$remote_addr``.

    Step 10.3 keeps the client address out of the application's logs because it
    is the thing being counted, not something worth keeping against a person.
    A proxy that logged it anyway would undo that decision at the edge.
    """
    assert re.search(r"log_format\s+vocallens\s+'([^']*)'", template)
    fmt = re.search(r"log_format\s+vocallens\s+'([^']*)'", template).group(1)
    assert "$remote_addr" not in fmt
    assert "$http_x_forwarded_for" not in fmt
    # And no header is logged, so a bearer key cannot reach the log.
    assert "$http_" not in fmt, fmt
    assert re.search(r"access_log\s+\$\{VL_ACCESS_LOG\}\s+vocallens\s*;", template)


# --- Secrets ----------------------------------------------------------------


def test_the_compose_file_carries_no_baked_in_secret(compose: dict[str, Any]) -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "sk-ant" not in text
    # Every credential is a variable with a development default, never a value
    # that would be wrong to publish.
    assert "ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}" in text
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-" in text
