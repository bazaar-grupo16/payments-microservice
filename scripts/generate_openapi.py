import json
import sys
import os
from pathlib import Path

# Set required environment variables for schema generation
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "changeme")
os.environ.setdefault("DB_NAME", "bazaar")
os.environ.setdefault("MP_ACCESS_TOKEN", "token")
os.environ.setdefault("MP_WEBHOOK_SECRET", "secret")
os.environ.setdefault("ORDERS_SERVICE_URL", "http://orders-service")

# Add the project root to the Python path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from src.main import app

def generate_openapi():
    openapi_schema = app.openapi()
    output_path = root_dir / "openapi.json"
    with open(output_path, "w") as f:
        json.dump(openapi_schema, f, indent=2)
    print(f"Successfully generated OpenAPI schema at {output_path}")

if __name__ == "__main__":
    generate_openapi()
