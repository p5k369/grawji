"""Tests for the rawji adapter's pure read-modify-write helper."""

import pytest

from grawji.core import OFFSET_FILM_SIM, rmw_patch


def test_rmw_patch_sets_film_sim_byte():
    base = bytes(OFFSET_FILM_SIM + 10)
    patched = rmw_patch(base, film_sim_byte=0x02)  # Velvia
    assert patched[OFFSET_FILM_SIM] == 0x02
    # Every other byte is untouched.
    assert patched[:OFFSET_FILM_SIM] == base[:OFFSET_FILM_SIM]
    assert patched[OFFSET_FILM_SIM + 1 :] == base[OFFSET_FILM_SIM + 1 :]


def test_rmw_patch_does_not_mutate_input():
    base = bytes(OFFSET_FILM_SIM + 10)
    rmw_patch(base, film_sim_byte=0x0C)
    assert base[OFFSET_FILM_SIM] == 0x00


def test_rmw_patch_rejects_short_profile():
    with pytest.raises(ValueError, match="too short"):
        rmw_patch(bytes(10), film_sim_byte=0x02)
