from __future__ import annotations

from baleobala.bale.auth_browser import _iran_local


def test_iran_local_normalizes_international_and_local_numbers() -> None:
    assert _iran_local(989120000000) == "9120000000"
    assert _iran_local(9120000000) == "9120000000"
