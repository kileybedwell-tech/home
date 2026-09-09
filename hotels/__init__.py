"""Find the cheapest hotel for a stay: ``python -m hotels search``."""

from .search import Quote, cheapest, nights_between, rank

__all__ = ["Quote", "cheapest", "nights_between", "rank"]
