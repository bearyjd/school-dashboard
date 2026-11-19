"""model.py: vocabularies and frozen dataclasses."""
import dataclasses

import pytest

from recon.support import mk_link, mk_record, mk_state
from school_dashboard.assignments import model


def test_portal_statuses_include_cleared():
    assert model.PORTAL_STATUSES == ("none", "open", "submitted", "graded", "missing", "late", "cleared")


def test_setters_are_the_six_known_actors():
    assert model.SETTERS == ("parent", "kid", "portal", "email", "system", "migration")


def test_flag_buckets_map_three_flags_into_needs_action():
    needs = {f for f, b in model.BUCKETS.items() if b == "needs_action"}
    assert needs == {"done_not_received", "disputed", "missing"}


def test_upcoming_has_no_bucket():
    assert "upcoming" not in model.BUCKETS


@pytest.mark.parametrize("build", [mk_link, mk_state, mk_record])
def test_dataclasses_are_frozen(build):
    obj = build()
    field = dataclasses.fields(obj)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, "x")


def test_link_state_defaults_are_live_and_unmissed():
    link = model.LinkState(source="schoology", external_key="k", norm_status="open")
    assert (link.live, link.trusted_misses, link.absent_since, link.via) == (True, 0, None, "scrape")


def test_source_record_via_defaults_to_scrape():
    assert mk_record().via == "scrape"
