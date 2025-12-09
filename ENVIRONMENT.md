Environment wiring overview

Backend (Flask + Socket.IO)
- Reads:
  - PORT (default 3001)
  - DATABASE_URL (fallback to ../rideconnect-taxi-service-221447-221468/database/db_connection.txt)
  - JWT_SECRET (randomly generated if not provided)
  - CORS_ORIGINS (default http://localhost:3000)
  - FARE_BASE, FARE_PER_KM, FARE_PER_MIN (defaults 3.0, 1.2, 0.3)
  - PAYMENT_PROVIDER (default fake)
  - SOCKETIO_CORS_ORIGINS (optional; default "*")
- Socket.IO namespace: /ws/taxi
- Engine.IO path: /socket.io (default)
- See taxi_backend/.env.example

Frontend (Vite + Svelte)
- Reads:
  - VITE_API_BASE: REST API base; defaults to current origin with port 3001
  - VITE_WS_BASE: WebSocket base; defaults to ws(s)://<host>:3001
- Socket.IO namespace: /ws/taxi
- Engine.IO path: /socket.io
- See taxi_frontend/.env.example

Local development
- Start backend on port 3001 (python taxi_backend/run.py)
- Start frontend on port 3000 (npm run dev / pnpm dev)
- Ensure CORS_ORIGINS includes http://localhost:3000
