from typing import Any, Dict

from flask import request
from flask_smorest import Blueprint, abort
from flask.views import MethodView
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import Schema, fields

from ..models import query_one, execute

# Drivers Blueprint
blp = Blueprint(
    "Drivers",
    "drivers",
    url_prefix="/drivers",
    description="Driver availability, location updates, and ride lifecycle actions (accept, start, complete)",
)

# ---------- DB bootstrap ----------

def _ensure_tables():
    # Users table (drivers included)
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
    # Rides table (mirrors definition from rides blueprint)
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
    # Driver locations table
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
    # Driver availability table
    execute(
        """
        CREATE TABLE IF NOT EXISTS driver_status (
            driver_id INTEGER PRIMARY KEY REFERENCES users(id),
            is_online BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )

# ---------- Schemas ----------

class LocationSchema(Schema):
    lat = fields.Float(required=True, description="Latitude")
    lng = fields.Float(required=True, description="Longitude")

class AvailabilitySchema(Schema):
    online = fields.Boolean(required=True, description="Set driver online (true) or offline (false)")

class MessageSchema(Schema):
    message = fields.String(required=True)

class RideIdParamSchema(Schema):
    ride_id = fields.Integer(required=True)

class AcceptRideRequestSchema(Schema):
    accept = fields.Boolean(required=True, description="Accept true to accept, false to decline")

class DriverStatusResponseSchema(Schema):
    driver_id = fields.Integer(required=True)
    is_online = fields.Boolean(required=True)
    location = fields.Nested(LocationSchema, allow_none=True)
    updated_at = fields.DateTime(allow_none=True)

# ---------- Helpers ----------

def _require_driver_identity() -> Dict[str, Any]:
    ident = get_jwt_identity() or {}
    if ident.get("role") != "driver":
        abort(403, message="Only drivers may access this endpoint")
    return ident

def _ride_belongs_to_driver(ride: Dict[str, Any], driver_id: int) -> bool:
    return (ride or {}).get("driver_id") == driver_id

# ---------- Routes ----------

@blp.route("/availability")
class DriverAvailabilityView(MethodView):
    """Set driver availability online/offline and view current availability."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def get(self):
        """Get current driver's availability and last known location.

        Summary:
            Driver availability status
        Responses:
            200: DriverStatusResponseSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]

        status = query_one("SELECT is_online, updated_at FROM driver_status WHERE driver_id = %s", [driver_id])
        loc = query_one("SELECT lat, lng, updated_at FROM driver_locations WHERE driver_id = %s", [driver_id])

        return {
            "driver_id": driver_id,
            "is_online": bool(status["is_online"]) if status else False,
            "location": {"lat": loc["lat"], "lng": loc["lng"]} if loc else None,
            "updated_at": status["updated_at"] if status else None,
        }, 200

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Update driver availability.

        Summary:
            Set driver online/offline
        Request Body:
            application/json: AvailabilitySchema
        Responses:
            200: MessageSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]
        payload = AvailabilitySchema().load(request.get_json() or {})
        online = bool(payload["online"])

        # Upsert driver_status
        updated = execute(
            """
            INSERT INTO driver_status (driver_id, is_online, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (driver_id) DO UPDATE
            SET is_online = EXCLUDED.is_online, updated_at = NOW()
            """,
            [driver_id, online],
        )
        if updated <= 0:
            abort(500, message="Failed to update availability")

        return {"message": "online" if online else "offline"}, 200


@blp.route("/location")
class DriverLocationView(MethodView):
    """Update driver location for matching and tracking."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self):
        """Update current driver latitude/longitude.

        Summary:
            Update driver location
        Request Body:
            application/json: LocationSchema
        Responses:
            200: MessageSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]
        loc = LocationSchema().load(request.get_json() or {})

        # Only accept updates if driver is online
        status = query_one("SELECT is_online FROM driver_status WHERE driver_id = %s", [driver_id])
        if not status or not bool(status["is_online"]):
            abort(409, message="Driver must be online to update location")

        # Upsert location
        updated = execute(
            """
            INSERT INTO driver_locations (driver_id, lat, lng, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (driver_id) DO UPDATE
            SET lat = EXCLUDED.lat, lng = EXCLUDED.lng, updated_at = NOW()
            """,
            [driver_id, loc["lat"], loc["lng"]],
        )
        if updated <= 0:
            abort(500, message="Failed to update location")

        return {"message": "location_updated"}, 200


@blp.route("/rides/<int:ride_id>/accept")
class DriverAcceptRideView(MethodView):
    """Accept or decline a ride that is in 'assigned' state and assigned to this driver."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Accept or decline an assigned ride.

        Summary:
            Accept or decline ride
        Parameters:
            ride_id: path integer
        Request Body:
            application/json: AcceptRideRequestSchema
        Responses:
            200: MessageSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]
        data = AcceptRideRequestSchema().load(request.get_json() or {})

        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if ride["status"] != "assigned":
            abort(409, message=f"Ride is not in assigned state (current={ride['status']})")
        if not _ride_belongs_to_driver(ride, driver_id):
            abort(403, message="Ride not assigned to this driver")

        if data["accept"]:
            # Transition to ongoing only on explicit start; accept keeps 'assigned' but acknowledges acceptance.
            # We simply acknowledge to keep state machine explicit with /start endpoint.
            return {"message": "accepted"}, 200
        else:
            # Decline: unassign driver and revert to requested for reassignment
            updated = execute(
                """
                UPDATE rides
                SET driver_id = NULL, status = 'requested', assigned_at = NULL
                WHERE id = %s AND status = 'assigned' AND driver_id = %s
                """,
                [ride_id, driver_id],
            )
            if updated <= 0:
                abort(409, message="Unable to decline ride in current state")
            return {"message": "declined"}, 200


@blp.route("/rides/<int:ride_id>/start")
class DriverStartRideView(MethodView):
    """Start a ride that is assigned to the driver, transitioning to 'ongoing'."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Start the ride (assigned driver only).

        Summary:
            Start ride
        Parameters:
            ride_id: path integer
        Responses:
            200: MessageSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]

        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if not _ride_belongs_to_driver(ride, driver_id):
            abort(403, message="Ride not assigned to this driver")
        if ride["status"] not in ("assigned", "requested"):
            abort(409, message=f"Ride cannot be started from state {ride['status']}")

        updated = execute(
            """
            UPDATE rides
            SET status = 'ongoing', started_at = NOW()
            WHERE id = %s AND driver_id = %s AND status IN ('assigned','requested')
            """,
            [ride_id, driver_id],
        )
        if updated <= 0:
            abort(409, message="Unable to start ride in current state")

        return {"message": "started"}, 200


@blp.route("/rides/<int:ride_id>/complete")
class DriverCompleteRideView(MethodView):
    """Alias driver completion endpoint to the same transition as rides complete, but scoped to driver actions."""

    # PUBLIC_INTERFACE
    @jwt_required()
    def post(self, ride_id: int):
        """Complete the ride (assigned driver only). Computes final fare in rides route; here we just change state if allowed.

        Summary:
            Complete ride (driver)
        Parameters:
            ride_id: path integer
        Responses:
            200: MessageSchema
        """
        _ensure_tables()
        ident = _require_driver_identity()
        driver_id = ident["id"]

        ride = query_one("SELECT * FROM rides WHERE id = %s", [ride_id])
        if not ride:
            abort(404, message="Ride not found")
        if not _ride_belongs_to_driver(ride, driver_id):
            abort(403, message="Ride not assigned to this driver")
        if ride["status"] in ("completed", "canceled"):
            abort(409, message=f"Ride already {ride['status']}")

        # Let rides.complete handle fare calc; we mirror a safe transition to completed if ongoing/assigned/requested.
        updated = execute(
            """
            UPDATE rides
            SET status = 'completed', completed_at = NOW()
            WHERE id = %s AND driver_id = %s AND status IN ('assigned','ongoing','requested')
            """,
            [ride_id, driver_id],
        )
        if updated <= 0:
            abort(409, message="Unable to complete ride in current state")
        return {"message": "completed"}, 200
