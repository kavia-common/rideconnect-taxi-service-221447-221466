import os

def _get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

# PUBLIC_INTERFACE
def base_fare() -> float:
    """Return base fare from env FARE_BASE or default."""
    return _get_float_env("FARE_BASE", 3.0)

# PUBLIC_INTERFACE
def per_km() -> float:
    """Return per km fare from env FARE_PER_KM or default."""
    return _get_float_env("FARE_PER_KM", 1.2)

# PUBLIC_INTERFACE
def per_min() -> float:
    """Return per minute fare from env FARE_PER_MIN or default."""
    return _get_float_env("FARE_PER_MIN", 0.3)

# PUBLIC_INTERFACE
def estimate_cost(distance_km: float, duration_min: float) -> float:
    """Compute estimated ride cost using base + per_km*distance + per_min*duration."""
    return round(base_fare() + per_km() * float(distance_km) + per_min() * float(duration_min), 2)
