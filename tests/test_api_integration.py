"""Comprehensive API Integration Test Suite for TripCaspian Providers.

Exercises and verifies:
1. IRCTC Provider (search, availability, booking link generation).
2. redBus Provider (cities search, bus services search, availability, booking link generation).
3. Uber Provider (geocode, products, price estimates, estimate_ride, booking request/status/cancel, OAuth helper functions).
4. End-to-End Service Layer integration (TripService multi-turn state machine and handoff).
"""

import os
import logging
import pytest
from tripcaspian.providers.train_irctc import IRCTCProvider
from tripcaspian.providers.bus_redbus import RedBusProvider
from tripcaspian.providers.cab_ola_uber import UberProvider, geocode_city, ProviderError
from tripcaspian.storage import SQLiteStorage
from tripcaspian.service import TripService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_testing")


def test_irctc_api_integration():
    """Test IRCTC Provider API search and booking link generation."""
    provider = IRCTCProvider()
    logger.info("Testing IRCTC Provider...")

    routes = provider.search(source="Delhi", destination="Jaipur")
    assert len(routes) > 0, "IRCTC search returned no routes"
    assert routes[0].mode == "train"

    avail = provider.check_availability(routes[0].id)
    assert "seats_left" in avail
    assert "price" in avail

    link = provider.build_booking_link(routes[0])
    assert "irctc.co.in" in link
    logger.info("IRCTC Provider test PASSED (%d routes found).", len(routes))


def test_redbus_api_integration():
    """Test redBus Provider API search, seat layout/details, and booking link."""
    provider = RedBusProvider()
    logger.info("Testing redBus Provider...")

    routes = provider.search(source="Mumbai", destination="Pune")
    assert len(routes) > 0, "redBus search returned no routes"
    assert routes[0].mode == "bus"

    avail = provider.check_availability(routes[0].id)
    assert "seats_left" in avail

    link = provider.build_booking_link(routes[0])
    assert "redbus.in" in link
    logger.info("redBus Provider test PASSED (%d routes found).", len(routes))


def test_uber_api_integration(monkeypatch, tmp_path):
    """Test Uber Provider API integration, products, estimates, ride request & cancellation, and OAuth."""
    monkeypatch.setenv("UBER_CLIENT_ID", "dummy_client_id")
    monkeypatch.setenv("UBER_CLIENT_SECRET", "dummy_client_secret")
    monkeypatch.setenv("UBER_REDIRECT_URI", "http://localhost:8000/callback")
    monkeypatch.setenv("UBER_SANDBOX", "true")

    creds_file = str(tmp_path / "uber_creds_test.json")
    provider = UberProvider(credentials_path=creds_file)
    logger.info("Testing Uber Provider SDK...")

    # Geocoding test
    coords = geocode_city("Delhi")
    assert coords == (28.6139, 77.2090)

    # Route search
    routes = provider.search_routes(source="Delhi", destination="Jaipur")
    assert len(routes) > 0, "Uber search_routes returned no routes"
    assert routes[0].mode == "cab"

    # Ride estimation
    estimate = provider.estimate(source="Delhi", destination="Jaipur")
    assert "price" in estimate

    # Booking Handoff Link / Auth Url
    link = provider.build_booking_link(routes[0])
    assert "uber.com" in link

    # OAuth helper testing
    auth_url, state = provider.create_auth_url()
    assert "login.uber.com" in auth_url or "uber" in auth_url.lower()

    # Save & Load Credentials helper
    creds_data = {"access_token": "token_123", "refresh_token": "refresh_456"}
    provider.save_credentials(creds_data, filepath=creds_file)
    loaded = provider.load_credentials(filepath=creds_file)
    assert loaded["access_token"] == "token_123"

    # Mocked Ride lifecycle: request -> status -> cancel
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.request_ride.return_value = {"request_id": "req_999", "status": "processing"}
    mock_client.get_ride_details.return_value = {"request_id": "req_999", "status": "accepted"}
    mock_client.cancel_ride.return_value = None

    provider._client = mock_client
    provider._rider_oauth = True

    booking_resp = provider.initiate_booking(routes[0])
    assert booking_resp["request_id"] == "req_999"

    status_resp = provider.get_booking_status("req_999")
    assert status_resp["status"] == "accepted"

    cancel_resp = provider.cancel_booking("req_999")
    assert cancel_resp["status"] == "canceled"

    logger.info("Uber Provider SDK test PASSED.")


def test_end_to_end_trip_service_api(tmp_path):
    """Test full multi-turn conversational trip service API."""
    db_file = str(tmp_path / "test_e2e_api.db")
    storage = SQLiteStorage(db_path=db_file)
    service = TripService(storage=storage)

    conv_id = "e2e_api_conv_1"

    # Step 1: Initial user query
    r1 = service.handle_user_message(conv_id, None, "I want to travel from Delhi to Jaipur for under 1500 rupees")
    assert "Top Travel Routes for Delhi ➡️ Jaipur" in r1

    # Step 2: Select option
    r2 = service.handle_user_message(conv_id, None, "book option 1")
    assert "You selected Option 1" in r2

    # Step 3: Immediate handoff
    r3 = service.handle_user_message(conv_id, None, "book now")
    assert "Your Trip Booking Handoff is Ready!" in r3
    assert "Click here to book now" in r3

    logger.info("End-to-End TripService API test PASSED.")
