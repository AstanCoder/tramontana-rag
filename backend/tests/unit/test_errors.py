from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import AppError


class Payload(BaseModel):
    n: int


def test_app_error_returns_envelope(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise AppError("invalid_question", "La pregunta no es válida", 422)

    response = client.get("/boom")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_question",
            "message": "La pregunta no es válida",
            "trace_id": None,
        }
    }


def test_validation_error_returns_envelope(app: FastAPI, client: TestClient) -> None:
    @app.post("/echo")
    def echo(payload: Payload) -> Payload:
        return payload

    response = client.post("/echo", json={"n": "not-an-int"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["trace_id"] is None


def test_http_exception_returns_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unhandled_error_hides_details(app: FastAPI, client: TestClient) -> None:
    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError("secret db dsn")

    response = client.get("/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
