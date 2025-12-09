from math import radians, sin, cos, asin, sqrt
from typing import Optional, Tuple, Dict, Any

from flask import request
from flask_smorest import Blueprint, abort
from flask.views import MethodView
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import Schema, fields, validate, ValidationError

from ..models import query_one, query_all, execute
from ..services import fare as fare_service
from .. import app
from ..realtime.socket import emit_ride_status_update

# Rides Blueprint
blp = Blueprint(
    "Rides",
    "rides",
    url_prefix="/rides",
    description="Ride booking, quoting, assignment, status, cancel and completion",
)

# -------------- Utility functions --------------

def _ensure_tables():
    """Create required tables if not exist."""
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
    execute(
        """
        CREATE TABLE IF NOT EXISTS driver_locations (
            driver_id INTEGER PRIMARY KEY REFERENCES users(id),
            lat DOUBLE PRECISION NOT NULL,
            lng DOUBLE PRECISION NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance using Haversine formula in kilometers."""
    # convert decimal degrees to radians
    rlat1, rlon1, rlat2, rlon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = rlon2 - rlon1
    dlat = rlat2 - rlat1
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    km = 6371.0 * c
    return round(km, 3)

def _estimate_duration_min(distance_km: float) -> float:
    """Very rough duration estimate: assume 30 km/h average speed."""
    if distance_km <= 0:
        return 5.0
    minutes = (distance_km / 30.0) * 60.0
    return round(minutes, 1)

def _get_nearest_driver(pickup: Tuple[float, float]) -> Optional[int]:
    """Find nearest driver by last reported location."""
    lat, lng = pickup
    drivers = query_all(
        """
        SELECT dl.driver_id, dl.lat, dl.lng
        FROM driver_locations dl
        JOIN users u ON u.id = dl.driver_id
        WHERE u.role = 'driver'
        """
    )
    best = None
    best_dist = 10 ** 9
    for d in drivers:
        dist = _haversine_km(lat, lng, float(d["lat"]), float(d["lng"]))
        if dist < best_dist:
            best_dist = dist
            best = d["driver_id"]
    return best

def _get_next_round_robin_driver() -> Optional[int]:
    """Simple round-robin: pick any driver with the oldest updated_at."""
    row = query_one(
        """
        SELECT dl.driver_id
        FROM driver_locations dl
        JOIN users u ON u.id = dl.driver_id
        WHERE u.role = 'driver'
        ORDER BY dl.updated_at ASC
        LIMIT 1
        """
    )
    return row["driver_id"] if row else None

def _assign_driver(ride_id: int, strategy: str, pickup: Tuple[float, float]) -> Optional[int]:
    """Assign a driver to the ride using provided strategy."""
    driver_id: Optional[int] = None
    if strategy == "nearest":
        driver_id = _get_nearest_driver(pickup)
    elif strategy == "round_robin":
        driver_id = _get_next_round_robin_driver()
    else:
        driver_id = _get_nearest_driver(pickup)

    if not driver_id:
        return None

    # Update ride assignment only if still requested
    updated = execute(
        """
        UPDATE rides
        SET driver_id = %s, status = 'assigned', assigned_at = NOW()
        WHERE id = %s AND status = 'requested'
        """,
        [driver_id, ride_id],
    )
    if updated <= 0:
        return None
    return driver_id

def _check_user_role(required: str):
    ident = get_jwt_identity() or {}
    role = ident.get("role")
    if role != required:
        abort(403, message=f"Only {required}s can perform this action")

def _ride_visible_to_user(ride: Dict[str, Any], user: Dict[str, Any]) -> bool:
    if user.get("role") == "rider":
        return ride.get("rider_id") == user.get("id")
    if user.get("role") == "driver":
        return ride.get("driver_id") == user.get("id")
    return False

# -------------- Schemas --------------

class LocationSchema(Schema):
    lat = fields.Float(required=True, description="Latitude")
    lng = fields.Float(required=True, description="Longitude")

class QuoteRequestSchema(Schema):
    pickup = fields.Nested(LocationSchema, required=True, description="Pickup coordinates")
    dropoff = fields.Nested(LocationSchema, required=True, description="Dropoff coordinates")

class QuoteResponseSchema(Schema):
    distance_km = fields.Float(required=True)
    duration_min = fields.Float(required=True)
    estimated_fare = fields.Float(required=True)
    fare_rules = fields.Dict(required=True)

class CreateRideRequestSchema(QuoteRequestSchema):
    pass

class CreateRideResponseSchema(Schema):
    ride_id = fields.Integer(required=True)
    status = fields.String(required=True)
    driver_id = fields.Integer(allow_none=True)
    estimated_fare = fields.Float(required=True)

class AssignRequestSchema(Schema):
    strategy = fields.String(required=False, validate=validate.OneOf(["nearest", "round_robin"]), missing="nearest")

class RideStatusResponseSchema(Schema):
    id = fields.Integer(required=True)
    status = fields.String(required=True)
    rider_id = fields.Integer(required=True)
    driver_id = fields.Integer(allow_none=True)
    pickup = fields.Nested(LocationSchema, required=True)
    dropoff = fields.Nested(LocationSchema, required=True)
    distance_km = fields.Float(allow_none=True)
    duration_min = fields.Float(allow_none=True)
    estimated_fare = fields.Float(allow_none=True)
    final_fare = fields.Float(allow_none=True)
    requested_at = fields.DateTime(allow_none=True)
    assigned_at = fields.DateTime(allow_none=True)
    started_at = fields.DateTime(allow_none=True)
    completed_at = fields.DateTime(allow_none=True)
    canceled_at = fields.DateTime(allow_none=True)
    cancel_reason = fields.String(allow_none=True)

class CancelRequestSchema(Schema):
    reason = fields.String(required=False)

# -------------- Routes --------------

@blp.route("/quote")
class FareQuoteView(MethodView):
    """Return fare estimate between pickup and dropoff using Haversine + fare rules."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Get fare quote for a potential ride.
        Summary:
            Fare quote
        Request Body:
            application/json: QuoteRequestSchema
        Returns:
            200: QuoteResponseSchema
        """
        _ensure_tables()
        payload = QuoteRequestSchema().load(request.get_json() or {})
        p = payload["pickup"]
        d = payload["dropoff"]

        distance_km = _haversine_km(p["lat"], p["lng"], d["lat"], d["lng"])
        duration_min = _estimate_duration_min(distance_km)
        estimated = fare_service.estimate_cost(distance_km, duration_min)
        return {
            "distance_km": distance_km,
            "duration_min": duration_min,
            "estimated_fare": estimated,
            "fare_rules": {
                "base": fare_service.base_fare(),
                "per_km": fare_service.per_km(),
                "per_min": fare_service.per_min(),
            },
        }, 200


@blp.route("")
class RideCreateView(MethodView):
    """Create a ride request (rider only)."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Create a new ride request.
        Summary:
            Create ride
        Request Body:
            application/json: CreateRideRequestSchema
        Returns:
            201: CreateRideResponseSchema
        """
        _ensure_tables()
        _check_user_role("rider")
        ident = get_jwt_identity()
        rider_id = ident.get("id")

        payload = CreateRideRequestSchema().load(request.get_json() or {})
        p = payload["pickup"]
        d = payload["dropoff"]
        distance_km = _haversine_km(p["lat"], p["lng"], d["lat"], d["lng"])
        duration_min = _estimate_duration_min(distance_km)
        estimated = fare_service.estimate_cost(distance_km, duration_min)

        row = query_one(
            """
            INSERT INTO rides (
                rider_id, status,
                pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                distance_km, duration_min, estimated_fare
            ) VALUES (%s,'requested', %s,%s,%s,%s, %s,%s,%s)
            RETURNING id, status, driver_id, estimated_fare
            """,
            [
                rider_id,
                p["lat"],
                p["lng"],
                d["lat"],
                d["lng"],
                distance_km,
                duration_min,
                estimated,
            ],
        )
        if not row:
            abort(500, message="Failed to create ride")

        # Emit ride requested
        try:
            socketio = app.extensions["socketio"]
            emit_ride_status_update(socketio, row["id"], "requested", driver_id=row["driver_id"])
        except Exception:
            pass

        return {
            "ride_id": row["id"],
            "status": row["status"],
            "driver_id": row["driver_id"],
            "estimated_fare": row["estimated_fare"],
        }, 201


@blp.route("/<int:ride_id>/assign")
class AssignDriverView(MethodView):
    """Assign driver to a ride request. Accessible by rider to trigger matching or by system/admin in future."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Assign a driver using 'nearest' or 'round_robin' strategy.
        Summary:
            Assign driver
        Parameters:
            ride_id: path integer
        Request Body:
            application/json: AssignRequestSchema
        Returns:
            200: Message with driver_id or 409/404
        """
        _ensure_tables()
        ident = get_jwt_identity()
        # Riders can assign on their rides; Drivers not allowed; future admin roles could be allowed
        if ident.get("role") != "rider":
            abort(403, message="Only riders can request assignment")

        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if ride["rider_id"] != ident.get("id"):
            abort(403, message="Cannot assign for another rider's ride")
        if ride["status"] != "requested":
            abort(409, message=f"Ride not in requested state (current={ride['status']})")

        data = AssignRequestSchema().load(request.get_json() or {})
        pickup = (float(ride["pickup_lat"]), float(ride["pickup_lng"]))
        driver_id = _assign_driver(ride_id, data.get("strategy", "nearest"), pickup)
        if not driver_id:
            abort(409, message="No driver available or assignment failed")

        # Emit assignment to ride room
        try:
            socketio = app.extensions["socketio"]
            emit_ride_status_update(socketio, ride_id, "assigned", driver_id=driver_id)
        except Exception:
            pass

        return {"message": "assigned", "driver_id": driver_id}, 200


@blp.route("/<int:ride_id>")
class RideStatusView(MethodView):
    """Get ride status and details (visible to rider or assigned driver)."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def get(self, ride_id: int):
        """Get ride status/details.
        Summary:
            Get ride
        Parameters:
            ride_id: path integer
        Returns:
            200: RideStatusResponseSchema
        """
        _ensure_tables()
        ident = get_jwt_identity()
        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if not _ride_visible_to_user(ride, ident):
            abort(403, message="Not allowed to view this ride")

        resp = {
            "id": ride["id"],
            "status": ride["status"],
            "rider_id": ride["rider_id"],
            "driver_id": ride["driver_id"],
            "pickup": {"lat": ride["pickup_lat"], "lng": ride["pickup_lng"]},
            "dropoff": {"lat": ride["dropoff_lat"], "lng": ride["dropoff_lng"]},
            "distance_km": ride["distance_km"],
            "duration_min": ride["duration_min"],
            "estimated_fare": ride["estimated_fare"],
            "final_fare": ride["final_fare"],
            "requested_at": ride["requested_at"],
            "assigned_at": ride["assigned_at"],
            "started_at": ride["started_at"],
            "completed_at": ride["completed_at"],
            "canceled_at": ride["canceled_at"],
            "cancel_reason": ride["cancel_reason"],
        }
        return resp, 200


@blp.route("/<int:ride_id>/cancel")
class RideCancelView(MethodView):
    """Cancel a ride before it starts. Riders can cancel their ride; driver cancellation can be added later."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Cancel a ride if not started.
        Summary:
            Cancel ride
        Parameters:
            ride_id: path integer
        Request Body:
            application/json: CancelRequestSchema
        Returns:
            200: message
        """
        _ensure_tables()
        ident = get_jwt_identity()
        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")

        # Only rider can cancel in this simple version
        if ident.get("role") != "rider" or ride["rider_id"] != ident.get("id"):
            abort(403, message="Only the rider who created the ride can cancel it")

        if ride["status"] in ("completed", "canceled"):
            abort(409, message=f"Ride already {ride['status']}")
        if ride["status"] in ("ongoing",):
            abort(409, message="Ride already started")

        data = {}
        try:
            data = CancelRequestSchema().load(request.get_json() or {})
        except ValidationError:
            pass
        reason = data.get("reason")

        updated = execute(
            """
            UPDATE rides
            SET status = 'canceled', canceled_at = NOW(), cancel_reason = %s
            WHERE id = %s AND status IN ('requested','assigned')
            """,
            [reason, ride_id],
        )
        if updated <= 0:
            abort(409, message="Unable to cancel ride in current state")

        try:
            socketio = app.extensions["socketio"]
            emit_ride_status_update(socketio, ride_id, "canceled", driver_id=ride.get("driver_id"))
        except Exception:
            pass

        return {"message": "canceled"}, 200


@blp.route("/<int:ride_id>/complete")
class RideCompleteView(MethodView):
    """Complete a ride (driver marks completed)."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Mark the ride as completed, computing final fare if missing.
        Summary:
            Complete ride
        Parameters:
            ride_id: path integer
        Returns:
            200: message with final fare
        """
        _ensure_tables()
        ident = get_jwt_identity()
        if ident.get("role") != "driver":
            abort(403, message="Only drivers can complete rides")

        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if ride.get("driver_id") != ident.get("id"):
            abort(403, message="Only assigned driver can complete this ride")
        if ride["status"] in ("completed", "canceled"):
            abort(409, message=f"Ride already {ride['status']}")

        # In a real app, we would use tracked actual distance/duration.
        # Here we fallback to estimated values.
        distance_km = float(ride["distance_km"] or 0)
        duration_min = float(ride["duration_min"] or _estimate_duration_min(distance_km))
        final_fare = fare_service.estimate_cost(distance_km, duration_min)

        updated = execute(
            """
            UPDATE rides
            SET status = 'completed', completed_at = NOW(), final_fare = %s
            WHERE id = %s AND status IN ('assigned','ongoing','requested')
            """,
            [final_fare, ride_id],
        )
        if updated <= 0:
            abort(409, message="Unable to complete ride in current state")

        try:
            socketio = app.extensions["socketio"]
            emit_ride_status_update(socketio, ride_id, "completed", driver_id=ident.get("id"), extra={"final_fare": final_fare})
        except Exception:
            pass

        return {"message": "completed", "final_fare": final_fare}, 200
