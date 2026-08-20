"""The policy on responses that are data, not documents.

Step 10.5 put three security headers at the edge and Step 10.9 gave the web app
a nonce-based Content-Security-Policy. ``limitations.md`` recorded what neither
covered, and it stayed recorded through ten more steps: responses from ``/api/``
carried no policy of their own. What stops a browser treating one as a document
was ``X-Content-Type-Options: nosniff`` alone — a narrower guarantee than a
policy, and one with no second line behind it if it is ever bypassed.

What is asserted here is that the policy reaches **every** response rather than
the ones a route happened to produce, that it says the four things it exists to
say, and that the two interactive documentation pages — which are HTML that
loads a CDN, and which the shipped deployment does not route at all — are the
only things exempt from it.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.middleware import API_SECURITY_POLICY, ApiSecurityPolicyMiddleware
from app.main import create_app

HEADER = "content-security-policy"


@pytest.fixture
def app() -> Any:
    return create_app()


@pytest.fixture
def bare(app: Any) -> TestClient:
    """A client with no repository overrides: this is about headers, not data."""
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/api/v1/health",
        "/api/v1/config",
        "/openapi.json",
        # A route that does not exist, and one that exists but refuses the
        # request: an error response is exactly the kind that should not be the
        # one without a policy.
        "/nope",
        "/api/v1/recordings/not-a-valid-id/audio-analysis",
    ],
)
def test_every_response_carries_the_policy(bare: TestClient, path: str) -> None:
    assert bare.get(path).headers[HEADER] == API_SECURITY_POLICY


def test_the_policy_forbids_the_four_things_a_json_response_never_does() -> None:
    """Asserted as properties rather than as a string, so the reason survives."""
    directives = {
        part.strip().split(" ")[0]: part.strip() for part in API_SECURITY_POLICY.split(";")
    }
    assert directives["default-src"] == "default-src 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'none'"
    assert directives["base-uri"] == "base-uri 'none'"
    assert directives["form-action"] == "form-action 'none'"
    # The one that still holds if a browser is somehow persuaded to render the
    # response: a unique origin, no scripts, no forms, no plugins.
    assert "sandbox" in directives


def test_the_policy_is_sent_once(bare: TestClient) -> None:
    """Two policies are enforced as their intersection, which nobody designed."""
    response = bare.get("/api/v1/health")

    assert len(response.headers.get_list(HEADER)) == 1


def test_a_response_no_route_produced_carries_it(app: Any) -> None:
    """The oversized-upload refusal is written by middleware, not by a route.

    It is produced before routing, by the guard that stops an unbounded body
    reaching the multipart parser, and it is still a response a browser holds.
    """
    client = TestClient(app)
    settings_cap = 50 * 1024 * 1024

    response = client.post(
        "/api/v1/recordings",
        content=b"x" * 1024,
        headers={"content-length": str(settings_cap + 1), "content-type": "audio/wav"},
    )

    assert response.status_code == 413
    assert response.headers[HEADER] == API_SECURITY_POLICY


@pytest.mark.parametrize("page", ["/docs", "/redoc"])
def test_the_documentation_uis_are_the_only_exemption(bare: TestClient, page: str) -> None:
    """They are HTML that loads a CDN; `default-src 'none'` renders them blank.

    They are also unreachable through the shipped deployment, which routes only
    `/api/` to this application — so this exempts a development convenience.
    """
    response = bare.get(page)

    assert response.status_code == 200
    assert HEADER not in response.headers


def test_the_exemptions_are_read_from_the_application(app: Any) -> None:
    """A path written into the middleware would go stale the moment one moved.

    Read from the registered middleware rather than inferred from a response,
    because the failure this guards against is a *silent* one: a hardcoded
    "/docs" keeps working right up until the day the documentation moves, and
    then exempts nothing and blanks a page.
    """
    registered = [
        middleware
        for middleware in app.user_middleware
        if middleware.cls is ApiSecurityPolicyMiddleware
    ]
    assert len(registered) == 1

    exempt = registered[0].kwargs["exempt_paths"]
    assert exempt == {app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url}
    assert app.docs_url in exempt and app.redoc_url in exempt
