"""Static airport reference data used by the simulator for great-circle routes.

Airports are grouped by region so the simulator's spawn pool can be selected
via the `REGION` env var (`europe` / `us` / `india` / `all`) without hardcoding
any single region as the only option.
"""

from __future__ import annotations

from typing import NamedTuple


class Airport(NamedTuple):
    iata: str
    country: str
    lat: float
    lon: float


# A spread of major airports within and around the default Europe bounding box
# (lamin=45 lomin=5 lamax=56 lomax=20), plus a few just outside it so routes
# have realistic variety in origin/destination country.
AIRPORTS_EUROPE: list[Airport] = [
    Airport("LHR", "United Kingdom", 51.4700, -0.4543),
    Airport("CDG", "France", 49.0097, 2.5479),
    Airport("FRA", "Germany", 50.0379, 8.5622),
    Airport("AMS", "Netherlands", 52.3105, 4.7683),
    Airport("MAD", "Spain", 40.4983, -3.5676),
    Airport("FCO", "Italy", 41.8003, 12.2389),
    Airport("ZRH", "Switzerland", 47.4647, 8.5492),
    Airport("VIE", "Austria", 48.1103, 16.5697),
    Airport("CPH", "Denmark", 55.6180, 12.6560),
    Airport("ARN", "Sweden", 59.6519, 17.9186),
    Airport("WAW", "Poland", 52.1657, 20.9671),
    Airport("PRG", "Czechia", 50.1008, 14.2600),
    Airport("BRU", "Belgium", 50.9014, 4.4844),
    Airport("MUC", "Germany", 48.3538, 11.7861),
    Airport("BCN", "Spain", 41.2971, 2.0785),
    Airport("MXP", "Italy", 45.6306, 8.7281),
    Airport("OSL", "Norway", 60.1939, 11.1004),
    Airport("HEL", "Finland", 60.3172, 24.9633),
    Airport("DUB", "Ireland", 53.4213, -6.2701),
    Airport("LIS", "Portugal", 38.7813, -9.1359),
    # Added 2026-08-31: the original 20 had a real hole across the Baltic
    # states and interior Balkans -- easternmost/southeasternmost coverage
    # was WAW/VIE, nothing between there and Turkey. Corridors in that area
    # had no major airport to snap to even once the new
    # adsb_lol_points entries (infra/terraform/variables.tf) start
    # collecting data there. See ml/corridors.py-derived corridor-discovery
    # docs for the snapping heuristic these feed.
    Airport("RIX", "Latvia", 56.9236, 23.9711),
    Airport("VNO", "Lithuania", 54.6341, 25.2858),
    Airport("TLL", "Estonia", 59.4133, 24.8328),
    Airport("OTP", "Romania", 44.5711, 26.0850),
    Airport("SOF", "Bulgaria", 42.6952, 23.4062),
    Airport("BEG", "Serbia", 44.8184, 20.3091),
]

# A spread of major US airports (both coasts + interior hubs) so the "us"
# region has realistic route variety, mirroring the Europe set.
AIRPORTS_US: list[Airport] = [
    Airport("JFK", "United States", 40.6413, -73.7781),
    Airport("LAX", "United States", 33.9416, -118.4085),
    Airport("ORD", "United States", 41.9742, -87.9073),
    Airport("ATL", "United States", 33.6407, -84.4277),
    Airport("DFW", "United States", 32.8998, -97.0403),
    Airport("DEN", "United States", 39.8561, -104.6737),
    Airport("SFO", "United States", 37.6213, -122.3790),
    Airport("SEA", "United States", 47.4502, -122.3088),
    Airport("MIA", "United States", 25.7959, -80.2870),
    Airport("BOS", "United States", 42.3656, -71.0096),
    Airport("IAH", "United States", 29.9902, -95.3368),
    Airport("PHX", "United States", 33.4352, -112.0101),
    Airport("LAS", "United States", 36.0840, -115.1537),
    Airport("MCO", "United States", 28.4312, -81.3081),
    Airport("EWR", "United States", 40.6895, -74.1745),
    Airport("MSP", "United States", 44.8848, -93.2223),
    Airport("DTW", "United States", 42.2124, -83.3534),
    Airport("PHL", "United States", 39.8744, -75.2424),
    Airport("SLC", "United States", 40.7899, -111.9791),
    Airport("IAD", "United States", 38.9531, -77.4565),
]

# Major Indian airports spanning north/south/east/west/northeast so simulated
# routes cover the whole subcontinent, not just the metro triangle. Real IATA
# coordinates (not approximated):
AIRPORTS_INDIA: list[Airport] = [
    Airport("DEL", "India", 28.5665, 77.1031),  # Indira Gandhi Intl, Delhi
    Airport("BOM", "India", 19.0896, 72.8656),  # Chhatrapati Shivaji Maharaj Intl, Mumbai
    Airport("BLR", "India", 13.1986, 77.7066),  # Kempegowda Intl, Bangalore
    Airport("MAA", "India", 12.9941, 80.1709),  # Chennai Intl
    Airport("HYD", "India", 17.2403, 78.4294),  # Rajiv Gandhi Intl, Hyderabad
    Airport("CCU", "India", 22.6520, 88.4463),  # Netaji Subhas Chandra Bose Intl, Kolkata
    Airport("AMD", "India", 23.0772, 72.6347),  # Sardar Vallabhbhai Patel Intl, Ahmedabad
    Airport("COK", "India", 10.1520, 76.4019),  # Cochin Intl, Kochi
    Airport("PNQ", "India", 18.5793, 73.9089),  # Pune Airport
    Airport("GOI", "India", 15.3808, 73.8314),  # Goa (Dabolim) Airport
    Airport("JAI", "India", 26.8242, 75.8122),  # Jaipur Intl
    Airport("LKO", "India", 26.7606, 80.8893),  # Chaudhary Charan Singh Intl, Lucknow
    Airport("TRV", "India", 8.4821, 76.9200),  # Trivandrum Intl
    Airport("GAU", "India", 26.1061, 91.5859),  # Lokpriya Gopinath Bordoloi Intl, Guwahati
    Airport("NAG", "India", 21.0922, 79.0472),  # Dr. Babasaheb Ambedkar Intl, Nagpur
    Airport("BBI", "India", 20.2444, 85.8178),  # Biju Patnaik Intl, Bhubaneswar
    Airport("IXC", "India", 30.6735, 76.7885),  # Chandigarh Airport
    Airport("VNS", "India", 25.4524, 82.8593),  # Lal Bahadur Shastri Airport, Varanasi
    Airport("PAT", "India", 25.5913, 85.0880),  # Jay Prakash Narayan Airport, Patna
    Airport("IDR", "India", 22.7218, 75.8011),  # Devi Ahilyabai Holkar Airport, Indore
]

_REGIONS = {
    "europe": AIRPORTS_EUROPE,
    "us": AIRPORTS_US,
    "india": AIRPORTS_INDIA,
}


def get_airports(region: str) -> list[Airport]:
    """Return the airport pool for `region` (europe/us/india/all)."""
    region = region.lower()
    if region == "all":
        return AIRPORTS_EUROPE + AIRPORTS_US + AIRPORTS_INDIA
    try:
        return _REGIONS[region]
    except KeyError:
        raise ValueError(
            f"Unknown REGION {region!r}, expected one of: europe, us, india, all"
        ) from None


# Backwards-compatible default (Europe was the original hardcoded pool).
AIRPORTS: list[Airport] = AIRPORTS_EUROPE
