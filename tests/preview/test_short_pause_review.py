"""Regressions for Green restoration and visible continuity exceptions."""

import pytest

from custom_components.adaptive_charge_preview.core.economics import economic_plan
from custom_components.adaptive_charge_preview.core.models import Tariff
from custom_components.adaptive_charge_preview.core.planner import plan
from .test_short_pauses import START, PLANNERS, balance


def test_bridged_green_restores_solar_when_relocated_again():
    data = [(50,.1,0),(200,.1,2),(200,.4,5),(50,.1,5),(50,.2,2),
            (50,.1,5),(50,.2,2),(200,.4,0),(200,.1,0),(200,.2,0),
            (900,.1,2),(900,.1,2)]
    # A public-plan regression: solar inside a bridge is later a donor.
    tariffs, start = [], START
    for duration, buy, solar in data:
        tariffs.append(Tariff(start, start+duration, buy, .02, solar))
        start += duration
    needed = 3.451954947680121
    result = economic_plan(START, start, needed, 0, tariffs, reserve_min=0, export_known=True)
    assert result.expected_solar_kwh == pytest.approx(sum(duration*solar/3600 for duration, _, solar in data))
    assert any(w.start == START+250 and w.end == START+650 for w in result.green_windows)
    assert "partial_prices_remaining_deadline_fallback" not in result.warnings
    assert balance(result) == pytest.approx(needed)
    assert result.cost is not None and all(w.cost is not None for w in result.windows)


@pytest.mark.parametrize("planner", PLANNERS)
def test_large_minimum_has_explicit_continuity_cost(planner):
    tariffs = [Tariff(START, START+3600, .1), Tariff(START+3600, START+5340, 1),
               Tariff(START+5340, START+12540, .2), Tariff(START+12540, START+16000, 2)]
    result = planner(START, START+16000, 33, 0, tariffs, reserve_min=0, minimum_min=30)
    assert len(result.windows) == 1
    assert result.cost == pytest.approx(11*.1+22*.2+11*1740/3600*.8)
    assert balance(result) == pytest.approx(33)


@pytest.mark.parametrize("planner", PLANNERS)
def test_shortened_tail_preserves_existing_nonzero_band_policy(planner):
    tariffs = [Tariff(START, START+900, .1), Tariff(START+900, START+1000, .4),
               Tariff(START+1000, START+1900, .2)]
    tariffs += [Tariff(START+i, START+i+50, .9) for i in range(1900,3000,50)]
    tariffs += [Tariff(START+3000, START+3200, .1)]
    tariffs += [Tariff(START+i, START+i+50, .9) for i in range(3200,3600,50)]
    needed = 11*2000/3600
    result = planner(START, START+3600, needed, .8, tariffs, reserve_min=0)
    assert balance(result) == pytest.approx(needed)
    assert result.shortfall_kwh == pytest.approx(0)
    if planner is plan:
        assert result.omitted_kwh == pytest.approx(11*100/3600)
        assert "small_remainder_within_tolerance" in result.warnings
    else:
        assert result.omitted_kwh == 0
        assert "short_block_required_for_significant_deficit" in result.warnings


def test_unknown_boundary_to_fallback_retains_short_pause_with_warning():
    tariffs = [Tariff(START, START+900, .1)]
    result = economic_plan(START, START+1100, 11*1000/3600, 0, tariffs, reserve_min=0)
    assert len(result.windows) == 2
    assert result.windows[-1].cost is None
    assert "short_pause_retained" in result.warnings
    assert balance(result) == pytest.approx(11*1000/3600)


@pytest.mark.parametrize("planner", PLANNERS)
def test_insufficient_tail_cannot_overdraw_and_explains_retained_running_pause(planner):
    tariffs = [Tariff(START, START+900, .4), Tariff(START+900, START+1800, .1), Tariff(START+1800, START+3600, .9)]
    result = planner(START+780, START+3600, .01, 0, tariffs, reserve_min=0, continue_current=True)
    assert result.mode == "Green" and "short_pause_retained" in result.warnings
    assert balance(result) == pytest.approx(.01)


def test_unknown_internal_price_gap_remains_visible():
    tariffs = [Tariff(START, START+900, .1), Tariff(START+1000, START+1900, .1),
               Tariff(START+1900, START+3600, .9)]
    result = economic_plan(START, START+3600, 5.5, 0, tariffs, reserve_min=0)
    assert len(result.windows) == 2 and "short_pause_retained" in result.warnings
    assert balance(result) == pytest.approx(5.5)


@pytest.mark.parametrize("export_known", [False, True])
def test_early_ceiling_retains_pause_with_warning(export_known):
    tariffs = [Tariff(START, START+900, .1), Tariff(START+900, START+1000, .9),
               Tariff(START+1000, START+1900, .1), Tariff(START+1900, START+86400, .5)]
    result = economic_plan(START, START+72*3600, 5.5, 0, tariffs, reserve_min=0, export_known=export_known)
    assert "short_pause_retained" in result.warnings
    assert not any(w.start < START+1000 and w.end > START+900 for w in result.windows)
