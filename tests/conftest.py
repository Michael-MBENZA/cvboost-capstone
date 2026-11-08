import pytest

from app_web import create_app
from app_web.config import Config


@pytest.fixture
def app(tmp_path):
    """Application de test : base et dossier d'upload temporaires (jetés après le test)."""

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "test.db")
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    return create_app(TestConfig)


@pytest.fixture
def client(app):
    """Navigateur simulé pour envoyer des requêtes à l'application."""
    return app.test_client()


@pytest.fixture
def connecte(client):
    """Client déjà inscrit et connecté."""
    client.post("/register", data={
        "username": "michael", "email": "michael@test.cd",
        "password": "motdepasse123", "confirm": "motdepasse123",
    })
    client.post("/login", data={"email": "michael@test.cd", "password": "motdepasse123"})
    return client
