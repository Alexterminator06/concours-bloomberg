"""Test de bout en bout de la ligne de commande, sans réseau (données Yahoo simulées)."""

import numpy as np
import pandas as pd

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
    assert list(curves.columns) == ["Stratégie RSI", "SMMD", "IWM", "IJH"]
    assert (curves.iloc[0] == 1_000_000).all()
