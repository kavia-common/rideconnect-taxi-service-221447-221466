from typing import Any, Dict

from flask import request
from flask_smorest import Blueprint, abort
from flask.views import MethodView
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import Schema, fields

from ..models import query_one, execute
from ..services import payments as payment_service

# Payments Blueprint
blp = Blueprint(
    "Payments",
    "payments",
    url_prefix="/payments",
    description="Payment intents and confirmations for rides",
)

# ---------- Schemas ----------

class CreatePaymentIntentRequestSchema(Schema):
    ride_id = fields.Integer(required=True, description="Ride ID to pay for")

class PaymentIntentResponseSchema(Schema):
    id = fields.String(required=True, description="Payment intent ID from provider")
    amount = fields.Integer(required=True, description="Amount in cents")
    currency = fields.String(required=True)
    status = fields.String(required=True)
    client_secret = fields.String(required=True)
    metadata = fields.Dict(required=True)
    provider = fields.String(required=True)

class ConfirmPaymentRequestSchema(Schema):
    payment_intent_id = fields.String(required=True, description="Payment intent ID")

class ConfirmPaymentResponseSchema(Schema):
    id = fields.String(required=True)
    status = fields.String(required=True)
    provider = fields.String(required=True)

class MessageSchema(Schema):
    message = fields.String(required=True)

# ---------- Helpers / Bootstrap ----------

def _ensure_tables():
    # users
    execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('rider','driver')),
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    # rides
    execute(
        """
        CREATE TABLE IF NOT EXISTS rides (
            id SERIAL PRIMARY KEY,
            rider_id INTEGER NOT NULL REFERENCES users(id),
            driver_id INTEGER REFERENCES users(id),
            status TEXT NOT NULL CHECK (status IN ('requested','assigned','ongoing','completed','canceled')),
            pickup_lat DOUBLE PRECISION NOT NULL,
            pickup_lng DOUBLE PRECISION NOT NULL,
            dropoff_lat DOUBLE PRECISION NOT NULL,
            dropoff_lng DOUBLE PRECISION NOT NULL,
            distance_km DOUBLE PRECISION,
            duration_min DOUBLE PRECISION,
            estimated_fare DOUBLE PRECISION,
            final_fare DOUBLE PRECISION,
            requested_at TIMESTAMP DEFAULT NOW(),
            assigned_at TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            canceled_at TIMESTAMP,
            cancel_reason TEXT
        )
        """
    )
    # payments
    execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            ride_id INTEGER NOT NULL REFERENCES rides(id),
            rider_id INTEGER NOT NULL REFERENCES users(id),
            provider TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'usd',
            status TEXT NOT NULL,
            client_secret TEXT,
            metadata JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP DEFAULT NOW(),
            confirmed_at TIMESTAMP
        )
        """
    )

def _require_rider_identity() -> Dict[str, Any]:
    ident = get_jwt_identity() or {}
    if ident.get("role") != "rider":
        abort(403, message="Only riders may access this endpoint")
    return ident

def _ride_visible_to_rider(ride: Dict[str, Any], rider_id: int) -> bool:
    return (ride or {}).get("rider_id") == rider_id

def _ride_is_payable(ride: Dict[str, Any]) -> bool:
    # We consider ride payable when completed and not paid yet
    if not ride:
        return False
    if ride.get("status") != "completed":
        return False
    # If payments table has a succeeded for this ride -> already paid
    existing = query_one(
        "SELECT id FROM payments WHERE ride_id = %s AND status = 'succeeded' LIMIT 1",
        [ride["id"]],
    )
    return existing is None

# ---------- Routes ----------

@blp.route("/create-intent")
class CreatePaymentIntentView(MethodView):
    """Create a payment intent for a completed ride."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Create a payment intent for a ride with final_fare.

        Summary:
            Create payment intent
        Request Body:
            application/json: CreatePaymentIntentRequestSchema
        Returns:
            201: PaymentIntentResponseSchema
        """
        _ensure_tables()
        ident = _require_rider_identity()
        data = CreatePaymentIntentRequestSchema().load(request.get_json() or {})
        ride_id = int(data["ride_id"])
        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if not _ride_visible_to_rider(ride, ident["id"]):
            abort(403, message="Cannot pay for another rider's ride")
        if not _ride_is_payable(ride):
            abort(409, message="Ride is not eligible for payment (not completed or already paid)")

        # Determine amount from final_fare (fallback to estimated if missing)
        final_fare = ride.get("final_fare")
        if final_fare is None:
            final_fare = ride.get("estimated_fare")
        if final_fare is None:
            abort(409, message="Ride has no fare available")

        amount_cents = int(round(float(final_fare) * 100))
        currency = "usd"

        intent = payment_service.create_payment_intent(
            amount_cents=amount_cents,
            currency=currency,
            metadata={"ride_id": str(ride_id), "rider_id": str(ident["id"])},
        )

        # Store payment record
        saved = query_one(
            """
            INSERT INTO payments (ride_id, rider_id, provider, intent_id, amount_cents, currency, status, client_secret, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            [
                ride_id,
                ident["id"],
                intent.get("provider"),
                intent.get("id"),
                amount_cents,
                currency,
                intent.get("status"),
                intent.get("client_secret"),
                # store as json string
                # fallback to empty dict if metadata missing
                (intent.get("metadata") or {}),
            ],
        )
        if not saved:
            abort(500, message="Failed to persist payment intent")

        return intent, 201


@blp.route("/confirm")
class ConfirmPaymentView(MethodView):
    """Confirm a previously created payment intent."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Confirm a payment intent and mark ride as paid when succeeded.

        Summary:
            Confirm payment
        Request Body:
            application/json: ConfirmPaymentRequestSchema
        Returns:
            200: ConfirmPaymentResponseSchema
        """
        _ensure_tables()
        ident = _require_rider_identity()
        payload = ConfirmPaymentRequestSchema().load(request.get_json() or {})
        intent_id = payload["payment_intent_id"]

        payment = query_one(
            "SELECT * FROM payments WHERE intent_id = %s AND rider_id = %s",
            [intent_id, ident["id"]],
        )
        if not payment:
            abort(404, message="Payment intent not found")

        # If already succeeded, idempotent 200
        if payment.get("status") == "succeeded":
            return {
                "id": intent_id,
                "status": "succeeded",
                "provider": payment.get("provider", "fake"),
            }, 200

        confirmed = payment_service.confirm_payment(intent_id)
        new_status = confirmed.get("status", "succeeded")

        # Update payment record and set confirmed_at
        updated = execute(
            """
            UPDATE payments
            SET status = %s, confirmed_at = NOW()
            WHERE id = %s
            """,
            [new_status, payment["id"]],
        )
        if updated <= 0:
            abort(500, message="Failed to update payment status")

        # When payment succeeded, mark ride as paid; since schema didn't originally include paid column,
        # we add a ride_payments table or set final_fare as-is and rely on payments table for paid state.
        # To keep it simple, we add a lightweight marker column if not exists (safe to run multiple times).
        execute(
            "ALTER TABLE rides ADD COLUMN IF NOT EXISTS paid BOOLEAN DEFAULT FALSE"
        )
        if new_status == "succeeded":
            # Only mark as paid if ride belongs to this rider
            ride = query_one("SELECT * FROM rides WHERE id = %s", [payment["ride_id"]])
            if ride and ride.get("rider_id") == ident["id"]:
                execute(
                    "UPDATE rides SET paid = TRUE WHERE id = %s",
                    [ride["id"]],
                )

        return confirmed, 200
