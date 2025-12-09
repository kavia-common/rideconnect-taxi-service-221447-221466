from flask import jsonify
from flask_smorest import Blueprint
from flask.views import MethodView

# Corrected tag/name for better docs
blp = Blueprint("Health", "health", url_prefix="/", description="Health check route")

@blp.route("/")
class HealthCheck(MethodView):
    """Simple health check endpoint."""
    def get(self):
        """Return API health status."""
        return {"message": "Healthy"}

@blp.route("/docs")
class DocsIndex(MethodView):
    """Redirect hint for API documentation."""
    def get(self):
        """
        Return the URL to the Swagger UI served by flask-smorest.

        Note: The interactive API docs UI is served at /docs/ (trailing slash) by default.
        """
        return jsonify({"docs_ui": "/docs/"}), 200
