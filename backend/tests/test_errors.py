import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    STATUS_BY_CODE,
    ApiError,
    ErrorCode,
    register_exception_handlers,
)


@pytest.fixture
def error_app() -> FastAPI:
    """A throwaway app whose routes fail in each way the handlers cover."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raises-api-error")
    def _raises_api_error() -> None:
        raise ApiError(ErrorCode.AUDIO_TOO_LONG, "That recording is longer than 5 minutes.")

    @app.get("/raises-custom-status")
    def _raises_custom_status() -> None:
        raise ApiError(ErrorCode.CORRUPTED_AUDIO, "Broken.", status_code=400)

    @app.get("/needs-a-query-param")
    def _needs_a_query_param(required: int) -> int:
        return required

    @app.get("/explodes")
    def _explodes() -> None:
        raise RuntimeError("password=hunter2 leaked from an internal call")

    return app


@pytest.fixture
def error_client(error_app: FastAPI) -> TestClient:
    # The unhandled-exception handler builds the 500 response and then Starlette
    # re-raises, so the exception has to be suppressed to observe the response.
    return TestClient(error_app, raise_server_exceptions=False)


def test_every_error_code_has_a_default_status():
    """A new code must not be added without deciding its HTTP meaning."""
    assert set(STATUS_BY_CODE) == set(ErrorCode)


def test_api_error_uses_the_default_status_for_its_code():
    error = ApiError(ErrorCode.FILE_TOO_LARGE, "Too big.")
    assert error.status_code == 413
    assert error.message == "Too big."
    assert str(error) == "Too big."


def test_api_error_status_can_be_overridden():
    error = ApiError(ErrorCode.UNSUPPORTED_FORMAT, "Nope.", status_code=400)
    assert error.status_code == 400


def test_api_error_renders_the_documented_envelope(error_client: TestClient):
    response = error_client.get("/raises-api-error")
    assert response.status_code == 422
    assert response.json() == {
        "status": "failed",
        "error_code": "AUDIO_TOO_LONG",
        "message": "That recording is longer than 5 minutes.",
    }


def test_custom_status_is_honoured_by_the_handler(error_client: TestClient):
    response = error_client.get("/raises-custom-status")
    assert response.status_code == 400
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"


def test_request_validation_failure_uses_the_envelope(error_client: TestClient):
    response = error_client.get("/needs-a-query-param")
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "VALIDATION_ERROR"
    # FastAPI's per-field detail list must not leak through.
    assert "detail" not in body


def test_unknown_route_uses_the_envelope(error_client: TestClient):
    response = error_client.get("/no-such-route")
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


def test_wrong_method_uses_the_envelope(error_client: TestClient):
    response = error_client.post("/raises-api-error")
    assert response.status_code == 405
    assert response.json()["error_code"] == "METHOD_NOT_ALLOWED"


def test_unhandled_exception_is_structured_and_leaks_nothing(error_client: TestClient):
    response = error_client.get("/explodes")
    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert "RuntimeError" not in body["message"]
    assert "Traceback" not in response.text


def test_handlers_are_registered_on_the_real_app(client: TestClient):
    """The application factory wires the handlers, not just the test fixture."""
    response = client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json() == {
        "status": "failed",
        "error_code": "NOT_FOUND",
        "message": "Not Found",
    }
