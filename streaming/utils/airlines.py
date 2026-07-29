"""Static ICAO callsign-prefix -> airline lookup for the gold airline_activity table.

Not exhaustive — good enough for demo-grade rollups over the default
Europe/US/India bounding boxes. Unmatched prefixes fall back to "Unknown/Other".
"""

from __future__ import annotations

AIRLINE_PREFIXES: dict[str, str] = {
    "DLH": "Lufthansa",
    "RYR": "Ryanair",
    "BAW": "British Airways",
    "AFR": "Air France",
    "KLM": "KLM",
    "EZY": "easyJet",
    "WZZ": "Wizz Air",
    "VLG": "Vueling",
    "IBE": "Iberia",
    "SWR": "Swiss",
    "AUA": "Austrian Airlines",
    "SAS": "SAS",
    "FIN": "Finnair",
    "LOT": "LOT Polish Airlines",
    "TAP": "TAP Air Portugal",
    "THY": "Turkish Airlines",
    "AAL": "American Airlines",
    "UAL": "United Airlines",
    "DAL": "Delta Air Lines",
    "SWA": "Southwest Airlines",
    "JBU": "JetBlue",
    "ASA": "Alaska Airlines",
    "FDX": "FedEx Express",
    "UPS": "UPS Airlines",
    "ACA": "Air Canada",
    "QTR": "Qatar Airways",
    "UAE": "Emirates",
    "ETD": "Etihad Airways",
    # --- India ---
    "AIC": "Air India",
    "IGO": "IndiGo",
    "SEJ": "SpiceJet",
    "AXB": "Air India Express",
    "AKJ": "Akasa Air",
    "VTI": "Vistara",
    "GOW": "Go First",
    "LLR": "Alliance Air",
}


def callsign_to_airline(callsign: str | None) -> str:
    if not callsign or len(callsign) < 3:
        return "Unknown/Other"
    prefix = callsign[:3].upper()
    return AIRLINE_PREFIXES.get(prefix, "Unknown/Other")
