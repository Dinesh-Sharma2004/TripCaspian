"""Realistic mock travel fixtures and dynamic generator for TripCaspian testing and demo mode.

Covers trains, buses, and cabs across popular Indian routes with realistic pricing, schedules, and seat counts.
"""

import hashlib
import random
from typing import Any
from tripcaspian.providers.base import RouteOption

MOCK_FIXTURES: list[dict[str, Any]] = [
    # Delhi to Jaipur
    {
        "id": "train_irctc_20978",
        "mode": "train",
        "operator": "IRCTC Vande Bharat Express (20978)",
        "price": 1050.0,
        "depart": "06:10 AM",
        "arrive": "10:45 AM",
        "duration_minutes": 275,
        "seats_left": 14,
        "deep_link": "https://www.irctc.co.in/nget/booking/train-list?src=NDLS&dst=JP",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },
    {
        "id": "train_irctc_12986",
        "mode": "train",
        "operator": "IRCTC Double Decker Express (12986)",
        "price": 640.0,
        "depart": "05:35 PM",
        "arrive": "10:05 PM",
        "duration_minutes": 270,
        "seats_left": 3,
        "deep_link": "https://www.irctc.co.in/nget/booking/train-list?src=DEE&dst=JP",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },
    {
        "id": "bus_redbus_zb101",
        "mode": "bus",
        "operator": "Zingbus AC Volvo Sleeper",
        "price": 750.0,
        "depart": "07:00 AM",
        "arrive": "12:30 PM",
        "duration_minutes": 330,
        "seats_left": 8,
        "deep_link": "https://www.redbus.in/bus-tickets/delhi-to-jaipur",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },
    {
        "id": "bus_redbus_ic202",
        "mode": "bus",
        "operator": "IntrCity SmartBus Volvo",
        "price": 680.0,
        "depart": "08:30 AM",
        "arrive": "02:00 PM",
        "duration_minutes": 330,
        "seats_left": 2,
        "deep_link": "https://www.redbus.in/bus-tickets/delhi-to-jaipur",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },
    {
        "id": "cab_uber_xl01",
        "mode": "cab",
        "operator": "Uber Intercity Premier",
        "price": 3200.0,
        "depart": "On-demand (Immediate)",
        "arrive": "+4h 30m after depart",
        "duration_minutes": 270,
        "seats_left": 1,
        "deep_link": "https://m.uber.com/ul/?action=setPickup&pickup=Delhi&drop=Jaipur",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },
    {
        "id": "cab_ola_prime01",
        "mode": "cab",
        "operator": "Ola Outstation Sedan",
        "price": 2850.0,
        "depart": "On-demand (Immediate)",
        "arrive": "+4h 45m after depart",
        "duration_minutes": 285,
        "seats_left": 1,
        "deep_link": "https://book.olacabs.com/outstation?from=Delhi&to=Jaipur",
        "source": "Delhi",
        "destination": "Jaipur",
        "is_mock": True,
    },

    # Mumbai to Pune
    {
        "id": "train_irctc_12127",
        "mode": "train",
        "operator": "IRCTC Intercity Express (12127)",
        "price": 420.0,
        "depart": "06:40 AM",
        "arrive": "09:57 AM",
        "duration_minutes": 197,
        "seats_left": 18,
        "deep_link": "https://www.irctc.co.in/nget/booking/train-list?src=CSMT&dst=PUNE",
        "source": "Mumbai",
        "destination": "Pune",
        "is_mock": True,
    },
    {
        "id": "train_irctc_22225",
        "mode": "train",
        "operator": "IRCTC Vande Bharat Express (22225)",
        "price": 885.0,
        "depart": "06:05 AM",
        "arrive": "09:15 AM",
        "duration_minutes": 190,
        "seats_left": 4,
        "deep_link": "https://www.irctc.co.in/nget/booking/train-list?src=CSMT&dst=PUNE",
        "source": "Mumbai",
        "destination": "Pune",
        "is_mock": True,
    },
    {
        "id": "bus_redbus_ms01",
        "mode": "bus",
        "operator": "MSRTC Shivneri AC Scania",
        "price": 510.0,
        "depart": "07:30 AM",
        "arrive": "11:00 AM",
        "duration_minutes": 210,
        "seats_left": 6,
        "deep_link": "https://www.redbus.in/bus-tickets/mumbai-to-pune",
        "source": "Mumbai",
        "destination": "Pune",
        "is_mock": True,
    },
    {
        "id": "cab_uber_go01",
        "mode": "cab",
        "operator": "Uber Intercity Go",
        "price": 2100.0,
        "depart": "On-demand (Immediate)",
        "arrive": "+3h 15m after depart",
        "duration_minutes": 195,
        "seats_left": 1,
        "deep_link": "https://m.uber.com/ul/?action=setPickup&pickup=Mumbai&drop=Pune",
        "source": "Mumbai",
        "destination": "Pune",
        "is_mock": True,
    },

    # Bengaluru to Chennai
    {
        "id": "train_irctc_20608",
        "mode": "train",
        "operator": "IRCTC Vande Bharat Express (20608)",
        "price": 995.0,
        "depart": "05:45 AM",
        "arrive": "10:25 AM",
        "duration_minutes": 280,
        "seats_left": 12,
        "deep_link": "https://www.irctc.co.in/nget/booking/train-list?src=SBC&dst=MAS",
        "source": "Bengaluru",
        "destination": "Chennai",
        "is_mock": True,
    },
    {
        "id": "bus_redbus_kstrc01",
        "mode": "bus",
        "operator": "KSRTC Flybus AC Multi-Axle",
        "price": 650.0,
        "depart": "08:00 AM",
        "arrive": "02:00 PM",
        "duration_minutes": 360,
        "seats_left": 3,
        "deep_link": "https://www.redbus.in/bus-tickets/bangalore-to-chennai",
        "source": "Bengaluru",
        "destination": "Chennai",
        "is_mock": True,
    },
]


def get_mock_options(
    source: str, destination: str, mode_filter: str | None = None
) -> list[RouteOption]:
    """Retrieve matching mock options or generate realistic dynamic fallback options."""
    src_norm = source.strip().lower()
    dst_norm = destination.strip().lower()

    results: list[RouteOption] = []
    for item in MOCK_FIXTURES:
        if (
            item["source"].lower() in src_norm or src_norm in item["source"].lower()
        ) and (
            item["destination"].lower() in dst_norm or dst_norm in item["destination"].lower()
        ):
            if mode_filter is None or item["mode"] == mode_filter:
                results.append(RouteOption.from_dict(item))

    # If no exact fixture matches the route, generate deterministic dynamic realistic options
    if not results:
        seed = int(hashlib.md5(f"{src_norm}-{dst_norm}".encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)

        # Dynamic Train
        if mode_filter is None or mode_filter == "train":
            price_t = rng.randint(450, 1100)
            results.append(
                RouteOption(
                    id=f"train_irctc_{rng.randint(10000, 99999)}",
                    mode="train",
                    operator=f"IRCTC Express ({rng.randint(12000, 22000)})",
                    price=float(price_t),
                    depart="07:15 AM",
                    arrive="12:45 PM",
                    duration_minutes=330,
                    seats_left=rng.randint(2, 15),
                    deep_link=f"https://www.irctc.co.in/nget/booking/train-list?src={source[:3].upper()}&dst={destination[:3].upper()}",
                    source=source,
                    destination=destination,
                    is_mock=True,
                )
            )

        # Dynamic Bus
        if mode_filter is None or mode_filter == "bus":
            price_b = rng.randint(550, 950)
            results.append(
                RouteOption(
                    id=f"bus_redbus_{rng.randint(100, 999)}",
                    mode="bus",
                    operator="IntrCity AC Sleeper Bus",
                    price=float(price_b),
                    depart="08:30 AM",
                    arrive="02:30 PM",
                    duration_minutes=360,
                    seats_left=rng.randint(1, 8),
                    deep_link=f"https://www.redbus.in/bus-tickets/{source.lower()}-to-{destination.lower()}",
                    source=source,
                    destination=destination,
                    is_mock=True,
                )
            )

        # Dynamic Cab
        if mode_filter is None or mode_filter == "cab":
            price_c = rng.randint(2200, 3800)
            results.append(
                RouteOption(
                    id=f"cab_uber_{rng.randint(10, 99)}",
                    mode="cab",
                    operator="Uber Intercity Sedan",
                    price=float(price_c),
                    depart="On-demand (Immediate)",
                    arrive="+5h after depart",
                    duration_minutes=300,
                    seats_left=1,
                    deep_link=f"https://m.uber.com/ul/?action=setPickup&pickup={source}&drop={destination}",
                    source=source,
                    destination=destination,
                    is_mock=True,
                )
            )

    return results
