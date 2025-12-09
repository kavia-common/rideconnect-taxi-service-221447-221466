import os
import uuid
from typing import Dict, Optional

# PUBLIC_INTERFACE
def get_provider_name() -> str:
    """Return configured payment provider name."""
    return os.getenv("PAYMENT_PROVIDER", "fake")

# PUBLIC_INTERFACE
def create_payment_intent(amount_cents: int, currency: str = "usd", metadata: Optional[Dict[str, str]] = None) -> dict:
    """Create a payment intent with a (stubbed) payment provider.

    In production, integrate with Stripe/Adyen/etc. For now, we generate a fake intent.
    """
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")

    # Placeholder for real provider integrations
    provider = get_provider_name()

    return {
        "id": f"pi_{uuid.uuid4().hex}",
        "amount": int(amount_cents),
        "currency": currency or "usd",
        "status": "requires_confirmation",
        "client_secret": f"secret_{uuid.uuid4().hex}",
        "metadata": metadata or {},
        "provider": provider,
    }

# PUBLIC_INTERFACE
def confirm_payment(intent_id: str) -> dict:
    """Confirm a payment intent (stubbed to always succeed)."""
    provider = get_provider_name()
    return {
        "id": intent_id,
        "status": "succeeded",
        "provider": provider,
    }
