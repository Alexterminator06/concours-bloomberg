"""Test de bout en bout de la ligne de commande, sans réseau (données Yahoo simulées)."""

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


def run_scan(tmp_path, monkeypatch, portfolio_text=None):
    universe = pd.DataFrame({"symbol": ["DEEP", "HELD", "SOFT"]})
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_universe", lambda *a, **k: universe)
    monkeypatch.setattr(cli, "download_ohlcv", pullback_panel)
    if portfolio_text is not None:
        (tmp_path / "portefeuille.csv").write_text(portfolio_text, encoding="utf-8")
    cli.main(["scan"])


def test_scan_with_portfolio_lists_exact_orders(tmp_path, monkeypatch, capsys):
    run_scan(tmp_path, monkeypatch, "ticker,quantite,prix_achat\nCASH,300000,\nHELD,1000,400\n")
    out = capsys.readouterr().out
    assert "Portefeuille : portefeuille.csv (1 position(s), cash 300 000 $)" in out
    assert "Signaux au" in out and "-> 3 action(s)" in out

    (orders_file,) = (tmp_path / "results").glob("ordres_*.csv")
    orders = pd.read_csv(orders_file, index_col=0)
    assert list(orders.index) == ["DEEP", "SOFT"]  # RSI le plus bas d'abord, HELD déjà détenue
    equity = 300_000 + 1_000 * pullback_panel(["HELD"], None, "2026-10-14")["Close"]["HELD"].iloc[-1]
    assert (orders["montant_estime"] <= 0.20 * equity).all()
    assert orders["montant_estime"].sum() <= 300_000
    assert "placer un stop loss à -5% et un take profit à +5%" in out


def test_scan_without_portfolio_gives_indicative_orders(tmp_path, monkeypatch, capsys):
    run_scan(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "portefeuille.exemple.csv" in out
    (signals_file,) = (tmp_path / "results").glob("signals_*.csv")
    assert list(pd.read_csv(signals_file, index_col=0).index) == ["DEEP", "HELD", "SOFT"]


def test_scan_reports_portfolio_errors_without_traceback(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="ajoutez une ligne « CASH »"):
        run_scan(tmp_path, monkeypatch, "ticker,quantite\nKFY,10\n")
