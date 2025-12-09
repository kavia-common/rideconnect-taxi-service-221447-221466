from typing import Any, Dict, Iterable, Optional
from . import app

_db = app.extensions["db"]

# PUBLIC_INTERFACE
def query_all(sql: str, params: Optional[Iterable[Any]] = None) -> Iterable[Dict[str, Any]]:
    """Run a SELECT returning multiple rows as dictionaries."""
    return _db.fetchall(sql, params)

# PUBLIC_INTERFACE
def query_one(sql: str, params: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]:
    """Run a SELECT returning a single row as a dictionary."""
    return _db.fetchone(sql, params)

# PUBLIC_INTERFACE
def execute(sql: str, params: Optional[Iterable[Any]] = None) -> int:
    """Run a write operation and return affected rows."""
    return _db.execute(sql, params)
