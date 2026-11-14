"""Exact provider labels observed in the authenticated preparation response."""

from types import SimpleNamespace as NS

import pytest

from quantum.openquantum_bridge import choose_free_quote
from quantum.tests.test_openquantum_bridge import prep_quote


def test_provider_public_plan_standard_queue_are_accepted():
    quote = prep_quote()
    quote.quote[0].name = "Private Plan"
    quote.quote[1].name = "Public Plan"
    quote.quote[1].queue_priorities[0].name = "Priority Queue"
    quote.quote[1].queue_priorities[1].name = "Standard Queue"
    result = choose_free_quote(
        quote,
        NS(spark_credits=25, full_credits=0),
        max_credits=2,
        auto_refill_disabled=True,
    )
    assert result == {
        "execution_plan_id": "public",
        "queue_priority_id": "standard",
        "credits": 1.0,
    }


@pytest.mark.parametrize(
    "label", ["Private Public Plan", "Public Plan Premium", "Publicity"]
)
def test_unknown_plan_names_are_not_matched_by_substring(label):
    quote = prep_quote()
    quote.quote[1].name = label
    with pytest.raises(PermissionError):
        choose_free_quote(
            quote,
            NS(spark_credits=25, full_credits=0),
            max_credits=2,
            auto_refill_disabled=True,
        )
