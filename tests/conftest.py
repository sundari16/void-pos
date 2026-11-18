from __future__ import annotations

from collections.abc import Iterator

import pytest

from support import dispose_engines


@pytest.fixture(autouse=True)
def _close_test_engines() -> Iterator[None]:
    """Each test's engines are disposed once its fixtures have closed their sessions."""
    yield
    dispose_engines()
