"""Comparable single-site results, metric deltas and elapsed-time alignment."""


def compare_results(a: dict, b: dict) -> dict:
    """Compare compatible completed results; reject mixed site/date changes."""
    raise NotImplementedError


def register_comparison_routes(app, store):
    """Register POST /comparisons for two persisted, completed analysis IDs."""
    raise NotImplementedError
