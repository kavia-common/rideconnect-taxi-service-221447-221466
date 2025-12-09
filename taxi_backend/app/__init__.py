import os
import json
import secrets
from typing import Any, Dict, Iterable, Optional, Tuple

from flask import Flask
from flask_cors import CORS
from flask_smorest import Api
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
import psycopg2
import psycopg2.extras

# Import blueprints and realtime namespace registration
from .routes.health import blp as health_blp
from .routes.auth import blp as auth_blp
from .routes.rides import blp as rides_blp
from .routes.drivers import blp as drivers_blp

# PUBLIC_INTERFACE
def create_app() -> Tuple[Flask, Api, SocketIO]:
    """Create and configure the Flask app, API, JWT, CORS, Socket.IO, and DB helper.

    Returns:
        (app, api, socketio): Configured Flask app, Smorest Api, and SocketIO instance.
    """
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # Basic OpenAPI/Docs config
    app.config["API_TITLE"] = "RideConnect Taxi Service API"
    app.config["API_VERSION"] = "v1"
    app.config["OPENAPI_VERSION"] = "3.0.3"
    app.config["OPENAPI_URL_PREFIX"] = "/docs"
    app.config["OPENAPI_SWAGGER_UI_PATH"] = ""
    app.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

    # CORS
    cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    CORS(app, resources={r"/*": {"origins": cors_origins.split(",")}})

    # JWT
    jwt_secret = os.getenv("JWT_SECRET") or secrets.token_urlsafe(32)
    app.config["JWT_SECRET_KEY"] = jwt_secret
    jwt = JWTManager(app)

    # Socket.IO with eventlet async mode
    socketio = SocketIO(
        app,
        cors_allowed_origins=os.getenv("SOCKETIO_CORS_ORIGINS", "*").split(","),
        async_mode="eventlet",
        logger=False,
        engineio_logger=False,
    )

    # Attach helpers to app context
    app.extensions["db"] = _DatabaseHelper()
    app.extensions["jwt"] = jwt
    app.extensions["socketio"] = socketio

    # API / Blueprints
    api = Api(app)
    api.register_blueprint(health_blp)
    api.register_blueprint(auth_blp)
    api.register_blueprint(rides_blp)
    api.register_blueprint(drivers_blp)

    # Register Socket.IO namespaces/handlers
    from .realtime.socket import register_namespaces
    register_namespaces(socketio)

    return app, api, socketio


class _DatabaseHelper:
    """Small psycopg2 helper that discovers DATABASE_URL or falls back to db_connection.txt parsing."""

    def __init__(self) -> None:
        self._dsn = self._resolve_database_url()

    def _resolve_database_url(self) -> Optional[str]:
        # First prefer explicit env
        url = os.getenv("DATABASE_URL")
        if url:
            return url

        # Fallback: read database container's db_connection.txt
        # Expected path: rideconnect-taxi-service-221447-221468/database/db_connection.txt
        fallback_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "rideconnect-taxi-service-221447-221468",
            "database",
            "db_connection.txt",
        )
        # Another relative attempt from known workspace base:
        alt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "rideconnect-taxi-service-221447-221468",
            "database",
            "db_connection.txt",
        )

        for path in (fallback_path, alt_path):
            try:
                with open(path, "r") as f:
                    content = f.read().strip()
                    # Typical content: psql postgresql://user:pass@host:port/db
                    # extract the URL part
                    if "postgresql://" in content:
                        idx = content.find("postgresql://")
                        return content[idx:].strip()
            except FileNotFoundError:
                continue
            except Exception:
                continue

        return None

    # PUBLIC_INTERFACE
    def get_conn(self):
        """Get a new psycopg2 connection using DATABASE_URL or raise a helpful error."""
        if not self._dsn:
            raise RuntimeError(
                "DATABASE_URL is not configured and db_connection.txt could not be located. "
                "Please set DATABASE_URL in environment or ensure the database container exposes db_connection.txt."
            )
        # Use DictCursor for convenience in helpers
        return psycopg2.connect(self._dsn, cursor_factory=psycopg2.extras.DictCursor)

    # PUBLIC_INTERFACE
    def fetchall(self, query: str, params: Optional[Iterable[Any]] = None) -> Iterable[Dict[str, Any]]:
        """Execute a SELECT query and return list of dict rows."""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or [])
                rows = cur.fetchall()
                return [dict(r) for r in rows]

    # PUBLIC_INTERFACE
    def fetchone(self, query: str, params: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]:
        """Execute a SELECT ... LIMIT 1 query and return a dict row or None."""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or [])
                r = cur.fetchone()
                return dict(r) if r else None

    # PUBLIC_INTERFACE
    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> int:
        """Execute a write query (INSERT/UPDATE/DELETE). Returns affected row count."""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or [])
                affected = cur.rowcount
                conn.commit()
                return affected


# Create singleton app objects for imports like `from app import app, api, socketio`
app, api, socketio = create_app()
