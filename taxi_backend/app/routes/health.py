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
