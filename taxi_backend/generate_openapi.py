import json
import os
from app import app, api  # import your Flask app and Api instance

def generate() -> str:
    """
    Generate the OpenAPI specification to taxi_backend/interfaces/openapi.json.

    Returns:
        The absolute path to the written openapi.json file.
    """
    with app.app_context():
        # flask-smorest stores the spec in api.spec
        openapi_spec = api.spec.to_dict()

        output_dir = "interfaces"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "openapi.json")

        with open(output_path, "w") as f:
            json.dump(openapi_spec, f, indent=2)

        return os.path.abspath(output_path)

if __name__ == "__main__":
    path = generate()
    print(f"OpenAPI spec generated at: {path}")
