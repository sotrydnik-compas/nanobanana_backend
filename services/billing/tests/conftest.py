import sys
import os
from pathlib import Path


# Ensure `import app...` works when running `pytest` from the service directory.
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


# Minimal env for settings initialization during app import.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/billing_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token")

# Required Tochka settings (even if PAYMENT_ENABLED is false).
os.environ.setdefault("TOCHKA_BEARER_TOKEN", "test")
os.environ.setdefault("TOCHKA_CUSTOMER_CODE", "test")
os.environ.setdefault("TOCHKA_MERCHANT_ID", "test")
os.environ.setdefault("TOCHKA_WEBHOOK_PUBLIC_KEY", "test")
