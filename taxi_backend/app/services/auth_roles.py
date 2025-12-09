from typing import Iterable

# PUBLIC_INTERFACE
def allowed_roles(*roles: str) -> Iterable[str]:
    """Return a set of allowed roles for decorators or checks."""
    return set(roles)
