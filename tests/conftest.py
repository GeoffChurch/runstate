"""Shared test fixtures."""

import pytest


@pytest.fixture(params=["file", "sqlite"])
def backend(request):
    """Parametrize tests over both Channel backends."""
    return request.param
