# rideconnect-taxi-service-221447-221466

Backend API (Flask + flask-smorest) with WebSocket support (Flask-SocketIO).

- Run server: `python taxi_backend/run.py` (serves on PORT env or 3001)
- Interactive API docs: open `http://localhost:3001/docs/` in your browser
  - A helper JSON pointer is also at `http://localhost:3001/docs`

OpenAPI generation:
- Generate and refresh the OpenAPI spec file after adding/modifying routes:
  ```
  cd taxi_backend
  python generate_openapi.py
  ```
- The spec is written to `taxi_backend/interfaces/openapi.json`