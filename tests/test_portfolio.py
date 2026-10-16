"""Lecture du portefeuille et calcul des ordres d'achat du scan quotidien."""

import math

import pandas as pd
import pytest

from rsi_strategy import StrategyConfig
from rsi_strategy.portfolio import PRICE_BUFFER, Portfolio, load_portfolio, plan_orders


def write(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "portefeuille.csv"
    path.write_text(text, encoding=encoding)
    return path


# --- Lecture du fichier ------------------------------------------------------------------------

def test_load_comma_separated_file(tmp_path):
    pf = load_portfolio(write(tmp_path, "ticker,quantite,prix_achat\nCASH,612500,\nkfy,2800,71.02\n"))
    assert pf.cash == 612_500
    assert list(pf.positions.index) == ["KFY"]
    assert pf.positions.loc["KFY", "quantite"] == 2800
    assert pf.positions.loc["KFY", "prix_achat"] == pytest.approx(71.02)


def test_load_excel_french_file(tmp_path):
    text = "Ticker;Quantité;Prix achat\nCASH;612 500,50;\nKFY;2 800;71,02\n"
    pf = load_portfolio(write(tmp_path, text, encoding="utf-8-sig"))
    assert pf.cash == pytest.approx(612_500.5)
    assert pf.positions.loc["KFY", "prix_achat"] == pytest.approx(71.02)
    pf = load_portfolio(write(tmp_path, text, encoding="cp1252"))
    assert pf.positions.loc["KFY", "quantite"] == 2800


def test_purchase_price_is_optional(tmp_path):
    pf = load_portfolio(write(tmp_path, "ticker,quantite\nCASH,1000\nKFY,10\n"))
    assert math.isnan(pf.positions.loc["KFY", "prix_achat"])


@pytest.mark.parametrize("text, message", [
    ("ticker,quantite\nKFY,10\n", "CASH"),
    ("ticker,quantite\nCASH,10\nKFY,10\nKFY,5\n", "plusieurs fois"),
    ("ticker,quantite\nCASH,dix\n", "invalide"),
    ("symbole,nombre\nCASH,10\n", "colonnes"),
])
def test_invalid_files_give_clear_errors(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_portfolio(write(tmp_path, text))


# --- Ordres d'achat ----------------------------------------------------------------------------

def signals(*rows):
    return pd.DataFrame(rows, columns=["ticker", "rsi", "dernier_cours"]).set_index("ticker")


def portfolio(cash, **positions):
    pos = pd.DataFrame([(t, q, p) for t, (q, p) in positions.items()],
                       columns=["ticker", "quantite", "prix_achat"]).set_index("ticker")
    return Portfolio(cash=cash, positions=pos)


def test_orders_follow_rsi_order_and_size_limit():
    sig = signals(("A", 2.0, 50.0), ("B", 5.0, 20.0), ("C", 8.0, 100.0))
    plan = plan_orders(sig, portfolio(1_000_000), pd.Series(dtype=float), StrategyConfig())
    assert plan.equity == 1_000_000
    assert list(plan.orders.index) == ["A", "B", "C"]
    assert plan.orders.loc["A", "quantite"] == int(200_000 // (50 * (1 + PRICE_BUFFER)))
    # Même si l'action ouvre 2 % plus haut, la position reste sous 20 % du portefeuille.
    assert (plan.orders["montant_estime"] * (1 + PRICE_BUFFER) <= 200_000).all()
    assert plan.skipped == []


def test_held_positions_are_valued_and_not_bought_again():
    sig = signals(("A", 2.0, 50.0), ("B", 5.0, 20.0))
    pf = portfolio(250_000, A=(3_000, 48.0), X=(7_000, 100.0))
    prices = pd.Series({"A": 50.0, "X": 85.0})
    plan = plan_orders(sig, pf, prices, StrategyConfig())

    assert plan.equity == pytest.approx(250_000 + 3_000 * 50 + 7_000 * 85)
    assert plan.positions.loc["X", "poids"] == pytest.approx(595_000 / plan.equity)
    assert plan.positions.loc["A", "stop_loss"] == pytest.approx(48 * 0.95)
    assert list(plan.positions["type"]) == ["RSI", "RSI"]
    assert list(plan.orders.index) == ["B"]
    assert plan.take_profit_pct == 0.05


def test_cash_limits_orders_and_tiny_leftovers_are_skipped():
    sig = signals(("A", 1.0, 10.0), ("B", 2.0, 10.0), ("C", 3.0, 10.0))
    pf = portfolio(250_000, X=(7_500, 100.0))
    plan = plan_orders(sig, pf, pd.Series({"X": 100.0}), StrategyConfig())

    assert list(plan.orders.index) == ["A", "B"]
    assert plan.orders.loc["A", "montant_estime"] == pytest.approx(200_000 / 1.02, abs=10)
    assert plan.orders.loc["B", "montant_estime"] == pytest.approx(50_000 / 1.02, abs=600)
    assert plan.skipped == ["C"]  # le reliquat de cash est sous 2 % du portefeuille
    spent = (plan.orders["montant_estime"] * (1 + PRICE_BUFFER)).sum()
    assert spent <= pf.cash


def test_position_without_recent_price_uses_purchase_price():
    plan = plan_orders(signals(), portfolio(100_000, OLD=(1_000, 10.0)), pd.Series(dtype=float),
                       StrategyConfig())
    assert plan.unpriced == ["OLD"]
    assert plan.equity == pytest.approx(110_000)


def test_max_positions_counts_existing_holdings():
    sig = signals(("A", 1.0, 10.0), ("B", 2.0, 10.0))
    pf = portfolio(800_000, X=(2_000, 100.0))
    plan = plan_orders(sig, pf, pd.Series({"X": 100.0}), StrategyConfig(max_positions=2))
    assert list(plan.orders.index) == ["A"]


def test_empty_portfolio_is_rejected():
    with pytest.raises(ValueError, match="Portefeuille vide"):
        plan_orders(signals(("A", 1.0, 10.0)), portfolio(0), pd.Series(dtype=float),
                    StrategyConfig())



# --- Take profit selon la tendance et grandes valeurs -------------------------------------------

@pytest.mark.parametrize("bull_tp, bullish, expected", [
    (0.10, True, 0.10), (0.10, False, 0.05),
    (0.0, True, 0.05),  # par défaut, l'objectif reste à 5 % quelle que soit la tendance
])
def test_take_profit_depends_on_market_trend(bull_tp, bullish, expected):
    plan = plan_orders(signals(("A", 2.0, 50.0)), portfolio(1_000_000), pd.Series(dtype=float),
                       StrategyConfig(take_profit_bull_pct=bull_tp), bullish=bullish)
    assert plan.take_profit_pct == expected


BIG = [f"M{k}" for k in range(1, 11)]


def test_leftover_cash_is_spread_over_largest_stocks():
    prices = pd.Series({t: 100.0 for t in BIG})
    plan = plan_orders(signals(), portfolio(1_000_000), prices, StrategyConfig(),
                       core_members=BIG, core_universe=BIG)
    assert sorted(plan.core_buys.index) == sorted(BIG)
    assert (plan.core_buys["quantite"] == int(100_000 // (100 * (1 + PRICE_BUFFER)))).all()
    assert plan.core_buys["montant_estime"].sum() * (1 + PRICE_BUFFER) <= 1_000_000
    assert plan.core_sells.empty


def test_rsi_buys_are_funded_by_selling_the_largest_lines():
    # 6 grandes valeurs sous la limite de 20 %, pas de cash.
    lines = {"M1": 1_500, "M2": 1_600, "M3": 1_700, "M4": 1_800, "M5": 1_700, "M6": 1_700}
    prices = pd.Series({t: 100.0 for t in lines})
    pf = portfolio(0, **{t: (q, 90.0) for t, q in lines.items()})
    plan = plan_orders(signals(("A", 2.0, 50.0)), pf, prices, StrategyConfig(),
                       core_members=list(lines), core_universe=list(lines))

    assert list(plan.orders.index) == ["A"]
    cost = plan.orders.loc["A", "quantite"] * 50 * (1 + PRICE_BUFFER)
    assert plan.core_sells.index[0] == "M4"  # la plus grosse ligne d'abord
    assert plan.core_sells["montant_estime"].sum() * (1 - PRICE_BUFFER) >= cost
    assert plan.core_buys.empty
    assert set(plan.positions["type"]) == {"grande valeur"}
    assert plan.positions["stop_loss"].isna().all()  # pas de stop sur les grandes valeurs


def test_stocks_leaving_the_ranking_are_sold_and_reinvested():
    prices = pd.Series({"OLD": 100.0, "M1": 100.0, "M2": 100.0})
    pf = portfolio(0, OLD=(1_000, 100.0), M1=(500, 100.0))
    plan = plan_orders(signals(), pf, prices, StrategyConfig(),
                       core_members=["M1", "M2"], core_universe=["OLD", "M1", "M2"])
    assert plan.core_sells.loc["OLD", "quantite"] == 1_000
    assert list(plan.core_buys.index) == ["M2"]  # la grande valeur la moins représentée


def test_reserved_share_of_largest_stocks_in_daily_orders():
    prices = pd.Series({t: 100.0 for t in BIG})
    sig = signals(("A", 1.0, 50.0), ("B", 2.0, 50.0), ("C", 3.0, 50.0), ("D", 4.0, 50.0))
    plan = plan_orders(sig, portfolio(1_000_000), prices, StrategyConfig(core_min_pct=0.5),
                       core_members=BIG, core_universe=BIG)
    rsi_cost = (plan.orders["montant_estime"] * (1 + PRICE_BUFFER)).sum()
    assert rsi_cost <= 500_000 + 1e-6
    assert list(plan.orders.index) == ["A", "B", "C"]  # 20 % + 20 % + le reste des 50 %
    assert plan.core_buys["montant_estime"].sum() > 450_000


# --- Positions données en dollars --------------------------------------------------------------

def test_load_positions_given_in_dollars(tmp_path):
    text = "Ticker;Montant USD;Prix achat\nCASH;17 334;\nMU UW;36 565,50;914,13\n7203 JT;36 700;\n"
    pf = load_portfolio(write(tmp_path, text))
    assert pf.cash == 17_334
    assert pf.positions.loc["MU UW", "montant_usd"] == pytest.approx(36_565.5)
    assert math.isnan(pf.positions.loc["MU UW", "quantite"])
    assert list(pf.positions.index) == ["MU UW", "7203 JT"]


def test_quantity_wins_over_amount_when_both_are_given(tmp_path):
    pf = load_portfolio(write(tmp_path, "ticker,quantite,montant_usd\nCASH,,1000\nKFY,10,999\n"))
    assert pf.cash == 1000
    assert pf.positions.loc["KFY", "quantite"] == 10 and math.isnan(pf.positions.loc["KFY", "montant_usd"])


def test_dollar_amounts_are_turned_into_shares_at_the_last_price():
    pos = pd.DataFrame({"quantite": [math.nan, math.nan], "montant_usd": [36_565.0, 5_000.0],
                        "prix_achat": [math.nan, 12.0]}, index=["BIG", "OLD"])
    pf = Portfolio(cash=100_000, positions=pos)
    plan = plan_orders(signals(), pf, pd.Series({"BIG": 914.13}), StrategyConfig(),
                       core_universe=["BIG"])
    assert plan.positions.loc["BIG", "quantite"] == 40  # 36 565 / 914,13 = 40,0
    assert plan.positions.loc["BIG", "valeur"] == pytest.approx(36_565)
    # Sans cours récent : valorisée au montant donné, et signalée.
    assert plan.positions.loc["OLD", "valeur"] == pytest.approx(5_000)
    assert plan.equity == pytest.approx(141_565) and plan.unpriced == ["OLD"]
    # BIG ne fait plus partie des grandes valeurs retenues : ses 40 actions sont vendues.
    assert plan.core_sells.loc["BIG", "quantite"] == 40


def test_largest_stocks_above_the_position_limit_are_cut_back_to_it():
    prices = pd.Series({"M1": 100.0, "M2": 100.0, "M3": 100.0})
    pf = portfolio(0, M1=(3_000, 90.0), M2=(2_000, 90.0), M3=(5_000, 90.0))  # 30, 20 et 50 %
    plan = plan_orders(signals(("A", 2.0, 50.0)), pf, prices, StrategyConfig(),
                       core_members=["M1", "M2", "M3"], core_universe=["M1", "M2", "M3"])
    # Ramenées à 20 % de 1 M$ : la vente finance l'achat RSI, sans autre vente.
    assert plan.core_sells["quantite"].to_dict() == {"M1": 1_000, "M3": 3_000}
    assert list(plan.orders.index) == ["A"]
    assert plan.core_buys.empty  # toutes les lignes sont à la limite
