"""Unit tests for official Uber Rides SDK provider implementation."""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from tripcaspian.providers.cab_ola_uber import UberProvider, ProviderError, geocode_city


def test_geocode_city():
    delhi_coords = geocode_city("Delhi")
    assert delhi_coords == (28.6139, 77.2090)

    jaipur_coords = geocode_city("Jaipur")
    assert jaipur_coords == (26.9124, 75.7873)

    unknown_coords = geocode_city("UnknownCity")
    assert unknown_coords == (28.6139, 77.2090)


def test_uber_provider_search_mock_fallback(monkeypatch):
    monkeypatch.setenv("PROVIDER_MODE", "mock")
    provider = UberProvider()

    routes = provider.search_routes("Delhi", "Jaipur")
    assert len(routes) >= 1
    assert any("Uber" in r.operator for r in routes)
    assert routes[0].mode == "cab"


def test_uber_provider_search_routes_live_mocked():
    provider = UberProvider()
    provider.is_live = True

    mock_client = MagicMock()
    mock_client.get_products.return_value = {
        "products": [{"product_id": "uber_xl_id", "display_name": "Uber XL"}]
    }
    mock_client.get_price_estimates.return_value = {
        "prices": [
            {
                "product_id": "uber_xl_id",
                "display_name": "Uber XL Intercity",
                "high_estimate": 3100.0,
                "duration": 16200,
            }
        ]
    }
    provider._client = mock_client

    routes = provider.search_routes("Delhi", "Jaipur")
    assert len(routes) == 1
    assert routes[0].operator == "Uber Uber XL Intercity"
    assert routes[0].price == 3100.0
    assert routes[0].is_mock is False


def test_uber_provider_estimate():
    provider = UberProvider()

    # Fallback / mock estimation
    est = provider.estimate("Delhi", "Jaipur")
    assert "price" in est
    assert est["price"] > 0


def test_uber_provider_booking_flow():
    provider = UberProvider()
    provider._client = MagicMock()
    provider._rider_oauth = True

    provider._client.request_ride.return_value = {
        "request_id": "uber_req_12345",
        "status": "processing",
        "driver": {"name": "Test Driver", "rating": 4.9},
    }
    provider._client.get_ride_details.return_value = {
        "request_id": "uber_req_12345",
        "status": "accepted",
    }
    provider._client.cancel_ride.return_value = None

    from tripcaspian.providers.base import RouteOption
    opt = RouteOption(
        id="uber_xl_id",
        mode="cab",
        operator="Uber XL",
        price=3000.0,
        depart="Immediate",
        arrive="4h",
        duration_minutes=240,
        seats_left=1,
        deep_link="http://uber.com",
        source="Delhi",
        destination="Jaipur",
        is_mock=False,
    )

    # Test initiate_booking
    booking_result = provider.initiate_booking(opt)
    assert booking_result["request_id"] == "uber_req_12345"
    assert booking_result["status"] == "processing"

    # Test get_booking_status
    status_result = provider.get_booking_status("uber_req_12345")
    assert status_result["status"] == "accepted"

    # Test cancel_booking
    cancel_result = provider.cancel_booking("uber_req_12345")
    assert cancel_result["status"] == "canceled"


def test_oauth_helper_functions(tmp_path, monkeypatch):
    monkeypatch.setenv("UBER_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("UBER_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("UBER_REDIRECT_URI", "http://localhost:8000/callback")

    creds_file = str(tmp_path / "uber_test_creds.json")
    provider = UberProvider(credentials_path=creds_file)

    # 1. create_auth_url
    url, state = provider.create_auth_url()
    assert "login.uber.com" in url or "uber" in url.lower()
    assert len(state) > 0

    # 2. save_credentials and load_credentials
    test_creds = {
        "access_token": "mock_access_token_123",
        "refresh_token": "mock_refresh_token_456",
        "expires_in": 3600,
    }
    provider.save_credentials(test_creds, filepath=creds_file)

    loaded = provider.load_credentials(filepath=creds_file)
    assert loaded["access_token"] == "mock_access_token_123"
    assert loaded["refresh_token"] == "mock_refresh_token_456"
