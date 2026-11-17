"""Future and running plans close short internal pauses without new energy."""

from datetime import datetime
from random import Random
from zoneinfo import ZoneInfo

import pytest

from custom_components.adaptive_charge_preview.core.economics import economic_plan
from custom_components.adaptive_charge_preview.core.models import Tariff
from custom_components.adaptive_charge_preview.core.planner import plan

START = datetime(2026, 11, 17, 3, tzinfo=ZoneInfo("Europe/Amsterdam")).timestamp()
PLANNERS = [plan, economic_plan]


def rows(prices, *, solar=0):
    return [Tariff(START+i*900, START+(i+1)*900, price, .05, solar) for i, price in enumerate(prices)]


def balance(result):
    return result.expected_solar_kwh+sum(w.additional_kwh for w in result.windows)+result.omitted_kwh+result.shortfall_kwh


@pytest.mark.parametrize("planner", PLANNERS)
def test_observed_109_second_pause_is_filled_and_last_tail_shortened(planner):
    tariffs = rows([.9, .9, .1, .1, .1, .1, .1, .2, .1, .1, .9, .9])
    needed = 21.6662585327
    result = planner(START, START+10800, needed, 0, tariffs, power=11, reserve_min=0)
    assert len(result.windows) == 1
    window = result.windows[0]
    assert window.start == START+1800
    assert window.end == pytest.approx(START+1800+needed/11*3600)
    assert window.end < START+9000  # The old last end was 05:30.
    assert window.kwh == pytest.approx(needed)
    assert balance(result) == pytest.approx(needed)
    assert "short_pause_retained" not in result.warnings


@pytest.mark.parametrize("planner", PLANNERS)
@pytest.mark.parametrize("minimum", [1, 3, 5])
@pytest.mark.parametrize("delta", [-.1, 0, .1])
@pytest.mark.parametrize("price", [-.1, 0, .2])
def test_gap_boundary_and_nonpositive_prices(planner, minimum, delta, price):
    gap = minimum*60+delta
    tariffs = rows([price, price+.1, price, price+1, price+1])
    needed = 11*(2700-gap)/3600
    result = planner(START, START+4500, needed, 0, tariffs, power=11, reserve_min=0, minimum_min=minimum)
    assert balance(result) == pytest.approx(needed)
    assert len(result.windows) == (1 if delta < 0 else 2)
    assert sum(w.end-w.start for w in result.windows) == pytest.approx(needed/11*3600)
    if delta < 0:
        assert result.windows[-1].end == pytest.approx(START+2700-gap)


@pytest.mark.parametrize("planner", PLANNERS)
def test_future_first_start_is_not_pulled_forward(planner):
    result = planner(START+780, START+3600, 1, 0, rows([.5, .1, .9, .9]), reserve_min=0)
    assert result.mode == "Green" and result.windows[0].start == START+900
    assert balance(result) == pytest.approx(1)


@pytest.mark.parametrize("planner", PLANNERS)
def test_multiple_short_gaps_do_not_create_a_new_short_pause(planner):
    tariffs = [Tariff(START, START+900, .1), Tariff(START+900, START+1000, .4),
               Tariff(START+1000, START+1800, .1), Tariff(START+1800, START+1900, .4),
               Tariff(START+1900, START+4500, .1), Tariff(START+4500, START+7200, .9)]
    needed = 11*4000/3600
    result = planner(START, START+7200, needed, 0, tariffs, reserve_min=0)
    assert len(result.windows) == 1
    assert result.windows[0].end == pytest.approx(START+4000)
    assert balance(result) == pytest.approx(needed)
    assert "short_pause_retained" not in result.warnings


@pytest.mark.parametrize("planner", PLANNERS)
@pytest.mark.parametrize("seed", range(20))
def test_replanning_credits_delivered_energy_without_growing_allowance(planner, seed):
    rng = Random(seed)
    tariffs = rows([.9, .9, .1, .1, .1, .1, .1, .2, .1, .1, .9, .9])
    allowance = 21.6662585327
    start = START+1800
    elapsed = rng.uniform(1, 6000)
    delivered = 11.1*elapsed/3600
    now = start+elapsed
    result = planner(now, START+10800, allowance-delivered, 0, tariffs, reserve_min=0, continue_current=True)
    assert balance(result)+delivered == pytest.approx(allowance)
    assert all(b.start-a.end >= 180-.01 for a, b in zip(result.windows, result.windows[1:]))
    assert result.windows[0].start == pytest.approx(now)


@pytest.mark.parametrize("planner", PLANNERS)
def test_missing_price_span_is_never_filled_as_a_known_price(planner):
    tariffs = rows([.1, .1, .2, .1, .1, .9])
    result = planner(START, START+5400, 11, 0, tariffs[:2]+tariffs[3:], reserve_min=0)
    if planner is plan:
        assert len(result.windows) == 1 and result.windows[0].end == START+5400
        assert result.windows[0].cost is None
    else:
        assert not any(w.start < START+2700 and w.end > START+1800 for w in result.windows)
    assert balance(result) == pytest.approx(11)


@pytest.mark.parametrize("export_known", [False, True])
def test_early_import_ceiling_forbids_filling_an_expensive_short_gap(export_known):
    tariffs = [Tariff(START, START+900, .1), Tariff(START+900, START+1000, .9),
               Tariff(START+1000, START+1900, .1), Tariff(START+1900, START+86400, .5)]
    # Import at .9 is forbidden beyond the known horizon, including a 100s gap.
    result = economic_plan(START, START+72*3600, 5.5, 0, tariffs, reserve_min=0, export_known=export_known)
    assert result.early_threshold_active
    assert not any(w.start < START+1000 and w.end > START+900 for w in result.windows)
    assert balance(result) == pytest.approx(5.5)


@pytest.mark.parametrize("planner", PLANNERS)
@pytest.mark.parametrize("solar", [.5, 2])
def test_solar_baseline_is_not_spent_twice_when_bridging(planner, solar):
    tariffs = rows([.1, .2, .1, .9, .9, .9], solar=solar)
    needed = solar*5400/3600+(11-solar)*2500/3600
    result = planner(START, START+5400, needed, 0, tariffs, reserve_min=0)
    assert balance(result) == pytest.approx(needed)
    assert all(w.kwh <= 11*(w.end-w.start)/3600+1e-8 for w in result.windows)


@pytest.mark.parametrize("planner", PLANNERS)
@pytest.mark.parametrize("seed", range(30))
def test_mixed_price_and_solar_intervals_conserve_energy(planner, seed):
    rng = Random(seed)
    tariffs = []
    start = START
    for _ in range(24):
        end = start+rng.choice([50, 100, 300, 900])
        tariffs.append(Tariff(start, end, rng.uniform(-.1, .6), .05, rng.uniform(0, 5)))
        start = end
    needed = rng.uniform(1, 8)
    result = planner(START, start, needed, 0, tariffs, reserve_min=0)
    assert balance(result) == pytest.approx(needed, abs=1e-6)
    assert all(a.end <= b.start+.001 for a, b in zip(result.windows, result.windows[1:]))
    assert all(START <= w.start < w.end <= start+.001 for w in result.windows)
    if any(.001 < b.start-a.end < 180 for a, b in zip(result.windows, result.windows[1:])):
        assert "short_pause_retained" in result.warnings


@pytest.mark.parametrize("planner", PLANNERS)
def test_new_short_gap_after_fragment_relocation_is_also_closed(planner):
    tariffs = [Tariff(START, START+600, .1), Tariff(START+600, START+800, .4),
               Tariff(START+800, START+1400, .1), Tariff(START+1400, START+3600, .9),
               Tariff(START+3600, START+3700, .1), Tariff(START+3700, START+7200, .9)]
    result = planner(START, START+7200, 11*1300/3600, 0, tariffs, reserve_min=0)
    assert len(result.windows) == 1
    assert result.windows[0].end == pytest.approx(START+1300)
    assert balance(result) == pytest.approx(11*1300/3600)


@pytest.mark.parametrize("planner", PLANNERS)
def test_debit_comes_from_last_tail_even_when_an_earlier_donor_is_dearer(planner):
    tariffs = [Tariff(START, START+900, .1), Tariff(START+900, START+1000, .4),
               Tariff(START+1000, START+1900, .3), Tariff(START+1900, START+3600, .9),
               Tariff(START+3600, START+4500, .2), Tariff(START+4500, START+7200, .9)]
    result = planner(START, START+7200, 8.25, 0, tariffs, reserve_min=0)
    assert len(result.windows) == 2
    assert result.windows[0].start == START and result.windows[0].end == START+1900
    assert result.windows[1].end == START+4400
    assert balance(result) == pytest.approx(8.25)
