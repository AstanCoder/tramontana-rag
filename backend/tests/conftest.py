import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # Unhandled errors must come back as 500 responses, not re-raise into the test.
    return TestClient(app, raise_server_exceptions=False)
