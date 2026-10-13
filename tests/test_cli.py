"""Test de bout en bout de la ligne de commande, sans réseau (données Yahoo simulées)."""

import re

import numpy as np
import pandas as pd
import pytest

import rsi_strategy.__main__ as cli


def fake_panel(tickers, start, end):
    idx = pd.bdate_range(start, end, inclusive="left")
    rng = np.random.default_rng(len(tickers))
    close = pd.DataFrame(40 * np.exp(np.cumsum(rng.normal(0, 0.025, (len(idx), len(tickers))), axis=0)),
                         index=idx, columns=tickers)
    noise = np.abs(rng.normal(0, 0.015, close.shape))
    return {
        "Open": close.shift(1).fillna(close),
        "High": close * (1 + noise),
        "Low": close * (1 - noise),
        "Close": close,
        "Volume": pd.DataFrame(5e5, index=idx, columns=tickers),
    }


def test_backtest_command_writes_results(tmp_path, monkeypatch, capsys):
    universe = pd.DataFrame({"symbol": [f"S{k}" for k in range(30)]})
    monkeypatch.setattr(cli, "load_universe", lambda *a, **k: universe)
    monkeypatch.setattr(cli, "load_ohlcv",
                        lambda tickers, start, end, cache, refresh=False: fake_panel(tickers, start, end))

    cli.main(["backtest", "--start", "2024-01-01", "--end", "2025-06-30", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Performance : stratégie vs indices" in out
    for name in ["trades.csv", "equity_curve.csv", "summary.csv", "performance.png", "next_orders.csv"]:
        assert (tmp_path / name).exists(), name
    curves = pd.read_csv(tmp_path / "equity_curve.csv", index_col=0)
    assert list(curves.columns) == ["Stratégie RSI", "VT", "SMMD", "IWM"]
    assert "Rendement relatif" in out
    assert (curves.iloc[0] == 1_000_000).all()


def test_backtest_command_with_filters(tmp_path, monkeypatch, capsys):
    universe = pd.DataFrame({"symbol": [f"S{k}" for k in range(30)]})
    monkeypatch.setattr(cli, "load_universe", lambda *a, **k: universe)
    monkeypatch.setattr(cli, "load_ohlcv",
                        lambda tickers, start, end, cache, refresh=False: fake_panel(tickers, start, end))

    cli.main(["backtest", "--start", "2024-01-01", "--end", "2025-06-30", "--out", str(tmp_path),
              "--trend-filter", "200", "--market-filter", "200", "--take-profit-first"])

    out = capsys.readouterr().out
    assert "Frais et glissement payés ($)" in out
    assert "Séances ambiguës" in out
    trades = pd.read_csv(tmp_path / "trades.csv")
    assert {"costs", "ambiguous"} <= set(trades.columns)


def pullback_panel(tickers, start, end):
    """Hausse régulière puis repli la dernière séance : DEEP -8 %, HELD -6 %, SOFT -5 %."""
    drops = {"DEEP": 0.92, "HELD": 0.94, "SOFT": 0.95}
    idx = pd.bdate_range(end=pd.Timestamp(end) - pd.Timedelta(days=1), periods=300)
    trend = 100 * 1.005 ** np.arange(len(idx))
    close = pd.DataFrame({t: trend * np.r_[np.ones(len(idx) - 1), drops.get(t, 1.0)]
                          for t in tickers}, index=idx)
    return {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
            "Volume": pd.DataFrame(1e6, index=idx, columns=tickers)}


def run_scan(tmp_path, monkeypatch, portfolio_text=None, wls_text=None, extra_args=()):
    universe = pd.DataFrame({"symbol": ["DEEP", "HELD", "SOFT"]})
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_universe", lambda *a, **k: universe)
    monkeypatch.setattr(cli, "download_ohlcv", pullback_panel)
    if portfolio_text is not None:
        (tmp_path / "portefeuille.csv").write_text(portfolio_text, encoding="utf-8")
    if wls_text is not None:
        source = tmp_path / "WLS.csv"
        source.write_text(wls_text, encoding="utf-8")
        cli.main(["import-wls", str(source)])
    cli.main(["scan", *extra_args])


# Export Bloomberg : 3 small caps US, 2 grandes valeurs US, une action japonaise (ignorée).
WLS_EXPORT = """Ticker,Name,Weight,Shares,Price
BIG1 UW Equity,Big One,4.0,20000,200
BIG2 UN Equity,Big Two,3.0,15000,200
7203 JT Equity,Toyota,0.5,13000,3000
DEEP UN Equity,Deep Inc,0.001,10,100
HELD UQ Equity,Held Corp,0.001,10,100
SOFT UR Equity,Soft Co,0.001,10,100
"""


def test_scan_with_portfolio_lists_exact_orders(tmp_path, monkeypatch, capsys):
    run_scan(tmp_path, monkeypatch, "ticker,quantite,prix_achat\nCASH,300000,\nHELD,1000,400\n")
    out = capsys.readouterr().out
    assert "Portefeuille : portefeuille.csv (1 position(s), cash 300 000 $)" in out
    assert "Signaux au" in out and "-> 3 action(s)" in out

    (orders_file,) = (tmp_path / "results").glob("ordres_*.csv")
    orders = pd.read_csv(orders_file)
    assert list(orders["ticker"]) == ["DEEP", "SOFT"]  # RSI le plus bas d'abord, HELD déjà détenue
    assert set(orders["type"]) == {"RSI"}  # sans liste WLS, pas de grandes valeurs
    equity = 300_000 + 1_000 * pullback_panel(["HELD"], None, "2026-10-14")["Close"]["HELD"].iloc[-1]
    assert (orders["montant_estime"] <= 0.20 * equity).all()
    assert orders["montant_estime"].sum() <= 300_000
    # VT monte régulièrement dans ces données : tendance haussière, objectif relevé à +10 %.
    assert (orders["take_profit_pct"] == 0.10).all()
    assert "stop loss -5% et take profit +10%" in out


def test_scan_without_portfolio_gives_indicative_orders(tmp_path, monkeypatch, capsys):
    run_scan(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "portefeuille.exemple.csv" in out
    (signals_file,) = (tmp_path / "results").glob("signals_*.csv")
    assert list(pd.read_csv(signals_file, index_col=0).index) == ["DEEP", "HELD", "SOFT"]


def test_scan_reports_portfolio_errors_without_traceback(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="ajoutez une ligne « CASH »"):
        run_scan(tmp_path, monkeypatch, "ticker,quantite\nKFY,10\n")



def test_import_wls_keeps_us_members(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    source = tmp_path / "WLS.csv"
    source.write_text(WLS_EXPORT + "BRK/B UN Equity,Berkshire,1.0,2000,500\n", encoding="utf-8")
    cli.main(["import-wls", str(source)])
    members = pd.read_csv(tmp_path / "data" / "wls_members.csv")
    assert list(members["symbol"]) == ["BIG1", "BIG2", "BRK-B", "DEEP", "HELD", "SOFT"]
    out = capsys.readouterr().out
    assert "6 actions US membres du WLS" in out and "3 small & mid caps" in out


def test_scan_places_leftover_cash_in_largest_stocks(tmp_path, monkeypatch, capsys):
    run_scan(tmp_path, monkeypatch, "ticker,quantite,prix_achat\nCASH,600000,\nHELD,1000,400\n",
             wls_text=WLS_EXPORT, extra_args=["--core-size", "2"])
    out = capsys.readouterr().out
    assert "Univers : 3 small & mid caps US membres du WLS" in out
    orders = pd.read_csv(next((tmp_path / "results").glob("ordres_*.csv")))
    assert list(orders["etape"]) == ["2. achat", "2. achat", "3. achat", "3. achat"]
    assert list(orders["ticker"]) == ["DEEP", "SOFT", "BIG1", "BIG2"]
    assert orders["montant_estime"].sum() <= 600_000
    assert "2. Acheter ces grandes valeurs avec le cash restant" in out  # pas de vente : 2 étapes


def test_backtest_command_with_largest_stocks(tmp_path, monkeypatch, capsys):
    rows = [f"S{k} UN Equity,Small {k},0.001,10,100" for k in range(30)]
    rows += [f"BIG{k} UW Equity,Big {k},{3 - k},10000,200" for k in range(3)]
    source = tmp_path / "WLS.csv"
    source.write_text("Ticker,Name,Weight,Shares,Price\n" + "\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_ohlcv",
                        lambda tickers, start, end, cache, refresh=False: fake_panel(tickers, start, end))
    cli.main(["import-wls", str(source)])
    cli.main(["backtest", "--start", "2024-01-01", "--end", "2025-06-30", "--out", str(tmp_path),
              "--core-size", "2"])
    out = capsys.readouterr().out
    assert "Univers : 30 small & mid caps US membres du WLS" in out
    assert re.search(r"Part moyenne en grandes valeurs\s+\d+\.\d\d%", out)
    assert re.search(r"P&L grandes valeurs \(\$\)\s+-?[\d ]+\n", out)
    trades = pd.read_csv(tmp_path / "trades.csv")
    assert not trades["ticker"].str.startswith("BIG").any()
