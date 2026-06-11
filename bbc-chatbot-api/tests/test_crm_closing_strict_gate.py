"""Pinning tests — premature-close incident, 10 Jun 2026.

A conversation must NEVER be closed (nor its reply replaced with the
closing template) before collection is COMPLETE. The submit gate is
intentionally looser; the closing gate must be strict. If you are
editing these tests to make them pass, read the commit message for
hotfix/crm-closing-strict-gate first and write an ADR.
"""
from app.models.lead import get_missing_fields


CONTACT_FULL = {
    "visitor_name": "Jeff Wansor",
    "visitor_email": "jeff@example.com",
    "visitor_phone": "+19512321307",
}


def _lead_route_only() -> dict:
    # The exact incident state: route + contact, NO dates, NO pax.
    return {"origin_code": "SMF", "destination_code": "ROA"}


def _lead_complete() -> dict:
    return {
        "origin_code": "SMF",
        "destination_code": "ROA",
        "departure_date": "2026-07-15",
        "trip_type": "one_way",
        "passengers": 2,
    }


def test_submit_gate_passes_on_route_plus_contact():
    """Early submit is INTENDED — for_crm=True needs route+contact only."""
    missing = get_missing_fields(_lead_route_only(), CONTACT_FULL, for_crm=True)
    assert missing == []


def test_strict_gate_blocks_closing_without_dates_and_pax():
    """THE incident: strict gate must report missing fields → no closing."""
    missing = get_missing_fields(_lead_route_only(), CONTACT_FULL)
    joined = " ".join(missing)
    assert missing != []
    assert "date" in joined
    assert "traveler" in joined or "passenger" in joined


def test_strict_gate_passes_only_when_complete():
    assert get_missing_fields(_lead_complete(), CONTACT_FULL) == []


def test_orchestrator_closing_gates_are_strict():
    """Source-level pin: the CLOSING sections (Step 7.5 + 8.1) must not
    use for_crm=True. Step 6.2 (submit) may keep it — that is the SUBMIT
    gate and is intentionally loose. If this test fails, for_crm=True
    was silently reintroduced in the closing path (happened once in the
    multi-site edit)."""
    import pathlib
    src = pathlib.Path("app/pipeline/orchestrator.py").read_text()
    # Split at the Step 7.5 marker — everything AFTER that is closing logic.
    marker = "STEP 7.5"
    assert marker in src, f"Step 7.5 marker missing — test needs update"
    closing_section = src.split(marker, 1)[1]
    assert "for_crm=True" not in closing_section, (
        "Closing gates (Step 7.5 + 8.1) must be STRICT. "
        "for_crm=True belongs ONLY in the submit gate (Step 6.2 / crm.py)."
    )
