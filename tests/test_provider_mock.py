"""Unit tests for Provider abstraction and Mock Fixtures."""

import pytest
from tripcaspian.providers import IRCTCProvider, RedBusProvider, CabProvider
from tripcaspian.providers.mock_data import get_mock_options


def test_irctc_provider_search():
    provider = IRCTCProvider()
    results = provider.search("Delhi", "Jaipur")
    assert len(results) >= 1
    assert any("IRCTC" in r.operator for r in results)
    link = provider.build_booking_link(results[0])
    assert "irctc" in link.lower()


def test_redbus_provider_search():
    provider = RedBusProvider()
    results = provider.search("Delhi", "Jaipur")
    assert len(results) >= 1
    assert any("bus" in r.mode for r in results)
    link = provider.build_booking_link(results[0])
    assert "redbus" in link.lower()


def test_cab_provider_search():
    provider = CabProvider()
    results = provider.search("Delhi", "Jaipur")
    assert len(results) >= 1
    assert any("cab" in r.mode for r in results)
    link = provider.build_booking_link(results[0])
    assert "uber" in link.lower() or "olacabs" in link.lower()


def test_dynamic_mock_fallback_for_unknown_route():
    options = get_mock_options("Chandigarh", "Shimla")
    assert len(options) >= 3
    modes = {o.mode for o in options}
    assert "train" in modes
    assert "bus" in modes
    assert "cab" in modes
