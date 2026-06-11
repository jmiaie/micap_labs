from rallycap.config import BotConfig
from rallycap.signals import evaluate_entry, evaluate_exit, screen_side
from rallycap.types import (
    BookTop,
    ExitReason,
    FairValue,
    GameState,
    Half,
    Market,
    Position,
    Side,
)

NOW = 1_000_000.0

MARKET = Market(condition_id="c1", game_pk=1, home_token_id="H", away_token_id="A",
                home_team="HOME", away_team="AWAY")


def cfg(**kw) -> BotConfig:
    return BotConfig(**kw)


def state(**kw) -> GameState:
    base = dict(
        game_pk=1, home_team="HOME", away_team="AWAY",
        inning=2, half=Half.TOP, outs=1,
        on_first=False, on_second=False, on_third=False,
        home_score=0, away_score=3, is_final=False,
        fetched_at=NOW, last_score_change_at=NOW - 300.0,  # calm: well past freeze
    )
    base.update(kw)
    return GameState(**base)


def book(bid=0.17, ask=0.19, bid_size=500.0, ask_size=500.0, token="H") -> BookTop:
    return BookTop(token_id=token, bid=bid, bid_size=bid_size,
                   ask=ask, ask_size=ask_size, fetched_at=NOW)


def fair(prob=0.26, side=Side.HOME, fresh=True) -> FairValue:
    return FairValue(side=side, prob=prob, model_prob=prob, sharp_prob=None,
                     fresh=fresh, computed_at=NOW)


def position(entry=0.19, size=100.0, edge=0.07) -> Position:
    return Position(market=MARKET, side=Side.HOME, entry_price=entry,
                    size_shares=size, entry_edge=edge, opened_at=NOW - 60)


# --------------------------------------------------------------------- entry

def test_entry_crosses_with_big_edge():
    sig = evaluate_entry(MARKET, state(), book(), fair(prob=0.27), Side.HOME,
                         size_shares=100.0, cfg=cfg(), now=NOW)
    assert sig is not None
    assert sig.cross  # edge 0.08 >= entry_edge + cross_margin (0.07)
    assert sig.limit_price == 0.19
    assert sig.edge >= 0.05


def test_entry_passive_with_moderate_edge():
    # edge at ask = 0.25 - 0.19 = 0.06: above entry threshold, below crossing
    sig = evaluate_entry(MARKET, state(), book(), fair(prob=0.25), Side.HOME,
                         size_shares=100.0, cfg=cfg(), now=NOW)
    assert sig is not None
    assert not sig.cross
    assert sig.limit_price < 0.19


def test_entry_rejected_without_edge():
    assert evaluate_entry(MARKET, state(), book(), fair(prob=0.21), Side.HOME,
                          size_shares=100.0, cfg=cfg(), now=NOW) is None


def test_entry_rejected_when_frozen():
    assert evaluate_entry(MARKET, state(), book(), fair(prob=0.27, fresh=False),
                          Side.HOME, size_shares=100.0, cfg=cfg(), now=NOW) is None


def test_entry_rejected_late_inning():
    s = state(inning=6)
    assert evaluate_entry(MARKET, s, book(), fair(prob=0.27), Side.HOME,
                          size_shares=100.0, cfg=cfg(), now=NOW) is None


def test_entry_rejected_outside_price_band():
    # ask 0.45 is no longer a 70-85% favorite situation on either side
    b = book(bid=0.43, ask=0.45)
    assert evaluate_entry(MARKET, state(), b, fair(prob=0.52), Side.HOME,
                          size_shares=100.0, cfg=cfg(), now=NOW) is None


def test_entry_rejected_wide_spread():
    b = book(bid=0.12, ask=0.19)
    assert evaluate_entry(MARKET, state(), b, fair(prob=0.27), Side.HOME,
                          size_shares=100.0, cfg=cfg(), now=NOW) is None


def test_entry_rejected_zero_size():
    assert evaluate_entry(MARKET, state(), book(), fair(prob=0.27), Side.HOME,
                          size_shares=0.0, cfg=cfg(), now=NOW) is None


def test_favorite_side_allowed_only_by_flag():
    # Favorite ask 0.74 (complement 0.26 is in the band), fair says 0.82.
    b = book(bid=0.72, ask=0.74)
    f = fair(prob=0.82, side=Side.AWAY)
    s = state()
    assert evaluate_entry(MARKET, s, b, f, Side.AWAY, 100.0, cfg(), NOW) is not None
    off = cfg(allow_favorite_side=False)
    assert evaluate_entry(MARKET, s, b, f, Side.AWAY, 100.0, off, NOW) is None


def test_screen_requires_depth():
    b = book(ask_size=100.0)  # 100 * 0.19 = $19 < $50 min depth
    assert not screen_side(state(), b, Side.HOME, cfg())


# ---------------------------------------------------------------------- exit

def test_take_profit_fires_at_50pct():
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.29, ask=0.31),
                        fair(prob=0.40), cfg())
    assert sig is not None and sig.reason is ExitReason.TAKE_PROFIT


def test_take_profit_respects_disable_flag():
    c = cfg(take_profit_enabled=False)
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.29, ask=0.31),
                        fair(prob=0.40), c)
    assert sig is None or sig.reason is not ExitReason.TAKE_PROFIT


def test_salvage_when_edge_flips_negative():
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.20, ask=0.22),
                        fair(prob=0.14), cfg())
    assert sig is not None and sig.reason is ExitReason.SALVAGE


def test_convergence_exit():
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.24, ask=0.26),
                        fair(prob=0.245), cfg())
    assert sig is not None and sig.reason is ExitReason.EDGE_CONVERGED


def test_time_stop_past_inning_seven():
    # bid is below fair - exit_edge, so convergence does not fire first
    sig = evaluate_exit(position(entry=0.19), state(inning=8),
                        book(bid=0.20, ask=0.22), fair(prob=0.23), cfg())
    assert sig is not None and sig.reason is ExitReason.TIME_STOP


def test_no_model_exits_on_stale_fair_but_tp_still_works():
    stale = fair(prob=0.14, fresh=False)  # would salvage if fresh
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.20, ask=0.22),
                        stale, cfg())
    assert sig is None
    tp = evaluate_exit(position(entry=0.19), state(), book(bid=0.29, ask=0.31),
                       stale, cfg())
    assert tp is not None and tp.reason is ExitReason.TAKE_PROFIT


def test_hold_when_nothing_triggers():
    sig = evaluate_exit(position(entry=0.19), state(), book(bid=0.21, ask=0.23),
                        fair(prob=0.27), cfg())
    assert sig is None
