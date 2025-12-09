from datetime import timedelta
from typing import Optional

from flask import request
from flask_smorest import Blueprint, abort
from flask.views import MethodView
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
)
from marshmallow import Schema, fields, validate
from passlib.context import CryptContext

from ..models import query_one, execute

# Password hashing context using bcrypt
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Blueprint for Auth routes
blp = Blueprint(
    "Auth",
    "auth",
    url_prefix="/auth",
    description="Authentication routes for registration, login, JWT refresh and logout",
)

# Marshmallow Schemas
class RegisterSchema(Schema):
    email = fields.Email(required=True, description="Unique email for the user")
    password = fields.String(
        required=True,
        load_only=True,
        validate=validate.Length(min=8),
        description="Password (min 8 chars)",
    )
    name = fields.String(required=True, description="Full name")
    role = fields.String(
        required=True,
        validate=validate.OneOf(["rider", "driver"]),
        description="User role: rider or driver",
    )


class LoginSchema(Schema):
    email = fields.Email(required=True, description="User email")
    password = fields.String(required=True, load_only=True, description="User password")


class TokenResponseSchema(Schema):
    access_token = fields.String(required=True, description="JWT access token")
    refresh_token = fields.String(required=False, description="JWT refresh token")


class MessageSchema(Schema):
    message = fields.String(required=True, description="Message string")


def _find_user_by_email(email: str) -> Optional[dict]:
    return query_one("SELECT id, email, name, role, password_hash FROM users WHERE email = %s", [email])


def _insert_user(email: str, name: str, role: str, password_hash: str) -> Optional[dict]:
    # Insert user and return created user row
    sql = """
        INSERT INTO users (email, name, role, password_hash)
        VALUES (%s, %s, %s, %s)
        RETURNING id, email, name, role
    """
    # psycopg2 helper execute returns affected; we need row: use query_one with RETURNING
    return query_one(sql, [email, name, role, password_hash])


def _ensure_users_table():
    # Create users table if not exists. Note: simple bootstrap; for production use migrations.
    sql = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('rider','driver')),
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """
    execute(sql)


def _hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


def _jwt_additional_claims(user: dict) -> dict:
    return {"role": user.get("role"), "email": user.get("email")}


@blp.route("/register")
class RegisterView(MethodView):
    """Register a new user (rider or driver)."""

    # PUBLIC_INTERFACE
    def post(self):
        """Register a new user with email, name, role, and password.

        Summary:
            Register a new user.
        Request Body:
            application/json: RegisterSchema
        Returns:
            201: TokenResponseSchema with access and refresh tokens
        """
        _ensure_users_table()
        data = RegisterSchema().load(request.get_json() or {})

        # Ensure unique email
        if _find_user_by_email(data["email"]):
            abort(409, message="Email already registered")

        password_hash = _hash_password(data["password"])
        user = _insert_user(data["email"], data["name"], data["role"], password_hash)
        if not user:
            abort(500, message="Failed to create user")

        identity = {"id": user["id"], "role": user["role"], "email": user["email"]}
        access = create_access_token(identity=identity, additional_claims=_jwt_additional_claims(user), expires_delta=timedelta(minutes=30))
        refresh = create_refresh_token(identity=identity, additional_claims=_jwt_additional_claims(user), expires_delta=timedelta(days=7))
        return {"access_token": access, "refresh_token": refresh}, 201


@blp.route("/login")
class LoginView(MethodView):
    """Login an existing user and receive tokens."""

    # PUBLIC_INTERFACE
    def post(self):
        """Authenticate user by email and password.

        Summary:
            Login user and get JWTs.
        Request Body:
            application/json: LoginSchema
        Returns:
            200: TokenResponseSchema with access and refresh tokens
        """
        _ensure_users_table()
        data = LoginSchema().load(request.get_json() or {})
        user = _find_user_by_email(data["email"])
        if not user or not _verify_password(data["password"], user["password_hash"]):
            abort(401, message="Invalid email or password")

        identity = {"id": user["id"], "role": user["role"], "email": user["email"]}
        access = create_access_token(identity=identity, additional_claims=_jwt_additional_claims(user), expires_delta=timedelta(minutes=30))
        refresh = create_refresh_token(identity=identity, additional_claims=_jwt_additional_claims(user), expires_delta=timedelta(days=7))
        return {"access_token": access, "refresh_token": refresh}, 200


@blp.route("/refresh")
class RefreshView(MethodView):
    """Refresh access token using a valid refresh token."""

    # PUBLIC_INTERFACE
    @jwt_required(refresh=True)
    def post(self):
        """Issue a new access token using a refresh token.

        Summary:
            Refresh access token.
        Responses:
            200: TokenResponseSchema (access_token only)
        """
        identity = get_jwt_identity()
        claims = get_jwt()
        # Keep role/email in additional claims
        add_claims = {"role": claims.get("role"), "email": claims.get("email")}
        access = create_access_token(identity=identity, additional_claims=add_claims, expires_delta=timedelta(minutes=30))
        return {"access_token": access}, 200


@blp.route("/logout")
class LogoutView(MethodView):
    """Stateless logout endpoint (client-side token discard)."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Logout endpoint. Since JWTs are stateless, client should discard tokens.

        Summary:
            Logout current user.
        Responses:
            200: MessageSchema
        """
        return {"message": "Logged out"}, 200
