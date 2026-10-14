"""What every test shares."""

from __future__ import annotations

import pytest

from sports_follow.adapters import budget


@pytest.fixture(autouse=True)
def source_budgets(monkeypatch):
    """Source budgets (adapters/budget.py) count in memory, fresh for each test: no test reaches Redis, or
    spends what another counted."""
    store = budget.MemoryStore()
    monkeypatch.setattr(budget, "STORE", store)
    return store
