import os
import uuid
from typing import Dict

# PUBLIC_INTERFACE
def get_provider_name() -> str:
    """Return configured payment provider name."""
    return os.getenv("PAYMENT_PROVIDER", "fake")

# PUBLIC_INTERFACE
def create_payment_intent(amount_cents: int, currency: str = "usd", metadata: Dict[str, str] | None = None) -> dict:
    """Stubbed payment intent creation. In production, integrate with real provider."""
    if get_provider_name() != "fake":
        # Placeholder for future integrations (Stripe, etc.)
        pass
    return {
        "id": f"pi_{uuid.uuid4().hex}",
        "amount": amount_cents,
        "currency": currency,
        "status": "requires_confirmation",
        "client_secret": f"secret_{uuid.uuid4().hex}",
        "metadata": metadata or {},
        "provider": get_provider_name(),
    }

# PUBLIC_INTERFACE
def confirm_payment(intent_id: str) -> dict:
    """Stubbed confirm step."""
    return {
        "id": intent_id,
        "status": "succeeded",
        "provider": get_provider_name(),
    }
