from typing import Any, Dict, Optional

from flask import request
from flask_jwt_extended import decode_token
from flask_socketio import Namespace, join_room, leave_room, emit, disconnect

# Namespace path constant to ensure consistency across app
TAXI_NAMESPACE = "/ws/taxi"


def _get_bearer_token(environ: Dict[str, Any]) -> Optional[str]:
    """Extract Bearer token from Authorization header for Socket.IO (engineio environ)."""
    headers = {}
    try:
        # environ may contain HTTP headers with names prefixed by "HTTP_"
        headers = {k[5:].replace("_", "-").title(): v for k, v in environ.items() if k.startswith("HTTP_")}
    except Exception:
        headers = {}
    auth = headers.get("Authorization") or headers.get("X-Authorization") or ""
    if isinstance(auth, (list, tuple)):
        auth = auth[0] if auth else ""
    if not isinstance(auth, str):
        return None
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    # Also check query string (e.g., ?token=...)
    qs_token = None
    try:
        qs_token = request.args.get("token")
    except Exception:
        # request may not be available in some engineio lifecycles
        pass
    return qs_token or None


def _decode_jwt(socketio, token: str) -> Optional[dict]:
    """Decode JWT using Flask app's JWT secret via flask-jwt-extended decode_token."""
    if not token:
        return None
    try:
        # decode_token will verify signature/expiry using app config under application context
        return decode_token(token) or None
    except Exception:
        return None


def _identity_from_decoded(decoded: dict) -> Optional[dict]:
    """Extract identity payload placed during JWT creation."""
    if not decoded:
        return None
    # flask-jwt-extended stores identity under 'sub' by default for complex objects
    ident = decoded.get("sub") or decoded.get("identity")
    if isinstance(ident, dict) and "id" in ident:
        return ident
    return None


class TaxiNamespace(Namespace):
    """Socket.IO namespace for taxi service with JWT authentication and ride rooms."""

    def on_connect(self):
        """Authenticate Socket.IO connection via JWT from Authorization header or query param."""
        token = _get_bearer_token(request.environ)
        decoded = _decode_jwt(self.server, token)
        ident = _identity_from_decoded(decoded)
        if not ident:
            # Reject connection
            return False
        # Attach to session for later events
        request.namespace_identity = ident  # type: ignore[attr-defined]
        return True

    def on_disconnect(self):
        # Nothing additional for now
        pass

    def on_join(self, data):
        """Join a room. Supports ride-specific rooms when provided with ride_id."""
        # Require auth per-event as a safeguard
        ident = getattr(request, "namespace_identity", None)
        if not ident:
            token = _get_bearer_token(request.environ)
            decoded = _decode_jwt(self.server, token)
            ident = _identity_from_decoded(decoded)
            if not ident:
                disconnect()
                return

        payload = data or {}
        room = payload.get("room")
        ride_id = payload.get("ride_id")
        # If ride_id provided, standardize room name to ride:<id>
        if ride_id is not None:
            room = f"ride:{ride_id}"
        if room:
            join_room(room)
            emit("joined", {"room": room})

    def on_leave(self, data):
        """Leave a room; mirrors join semantics for ride_id derived rooms."""
        payload = data or {}
        room = payload.get("room")
        ride_id = payload.get("ride_id")
        if ride_id is not None:
            room = f"ride:{ride_id}"
        if room:
            leave_room(room)
            emit("left", {"room": room})


# Helper emitters to be used by REST routes

# PUBLIC_INTERFACE
def emit_driver_location_update(socketio, driver_id: int, lat: float, lng: float) -> None:
    """Emit driver location update to any rooms subscribed to the driver and affected rides."""
    # Broadcast to driver room and globally under namespace
    socketio.emit(
        "driver_location_update",
        {"driver_id": driver_id, "lat": lat, "lng": lng},
        namespace=TAXI_NAMESPACE,
        to=f"driver:{driver_id}",
    )


# PUBLIC_INTERFACE
def emit_ride_status_update(socketio, ride_id: int, status: str, driver_id: Optional[int] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    """Emit ride status update to the ride-specific room."""
    payload: Dict[str, Any] = {"ride_id": ride_id, "status": status}
    if driver_id is not None:
        payload["driver_id"] = driver_id
    if extra:
        payload.update(extra)
    socketio.emit(
        "ride_status_update",
        payload,
        namespace=TAXI_NAMESPACE,
        to=f"ride:{ride_id}",
    )


# PUBLIC_INTERFACE
def standardize_ride_room(ride_id: int) -> str:
    """Return the standardized room name for a ride."""
    return f"ride:{ride_id}"


# PUBLIC_INTERFACE
def register_namespaces(socketio):
    """Register Socket.IO namespaces on the provided socketio instance."""
    socketio.on_namespace(TaxiNamespace(TAXI_NAMESPACE))
