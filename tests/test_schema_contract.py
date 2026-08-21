"""Schema-freeze contract test.

Asserts that simulator output and real, captured OpenSky responses validate
against the *same* `FlightState` model and agree on field names, nullability,
and Python types once validated. This is the guardrail that stops the
simulator's shape from silently drifting away from the real API contract —
if this test fails, the simulator (or `FlightState`, or `OpenSkyStateVector`)
has diverged from live OpenSky data and needs to be reconciled before
anything downstream (Spark schemas, dbt sources, API models) is trusted.

Two independent real captures are checked (different bbox, different time)
so a field that merely *happened* to be non-null in one snapshot doesn't get
mistaken for a guaranteed-non-null contract:
  - opensky_real_sample.json    — Europe bbox, captured 2026-07-28
  - opensky_real_sample_us.json — continental US bbox, captured 2026-07-28
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.schemas.adsb_lol_raw import AdsbLolAircraft
from ingestion.schemas.flight_state import FlightState
from ingestion.schemas.opensky_raw import OpenSkyStateVector
from ingestion.simulator import FlightSimulator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PATHS = [
    FIXTURES_DIR / "opensky_real_sample.json",
    FIXTURES_DIR / "opensky_real_sample_us.json",
]

# Fields stamped by our own pipeline, not present in the raw OpenSky payload —
# excluded from the real-vs-simulated type/nullability comparison.
INGEST_METADATA_FIELDS = {"ingest_ts", "source"}


def _load_real_flight_states(fixture_path: Path) -> list[FlightState]:
    payload = json.loads(fixture_path.read_text())
    states = []
    for row in payload["states"]:
        vector = OpenSkyStateVector.from_row(row)
        states.append(vector.to_flight_state(source="opensky"))
    return states


def _generate_simulated_flight_states(ticks: int = 20) -> list[FlightState]:
    sim = FlightSimulator(aircraft_count=100, anomaly_rate=0.05, seed=123)
    states: list[FlightState] = []
    for _ in range(ticks):
        states.extend(sim.tick_flight_states())
    return states


@pytest.fixture(scope="module", params=FIXTURE_PATHS, ids=lambda p: p.stem)
def real_states(request) -> list[FlightState]:
    fixture_path: Path = request.param
    assert fixture_path.exists(), (
        f"Missing real-data fixture at {fixture_path}. Capture one with a real "
        "(or anonymous) call to https://opensky-network.org/api/states/all and "
        "save the raw JSON there before running this test."
    )
    states = _load_real_flight_states(fixture_path)
    assert len(states) > 100, f"{fixture_path} has suspiciously few states; re-capture it."
    return states


@pytest.fixture(scope="module")
def simulated_states() -> list[FlightState]:
    states = _generate_simulated_flight_states()
    assert len(states) > 100
    return states


def test_fixture_has_expected_shape():
    for fixture_path in FIXTURE_PATHS:
        payload = json.loads(fixture_path.read_text())
        assert set(payload.keys()) == {"time", "states"}
        assert len(payload["states"][0]) == 17, (
            f"{fixture_path}: OpenSky /states/all row must have 17 positional fields"
        )


def test_at_least_one_fixture_has_on_ground_aircraft():
    on_ground_counts = {}
    for fixture_path in FIXTURE_PATHS:
        payload = json.loads(fixture_path.read_text())
        on_ground_counts[fixture_path.name] = sum(1 for row in payload["states"] if row[8])
    assert any(count > 0 for count in on_ground_counts.values()), (
        f"No fixture contains any on_ground=true records: {on_ground_counts}. "
        "Capture a fixture over an airport-dense bbox to exercise ground state."
    )


def test_non_optional_fields_are_never_null_in_any_real_fixture():
    """If a field FlightState marks as required ever comes back null from a
    real capture, that's a live contract violation — the model must be
    widened to Optional, not the fixture discarded.
    """
    non_optional_fields = [
        name
        for name, info in FlightState.model_fields.items()
        if name not in INGEST_METADATA_FIELDS
        and info.default is not None
        and "None" not in str(info.annotation)
    ]
    for fixture_path in FIXTURE_PATHS:
        states = _load_real_flight_states(fixture_path)
        for field_name in non_optional_fields:
            nulls = sum(1 for s in states if getattr(s, field_name) is None)
            assert nulls == 0, (
                f"{fixture_path.name}: field '{field_name}' is declared non-optional on "
                f"FlightState but had {nulls} null value(s) in this real capture — widen "
                "the model to Optional for this field."
            )


def test_real_and_simulated_share_identical_field_names(real_states, simulated_states):
    real_keys = set(real_states[0].model_dump().keys())
    sim_keys = set(simulated_states[0].model_dump().keys())
    assert real_keys == sim_keys == set(FlightState.model_fields.keys()), (
        "Simulator and real-data records must produce the exact same set of "
        "fields as FlightState — one path is emitting a divergent shape."
    )


@pytest.mark.parametrize(
    "field_name",
    [f for f in FlightState.model_fields if f not in INGEST_METADATA_FIELDS],
)
def test_field_types_and_nullability_match(field_name, real_states, simulated_states):
    """For every shared field, real and simulated data must use the same
    Python type(s) once validated, and agree on whether None is possible.
    """
    real_values = [getattr(s, field_name) for s in real_states]
    sim_values = [getattr(s, field_name) for s in simulated_states]

    real_non_null_types = {type(v) for v in real_values if v is not None}
    sim_non_null_types = {type(v) for v in sim_values if v is not None}

    field_info = FlightState.model_fields[field_name]
    is_optional = "None" in str(field_info.annotation) or field_info.annotation is type(None)
    is_optional = is_optional or field_info.default is None

    if not is_optional:
        assert all(v is not None for v in real_values), (
            f"{field_name} is declared non-optional but real fixture has nulls"
        )

    # A simulated value's type must be one the real data (or the schema's
    # optionality) would also permit. Since both paths validate through the
    # same FlightState model, divergence here means the *simulator's own
    # value construction* is producing a type the model happens to still
    # accept (e.g. via coercion) but that real OpenSky data never exhibits —
    # a strong signal the simulator has drifted from the live contract.
    if sim_non_null_types and real_non_null_types:
        assert sim_non_null_types.issubset(real_non_null_types), (
            f"{field_name}: simulator produced types {sim_non_null_types} not seen "
            f"in real data (real types were {real_non_null_types})"
        )

    if any(v is None for v in sim_values):
        assert is_optional, (
            f"{field_name}: simulator produced None but FlightState marks it non-optional"
        )


def test_real_data_round_trips_through_flight_state_without_modification(real_states):
    """Sanity check that OpenSkyStateVector -> FlightState is lossless for
    every field FlightState declares (aside from stamped ingest metadata).
    """
    sample = real_states[0]
    dumped = sample.model_dump()
    for field in INGEST_METADATA_FIELDS:
        dumped.pop(field)
    # Re-validating the dumped data must succeed unchanged.
    reconstructed = FlightState(source="opensky", **dumped)
    assert reconstructed.model_dump(exclude=INGEST_METADATA_FIELDS) == dumped


@pytest.mark.parametrize(
    ("raw_callsign", "expected"),
    [
        ("DLH9LF  ", "DLH9LF"),  # OpenSky right-pads callsigns with spaces
        ("   ", None),  # whitespace-only must normalise to None, not ""
        ("", None),  # empty string must normalise to None, not ""
        (None, None),  # already-null passes through unchanged
        ("BAW123", "BAW123"),  # unpadded callsign is untouched
    ],
)
def test_callsign_normalisation(raw_callsign, expected):
    """OpenSky pads callsigns to 8 chars with trailing spaces and sometimes
    returns "" or null for aircraft without a filed callsign. FlightState
    must normalise all of these to a clean, trimmed string or None — never
    an empty/whitespace string.
    """
    row = [
        "abc123",
        raw_callsign,
        "Testland",
        1,
        1,
        10.0,
        50.0,
        1000.0,
        False,
        200.0,
        90.0,
        0.0,
        None,
        1000.0,
        None,
        False,
        0,
    ]
    vector = OpenSkyStateVector.from_row(row)
    state = vector.to_flight_state(source="opensky")
    assert state.callsign == expected
    if state.callsign is not None:
        assert state.callsign == state.callsign.strip()
        assert state.callsign != ""


# --- Source-adapter parity: every registered adapter must produce valid FlightState ---
#
# Unlike the OpenSky-specific tests above (which assert against OpenSky's
# exact 17-column positional contract), this section is adapter-agnostic: it
# proves each source's *mapping* to FlightState works end to end against a
# real captured fixture, without assuming any adapter shares OpenSky's wire
# format. An adapter that can't produce a valid FlightState from its own real
# fixture fails here loudly, not silently downstream.


def _adsb_lol_states_from_fixture() -> list[FlightState]:
    payload = json.loads((FIXTURES_DIR / "adsb_lol_real_sample.json").read_text())
    now = payload["now"] / 1000
    return [AdsbLolAircraft(**row).to_flight_state(now=now) for row in payload["ac"]]


SOURCE_ADAPTER_FIXTURES: dict[str, object] = {
    "opensky": lambda: _load_real_flight_states(FIXTURES_DIR / "opensky_real_sample.json"),
    "adsb_lol": _adsb_lol_states_from_fixture,
    "simulate": lambda: _generate_simulated_flight_states(ticks=5),
}


@pytest.mark.parametrize("adapter_name", list(SOURCE_ADAPTER_FIXTURES))
def test_every_source_adapter_produces_valid_flight_states(adapter_name):
    states = SOURCE_ADAPTER_FIXTURES[adapter_name]()
    assert len(states) > 0, f"{adapter_name} adapter produced zero states from its fixture"
    for state in states:
        assert isinstance(state, FlightState)
        assert state.source == adapter_name
