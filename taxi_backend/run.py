import os
from app import app, socketio

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3001"))
    # Use eventlet for WebSocket support
    socketio.run(app, host="0.0.0.0", port=port)
