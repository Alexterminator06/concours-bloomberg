import numpy as np
import pandas as pd
import pytest

from rsi_strategy import StrategyConfig, compute_signals, oversold_signal, rsi, run_backtest
from rsi_strategy.metrics import performance_stats, relative_stats

SIGNAL_DAY, ENTRY_DAY = 15, 16  # premier jour où RSI(14) < 30 deux jours de suite sur une baisse continue


def make_panel(closes: dict[str, list[float]]) -> dict[str, pd.DataFrame]:
    n = len(next(iter(closes.values())))
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.DataFrame(closes, index=idx, dtype=float)
    return {
        "Open": close.copy(),
        "High": close * 1.01,
        "Low": close * 0.99,
        "Close": close,
        "Volume": pd.DataFrame(1e6, index=idx, columns=close.columns),
    }


def declining(n: int = 20, step: float = 0.01) -> list[float]:
    return [100 * (1 - step) ** k for k in range(n)]


def set_bar(panel, day, ticker, o, h, l, c):
    for field, v in zip(["Open", "High", "Low", "Close"], [o, h, l, c]):
        panel[field].iloc[day, panel[field].columns.get_loc(ticker)] = v


def cfg(**kw) -> StrategyConfig:
    # Les tests de mécanique d'exécution utilisent la règle d'origine (RSI(14) < 30 deux jours,
    # sans filtre de tendance), qui se déclenche sur une simple baisse continue.
    base = dict(commission_bps=0.0, slippage_bps=0.0, min_avg_dollar_volume=0.0,
                avg_volume_window=1, rsi_period=14, rsi_threshold=30.0, consecutive_days=2,
                trend_ma_days=0, take_profit_bull_pct=0.0, core_size=0)
    base.update(kw)
    return StrategyConfig(**base)


# --- Indicateurs -------------------------------------------------------------------------------

def naive_wilder_rsi(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    deltas = np.diff(values)
    avg_gain = avg_loss = None
    for i, d in enumerate(deltas, start=1):
        g, l = max(d, 0.0), max(-d, 0.0)
        if avg_gain is None:
            avg_gain, avg_loss = g, l
        else:
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
        if i >= period:
            out[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def test_rsi_matches_wilder_recursion():
    rng = np.random.default_rng(0)
    prices = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    ours = rsi(pd.Series(prices), 14).to_numpy()
    np.testing.assert_allclose(ours, naive_wilder_rsi(prices, 14), equal_nan=True)


def test_rsi_extremes():
    assert rsi(pd.Series(declining(30)), 14).iloc[-1] == 0.0
    assert rsi(pd.Series(declining(30)[::-1]), 14).iloc[-1] == 100.0
    assert rsi(pd.Series([10.0] * 30), 14).iloc[-1] == 50.0


def test_signal_needs_consecutive_days():
    values = pd.DataFrame({"x": [40, 25, 40, 25, 25, 20, np.nan, 25]})
    sig = oversold_signal(values, 30, 2)["x"].tolist()
    assert sig == [False, False, False, False, True, True, False, False]


# --- Backtest ----------------------------------------------------------------------------------

def test_entry_next_open_and_take_profit():
    panel = make_panel({"A": declining()})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.06, x * 0.99, x * 1.02)
    res = run_backtest(panel, cfg())
    t = res.trades.iloc[0]
    dates = panel["Close"].index
    assert t.signal_date == dates[SIGNAL_DAY]
    assert t.entry_date == dates[ENTRY_DAY]
    assert t.entry_price == pytest.approx(x)
    assert t.exit_reason == "take_profit"
    assert t.exit_price == pytest.approx(x * 1.05)
    assert t.shares == int(1_000_000 * 0.20 // x)
    assert t.pnl == pytest.approx(t.shares * x * 0.05)


def test_stop_loss_intraday():
    panel = make_panel({"A": declining()})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.01, x * 0.94, x * 0.96)
    t = run_backtest(panel, cfg()).trades.iloc[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == pytest.approx(x * 0.95)
    assert t.return_pct == pytest.approx(-0.05)


def test_gap_down_exits_at_open():
    panel = make_panel({"A": declining(22)})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.01, x * 0.99, x)
    set_bar(panel, ENTRY_DAY + 1, "A", x * 0.90, x * 0.92, x * 0.88, x * 0.90)
    t = run_backtest(panel, cfg()).trades.iloc[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == pytest.approx(x * 0.90)
    assert t.holding_days == 1


def test_gap_up_exits_at_open():
    panel = make_panel({"A": declining(22)})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.01, x * 0.99, x)
    set_bar(panel, ENTRY_DAY + 1, "A", x * 1.10, x * 1.12, x * 1.08, x * 1.10)
    t = run_backtest(panel, cfg()).trades.iloc[0]
    assert t.exit_reason == "take_profit"
    assert t.exit_price == pytest.approx(x * 1.10)


@pytest.mark.parametrize("stop_first, reason", [(True, "stop_loss"), (False, "take_profit")])
def test_stop_and_target_same_day(stop_first, reason):
    panel = make_panel({"A": declining()})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.06, x * 0.94, x)
    t = run_backtest(panel, cfg(stop_first_if_ambiguous=stop_first)).trades.iloc[0]
    assert t.exit_reason == reason
    assert t.ambiguous


def test_costs_are_applied():
    panel = make_panel({"A": declining()})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.06, x * 0.99, x)
    t = run_backtest(panel, cfg(commission_bps=10, slippage_bps=20)).trades.iloc[0]
    entry = x * 1.002
    assert t.entry_price == pytest.approx(entry)
    assert t.exit_price == pytest.approx(entry * 1.05)  # take profit limite : pas de glissement
    gross = t.shares * entry * 0.05
    fees = t.shares * entry * 0.001 + t.shares * entry * 1.05 * 0.001
    assert t.pnl == pytest.approx(gross - fees)
    assert t.costs == pytest.approx(fees + t.shares * x * 0.002)
    assert not t.ambiguous


def test_max_positions_keeps_lowest_rsi():
    def zigzag(down, up, n=20):
        out, p = [100.0], 100.0
        for k in range(1, n):
            p *= (1 - down) if k % 2 else (1 + up)
            out.append(p)
        return out

    panel = make_panel({"A": declining(), "B": zigzag(0.02, 0.003), "C": zigzag(0.02, 0.006)})
    rsi_values, eligible = compute_signals(panel, cfg())
    day = eligible.index[eligible.all(axis=1)][0]
    expected = set(rsi_values.loc[day].nsmallest(2).index)

    res = run_backtest(panel, cfg(max_positions=2))
    first = pd.concat([res.trades, res.open_positions])
    first = first[first["signal_date"] == day]
    assert set(first["ticker"]) == expected


def test_trend_filter_keeps_pullbacks_in_uptrends_only():
    falling = declining(260)                                                # chute continue
    pullback = [100 * 1.005 ** k for k in range(250)]                       # hausse...
    pullback += [pullback[-1] * 0.92 ** k for k in range(1, 11)]            # ...puis repli brutal
    panel = make_panel({"FALL": falling, "PULL": pullback})

    _, without = compute_signals(panel, cfg())
    _, with_trend = compute_signals(panel, cfg(trend_ma_days=200))
    assert without["FALL"].any() and without["PULL"].any()
    assert not with_trend["FALL"].any()
    assert with_trend["PULL"].any()
    assert (with_trend <= without).all().all()  # le filtre ne fait que retirer des signaux


@pytest.mark.parametrize("market_trend, expect_trades", [(-0.01, False), (0.01, True)])
def test_market_filter(market_trend, expect_trades):
    panel = make_panel({"A": declining()})
    market = pd.Series([100 * (1 + market_trend) ** k for k in range(20)], index=panel["Close"].index)
    res = run_backtest(panel, cfg(market_ma_days=5), market_close=market)
    assert (len(res.trades) + len(res.open_positions) > 0) == expect_trades


def test_market_filter_requires_index_prices():
    with pytest.raises(ValueError):
        run_backtest(make_panel({"A": declining()}), cfg(market_ma_days=5))


def test_default_signal_is_rsi2_pullback_in_uptrend():
    defaults = StrategyConfig()
    assert (defaults.rsi_period, defaults.rsi_threshold, defaults.consecutive_days) == (2, 10.0, 1)
    assert (defaults.trend_ma_days, defaults.market_ma_days) == (200, 0)

    falling = declining(260)
    pullback = [100 * 1.005 ** k for k in range(250)]
    pullback += [pullback[-1] * 0.95 ** k for k in range(1, 11)]
    panel = make_panel({"FALL": falling, "PULL": pullback})
    rsi_values, eligible = compute_signals(panel, StrategyConfig(min_avg_dollar_volume=0.0))
    first = eligible.index[eligible["PULL"]][0]
    assert first == panel["Close"].index[250]  # dès la première séance de repli
    assert rsi_values.loc[first, "PULL"] < 10
    assert not eligible["FALL"].any()  # pas d'achat d'une action en chute durable


def test_liquidity_filter_blocks_illiquid_stocks():
    panel = make_panel({"A": declining()})
    panel["Volume"] *= 0.0
    res = run_backtest(panel, cfg(min_avg_dollar_volume=1e6))
    assert res.trades.empty and res.open_positions.empty


def test_portfolio_accounting_on_random_walks():
    rng = np.random.default_rng(42)
    n_days, n_tickers = 400, 40
    rets = rng.normal(-0.0002, 0.03, (n_days, n_tickers))
    close = 30 * np.exp(np.cumsum(rets, axis=0))
    panel = make_panel({f"T{k}": close[:, k] for k in range(n_tickers)})
    noise = np.abs(rng.normal(0, 0.02, close.shape))
    panel["Open"] = panel["Close"].shift(1).fillna(panel["Close"]) * (1 + rng.normal(0, 0.01, close.shape))
    panel["High"] = np.maximum(panel["Open"], panel["Close"]) * (1 + noise)
    panel["Low"] = np.minimum(panel["Open"], panel["Close"]) * (1 - noise)

    c = cfg(commission_bps=5, slippage_bps=5, max_positions=10, position_size_pct=0.1)
    res = run_backtest(panel, c, start=panel["Close"].index[50])
    eq = res.equity

    assert len(res.trades) > 20
    assert (eq["cash"] >= -1e-6).all()
    assert (eq["n_positions"] <= c.max_positions).all()
    np.testing.assert_allclose(eq["equity"], eq["cash"] + eq["invested"])
    unrealized = res.open_positions["unrealized_pnl"].sum() if not res.open_positions.empty else 0.0
    assert eq["equity"].iloc[-1] - c.initial_capital == pytest.approx(res.trades["pnl"].sum() + unrealized)
    # Jamais deux positions simultanées sur la même action, et pas de signal avant le début.
    for _, g in res.trades.sort_values("entry_date").groupby("ticker"):
        assert (g["entry_date"].iloc[1:].to_numpy() >= g["exit_date"].iloc[:-1].to_numpy()).all()
    assert (res.trades["entry_date"] > res.trades["signal_date"]).all()
    assert res.trades["signal_date"].min() >= panel["Close"].index[50]


def test_default_sizing_caps_each_position_at_20pct_of_portfolio():
    rng = np.random.default_rng(7)
    n_days, n_tickers = 300, 60
    close = 30 * np.exp(np.cumsum(rng.normal(-0.0005, 0.03, (n_days, n_tickers)), axis=0))
    panel = make_panel({f"T{k}": close[:, k] for k in range(n_tickers)})

    c = cfg(commission_bps=5, slippage_bps=5)
    assert c.max_positions is None and c.position_size_pct == 0.20
    res = run_backtest(panel, c, start=panel["Close"].index[30])
    entries = pd.concat([res.trades, res.open_positions])
    equity_at_signal = res.equity["equity"].reindex(entries["signal_date"]).to_numpy()
    entry_value = (entries["shares"] * entries["entry_price"]).to_numpy()

    assert len(entries) > 20
    # Plafond de 20 % de la valeur totale du portefeuille (frais d'entrée inclus).
    assert (entry_value * (1 + c.commission_bps / 1e4) <= 0.20 * equity_at_signal + 1e-6).all()
    # Pas de positions résiduelles : au moins 2 % du portefeuille, à une action près.
    assert (entry_value >= 0.02 * equity_at_signal - entries["entry_price"].to_numpy()).all()
    # Pas de levier : le cash ne devient jamais négatif.
    assert (res.equity["cash"] >= -1e-6).all()


def test_performance_stats():
    idx = pd.bdate_range("2024-01-01", periods=4)
    stats = performance_stats(pd.Series([100.0, 120.0, 90.0, 110.0], index=idx))
    assert stats["Rendement total"] == pytest.approx(0.10)
    assert stats["Max drawdown"] == pytest.approx(-0.25)


def test_relative_return_is_the_gap_in_total_return():
    idx = pd.bdate_range("2024-01-01", periods=3)
    strategy = pd.Series([100.0, 110.0, 130.0], index=idx)
    world = pd.Series([100.0, 105.0, 110.0], index=idx)
    assert relative_stats(strategy, world)["Rendement relatif"] == pytest.approx(0.20)


# --- Take profit adapté à la tendance ------------------------------------------------------------

@pytest.mark.parametrize("market_trend, expected_tp", [(0.01, 0.10), (-0.01, 0.05)])
def test_take_profit_follows_market_trend(market_trend, expected_tp):
    panel = make_panel({"A": declining()})
    x = panel["Close"]["A"].iloc[ENTRY_DAY]
    set_bar(panel, ENTRY_DAY, "A", x, x * 1.12, x * 0.99, x * 1.11)
    market = pd.Series([100 * (1 + market_trend) ** k for k in range(20)], index=panel["Close"].index)
    res = run_backtest(panel, cfg(take_profit_bull_pct=0.10, bull_ma_days=5), market_close=market)
    t = res.trades.iloc[0]
    assert t.take_profit_pct == expected_tp
    assert t.exit_reason == "take_profit"
    assert t.exit_price == pytest.approx(x * (1 + expected_tp))


def test_adaptive_take_profit_requires_index_prices():
    with pytest.raises(ValueError, match="tendance"):
        run_backtest(make_panel({"A": declining()}), cfg(take_profit_bull_pct=0.10))


# --- Grandes valeurs ---------------------------------------------------------------------------

def core_panel(n_days=40, **extra):
    closes = {"A": [50.0] * n_days, "BIG1": [100.0] * n_days, "BIG2": [100.0] * n_days,
              "BIG3": [100.0] * n_days}
    closes.update(extra)
    return make_panel(closes)


def test_idle_cash_goes_to_largest_stocks_within_the_size_limit():
    panel = core_panel()
    shares = pd.Series({"BIG1": 30.0, "BIG2": 20.0, "BIG3": 10.0})
    res = run_backtest(panel, cfg(core_size=2), core_shares=shares)
    assert set(res.core_holdings) == {"BIG1", "BIG2"}  # les 2 plus grosses capitalisations
    # 2 lignes plafonnées à 20 % chacune : 40 % investi, le reste en cash.
    assert res.equity["core"].iloc[-1] == pytest.approx(400_000, rel=1e-3)
    assert res.trades.empty and res.open_positions.empty  # pas de signal RSI sur les grandes valeurs


def test_largest_stocks_are_ranked_with_prices_of_the_time():
    n = 60  # janvier à mars : un classement au début de chaque mois
    rising = [100.0 * 1.05 ** k for k in range(n)]  # 1 Md$ en janvier, plus de 7 Md$ en mars
    panel = core_panel(n, RISE=rising)
    shares = pd.Series({"BIG1": 30.0, "BIG2": 20.0, "BIG3": 10.0, "RISE": 10.0})
    first_month = panel["Close"].index[panel["Close"].index.month == panel["Close"].index[0].month]
    res = run_backtest(panel, cfg(core_size=1), end=first_month[-1], core_shares=shares)
    assert set(res.core_holdings) == {"BIG1"}  # 3 Md$, contre 1 Md$ pour RISE en janvier
    res = run_backtest(panel, cfg(core_size=1), core_shares=shares)
    assert set(res.core_holdings) == {"RISE"}  # RISE est devenue la plus grosse en mars


def test_largest_stocks_are_sold_to_fund_rsi_buys():
    panel = make_panel({"A": declining(), "B1": [100.0] * 20, "B2": [100.0] * 20,
                        "B3": [100.0] * 20, "B4": [100.0] * 20, "B5": [100.0] * 20})
    shares = pd.Series({f"B{k}": float(10 - k) for k in range(1, 6)})
    res = run_backtest(panel, cfg(core_size=5), core_shares=shares)
    eq = res.equity
    assert eq["core"].iloc[ENTRY_DAY - 1] == pytest.approx(1_000_000, rel=1e-3)  # tout investi
    entry = pd.concat([res.trades, res.open_positions]).iloc[0]
    assert entry["entry_date"] == panel["Close"].index[ENTRY_DAY]
    assert entry["shares"] * entry["entry_price"] == pytest.approx(200_000, rel=0.01)
    assert (eq["cash"] >= -1e-6).all()


def test_accounting_with_largest_stocks_on_random_walks():
    rng = np.random.default_rng(3)
    n_days, n_small, n_big = 300, 30, 6
    close = 30 * np.exp(np.cumsum(rng.normal(-0.0003, 0.03, (n_days, n_small + n_big)), axis=0))
    names = [f"S{k}" for k in range(n_small)] + [f"BIG{k}" for k in range(n_big)]
    panel = make_panel(dict(zip(names, close.T)))
    noise = np.abs(rng.normal(0, 0.02, close.shape))
    panel["Open"] = panel["Close"].shift(1).fillna(panel["Close"]) * (1 + rng.normal(0, 0.01, close.shape))
    panel["High"] = np.maximum(panel["Open"], panel["Close"]) * (1 + noise)
    panel["Low"] = np.minimum(panel["Open"], panel["Close"]) * (1 - noise)
    shares = pd.Series({f"BIG{k}": float(100 + k) for k in range(n_big)})
    market = pd.Series(np.linspace(100, 130, n_days), index=panel["Close"].index)

    c = cfg(core_size=4, commission_bps=5, slippage_bps=5, take_profit_bull_pct=0.10, bull_ma_days=20)
    res = run_backtest(panel, c, start=panel["Close"].index[40], market_close=market,
                       core_shares=shares)
    eq = res.equity
    assert len(res.trades) > 10
    assert eq["core"].mean() > 0
    assert (eq["cash"] >= -1e-6).all()
    assert len(res.core_holdings) <= c.core_size
    assert not set(res.trades["ticker"]) & set(shares.index)
    unrealized = res.open_positions["unrealized_pnl"].sum() if not res.open_positions.empty else 0.0
    assert eq["equity"].iloc[-1] - c.initial_capital == pytest.approx(
        res.trades["pnl"].sum() + unrealized + res.core_pnl)


def test_reserved_share_of_largest_stocks_limits_rsi_buys():
    small = {f"S{k}": declining(40) for k in range(6)}  # signaux RSI tous les jours dès la 15e séance
    big = {f"B{k}": [100.0] * 40 for k in range(1, 6)}
    panel = make_panel({**small, **big})
    shares = pd.Series({f"B{k}": float(10 - k) for k in range(1, 6)})
    res = run_backtest(panel, cfg(core_size=5, core_min_pct=0.5), core_shares=shares)
    eq = res.equity
    rsi_share = (eq["invested"] - eq["core"]) / eq["equity"]
    assert rsi_share.max() <= 0.5 + 0.01  # à l'achat, au plus 50 % en RSI
    assert rsi_share.iloc[ENTRY_DAY] > 0.45  # et la place est bien utilisée
    assert (eq["core"] / eq["equity"]).iloc[ENTRY_DAY:].min() > 0.45
    assert (eq["cash"] >= -1e-6).all()
