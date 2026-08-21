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
TLS_COMPOSE = ROOT / "docker-compose.tls.yml"

NGINX = ROOT / "deploy" / "nginx"
#: The plaintext entry point, and the one that terminates TLS. Two listeners.
TEMPLATE = NGINX / "templates" / "vocallens.conf.template"
TLS_TEMPLATE = NGINX / "templates-tls" / "vocallens.conf.template"
#: Everything the proxy *does*, shared by both so they cannot drift apart.
SHARED = NGINX / "common" / "_vocallens.inc.template"

#: The one service allowed to be reachable from outside.
PUBLIC_SERVICE = "proxy"


def effective(entry_point: Path) -> str:
    """An entry point with its shared body spliced in, as nginx assembles it.

    Step 13 split the proxy into a listener and a body so that the plaintext and
    TLS configurations could share the second without duplicating it. These
    tests are about the configuration that *runs*, so they read what the
    ``include`` resolves to rather than the two halves separately — which also
    keeps ``server_level`` below meaning what it always meant.
    """
    text = entry_point.read_text(encoding="utf-8")
    body = SHARED.read_text(encoding="utf-8")
    assert "include ${VL_INCLUDE};" in text, f"{entry_point.name} includes no shared body"
    return text.replace("include ${VL_INCLUDE};", body)


def without_comments(text: str) -> str:
    """Directives only.

    Assertions about what is *absent* have to look at directives only. These
    files explain at length why there is no CSP and no HSTS, so a naive
    ``"Content-Security-Policy" not in template`` fails on the very comment
    documenting its absence — which is how the first version of these tests
    failed.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


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
    """The plaintext deployment, as it runs."""
    return effective(TEMPLATE)


@pytest.fixture(scope="module")
def directives(template: str) -> str:
    return without_comments(template)


@pytest.fixture(scope="module")
def tls_template() -> str:
    """The TLS deployment, as it runs."""
    return effective(TLS_TEMPLATE)


@pytest.fixture(scope="module")
def tls_directives(tls_template: str) -> str:
    return without_comments(tls_template)


@pytest.fixture(scope="module")
def tls_compose() -> dict[str, Any]:
    parsed = yaml.safe_load(TLS_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


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

    # The effective configuration, not the entry point: the refusal lives in the
    # shared body that both listeners include.
    assert _TOO_LARGE_MESSAGE in effective(TEMPLATE)
    assert _TOO_LARGE_MESSAGE in effective(TLS_TEMPLATE)


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
    match = re.search(r"location\s+=\s+/healthz\s*\{(.*?)\n\s*\}", template, re.S)
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

    Since Step 10.21 the *API* has a policy of its own too, set on the response
    for the opposite reason: nothing it serves is a document with scripts in it,
    so there is no nonce to know and the policy can be a constant. Both arrive
    from an upstream, and both would be intersected with anything added here.
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


# --- Terminating TLS (Step 13) ----------------------------------------------
#
# The property this whole section exists to keep is one sentence: **a listener
# that speaks plaintext must never advertise HSTS.** It is a promise a browser
# enforces — once seen, it refuses to reach the host over HTTP until the max-age
# expires — so a host that sends it and then cannot serve TLS is unreachable
# rather than degraded.
#
# The layout is what makes that true. `Strict-Transport-Security` appears in
# exactly one file, and that file is the one carrying `ssl_certificate`. Neither
# the plaintext entry point nor the shared body can reach it, so the mistake is
# not one somebody has to remember not to make.


def test_the_plaintext_deployment_makes_no_transport_security_claim(directives: str) -> None:
    """The original invariant, now asserted against the *effective* config.

    Before Step 13 this read one file. It now reads the entry point with the
    shared body spliced in, which is the thing that actually runs — so moving
    HSTS into the shared body, where the plaintext listener would inherit it,
    fails here.
    """
    assert "Strict-Transport-Security" not in directives
    assert "ssl_certificate" not in directives
    assert "listen 443" not in directives


def test_the_shared_body_cannot_carry_hsts() -> None:
    """Said directly of the shared file, not only of what includes it.

    The test above would also pass if HSTS were absent for some accidental
    reason. This one states the structural rule: the body both listeners share
    is not allowed to know about transport security at all.
    """
    shared = without_comments(SHARED.read_text(encoding="utf-8"))
    assert "Strict-Transport-Security" not in shared
    assert "ssl_" not in shared


def test_the_tls_deployment_does_claim_transport_security(tls_directives: str) -> None:
    assert "Strict-Transport-Security" in tls_directives
    assert "ssl_certificate" in tls_directives


def test_hsts_is_configurable_rather_than_baked_in(tls_directives: str) -> None:
    """The safe way to adopt HSTS is a short max-age raised after checking.

    A literal in the template would make the cautious version of that a code
    change, so the value is substituted and compose supplies a default.
    """
    assert re.search(r'Strict-Transport-Security\s+"\$\{VL_HSTS\}"\s+always', tls_directives)


def test_the_default_hsts_is_a_year_and_does_not_preload(tls_compose: dict[str, Any]) -> None:
    """`preload` is a one-way door and commits subdomains this deployment may not own.

    Submitting to the browser preload list is a decision an operator takes
    knowingly. It must not be something they inherit from a default.
    """
    default = environment(tls_compose["services"][PUBLIC_SERVICE])["VL_HSTS"]
    assert "max-age=31536000" in default
    assert "includeSubDomains" in default
    assert "preload" not in default


def test_both_listeners_share_one_body() -> None:
    """One definition of routing, the body cap, forwarding and the headers.

    Two server blocks with two copies is two places for a rule to drift, and the
    one that drifts is always the one nobody re-reads.
    """
    for entry in (TEMPLATE, TLS_TEMPLATE):
        assert "include ${VL_INCLUDE};" in entry.read_text(encoding="utf-8"), entry.name

    # And the shared body really is where the substance lives.
    shared = SHARED.read_text(encoding="utf-8")
    for directive in ("client_max_body_size", "proxy_set_header X-Forwarded-For", "location /api/"):
        assert directive in shared, directive


def test_the_shared_body_is_not_auto_included_as_a_conf(services: dict[str, Any]) -> None:
    """Its substituted name must not match the image's ``include conf.d/*.conf``.

    A partial full of ``location`` blocks pulled into the ``http`` context does
    not parse, and the failure is at container start with a message about an
    unexpected directive rather than about the mount.
    """
    mounts = services[PUBLIC_SERVICE]["volumes"]
    shared_mount = next(m for m in mounts if "_vocallens.inc.template" in m)
    target = shared_mount.split(":")[1]
    assert target.endswith("_vocallens.inc.template")
    # Substituted, `.template` is stripped, leaving `.inc` — not `.conf`.
    assert not target.removesuffix(".template").endswith(".conf")


def test_tls_serves_the_acme_challenge_without_the_application(tls_directives: str) -> None:
    """Renewal must not depend on the app being up.

    The moment a certificate most needs renewing is the moment something is
    wrong, so the challenge is served from a webroot rather than proxied.
    """
    match = re.search(
        r"location\s+\^~\s+/\.well-known/acme-challenge/\s*\{(.*?)\n\s*\}",
        tls_directives,
        re.S,
    )
    assert match, "no ACME challenge location"
    body = match.group(1)
    assert "root" in body
    assert "proxy_pass" not in body


def test_the_redirect_preserves_the_method(tls_directives: str) -> None:
    """308, not 301.

    A 301 permits a client to turn a POST into a GET. Nothing should reach port
    80 in normal use — HSTS keeps a returning browser off it — which is exactly
    why what does reach it must not be silently corrupted.
    """
    assert re.search(r"return\s+308\s+https://\$host\$request_uri", tls_directives)
    assert not re.search(r"return\s+30[12]\s", tls_directives)


def test_tls_negotiates_only_current_protocols(tls_directives: str) -> None:
    match = re.search(r"ssl_protocols([^;]*);", tls_directives)
    assert match, "no ssl_protocols"
    protocols = match.group(1).split()
    assert protocols == ["TLSv1.2", "TLSv1.3"]


def test_session_tickets_are_off(tls_directives: str) -> None:
    """A ticket key living for the life of the process loses forward secrecy."""
    assert re.search(r"ssl_session_tickets\s+off\s*;", tls_directives)


def test_the_tls_override_requires_an_explicit_public_origin(tls_compose: dict[str, Any]) -> None:
    """The mistake this file most wants to prevent.

    ``NEXT_PUBLIC_API_URL`` is inlined into the browser bundle at build time. If
    it stayed ``http://localhost`` while the page was served over HTTPS, every
    API call would fail and the application would load and do nothing —
    measured in Chromium during Step 13: ten console errors and an empty screen.
    Requiring the variable turns that into a failure at ``up`` with a message.
    """
    build = tls_compose["services"]["frontend"]["build"]
    api_url = str(build["args"]["NEXT_PUBLIC_API_URL"])
    assert api_url.startswith("${PUBLIC_ORIGIN:?"), api_url


def test_the_tls_override_requires_a_certificate_directory(tls_compose: dict[str, Any]) -> None:
    mounts = [str(m) for m in tls_compose["services"][PUBLIC_SERVICE]["volumes"]]
    assert any(m.startswith("${TLS_CERT_DIR:?") for m in mounts), mounts


def test_the_tls_override_mounts_the_tls_templates(tls_compose: dict[str, Any]) -> None:
    """Compose merges volumes by container path, so this replaces the plain one."""
    mounts = [str(m) for m in tls_compose["services"][PUBLIC_SERVICE]["volumes"]]
    assert any("templates-tls:/etc/nginx/templates" in m for m in mounts), mounts


def test_the_tls_override_publishes_only_the_two_web_ports(tls_compose: dict[str, Any]) -> None:
    """Terminating TLS must not become an excuse to expose anything else."""
    assert set(tls_compose["services"]) == {"frontend", PUBLIC_SERVICE}
    ports = tls_compose["services"][PUBLIC_SERVICE]["ports"]
    published = [str(p).rsplit(":", 1)[-1] for p in ports]
    assert sorted(published) == ["443", "80"]
