from flask_socketio import Namespace, join_room, leave_room

class TaxiNamespace(Namespace):
    """Basic Socket.IO namespace for ride events."""

    def on_connect(self):
        return True

    def on_disconnect(self):
        pass

    def on_join(self, data):
        room = (data or {}).get("room")
        if room:
            join_room(room)

    def on_leave(self, data):
        room = (data or {}).get("room")
        if room:
            leave_room(room)

# PUBLIC_INTERFACE
def register_namespaces(socketio):
    """Register Socket.IO namespaces on the provided socketio instance."""
    socketio.on_namespace(TaxiNamespace("/ws/taxi"))
