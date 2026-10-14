"""Conversion des membres du WLS (tickers Bloomberg) et des cours en dollars."""

import logging

import numpy as np
import pandas as pd
import pytest

from rsi_strategy import StrategyConfig, compute_signals, data
from rsi_strategy.wls import alternative_symbols, bloomberg_lookup, bloomberg_to_yahoo, read_bloomberg_export


@pytest.mark.parametrize("bloomberg, yahoo", [
    ("NVDA UW Equity", "NVDA"), ("BRK/B UN Equity", "BRK-B"), ("7203 JT Equity", "7203.T"),
    ("700 HK Equity", "0700.HK"), ("RR/ LN Equity", "RR.L"), ("BT/A LN", "BT-A.L"),
    ("2330 TT Equity", "2330.TW"), ("005930 KP Equity", "005930.KS"), ("NOVN SE Equity", "NOVN.SW"),
    ("SAP GY Equity", "SAP.DE"), ("TTE FP Equity", "TTE.PA"), ("ITUB4 BS Equity", "ITUB4.SA"),
    ("600519 C1 Equity", "600519.SS"), ("EMAAR DB Equity", None), ("n'importe quoi", None),
])
def test_bloomberg_tickers_are_converted_to_yahoo(bloomberg, yahoo):
    assert bloomberg_to_yahoo(bloomberg) == yahoo


def test_alternative_spellings():
    assert alternative_symbols("6488.TW", "TT") == ["6488.TWO"]
    assert alternative_symbols("INVEB.ST", "SS") == ["INVE-B.ST"]
    assert alternative_symbols("SAP.DE", "GY") == []


def test_export_gives_currency_and_dollar_size(tmp_path):
    path = tmp_path / "WLS.csv"
    path.write_text("Ticker,Name,Weight,Shares,Price\n"
                    "AAA UN Equity,A,2.0,1000,100\n"         # 100 Md$ pour un poids de 2
                    "7203 JT Equity,Toyota,1.0,10000,3000\n"  # 30 000 Md¥ : 50 Md$ d'après le poids
                    "HSBA LN Equity,HSBC,0.5,1000,1000\n", encoding="utf-8")
    members = read_bloomberg_export(path).set_index("symbol")
    assert members.loc["7203.T", "currency"] == "JPY"
    assert members.loc["7203.T", "mcap_usd"] == pytest.approx(50e9)
    assert (members.loc["HSBA.L", "currency"], members.loc["HSBA.L", "unit"]) == ("GBP", 0.01)
    lookup = bloomberg_lookup(members.reset_index())
    assert lookup["7203 JT"] == "7203.T" and lookup["HSBA LN"] == "HSBA.L"


def test_prices_are_converted_to_dollars():
    idx = pd.bdate_range("2024-01-01", periods=3)
    frame = lambda values: pd.DataFrame(values, index=idx)
    panel = {f: frame({"7203.T": [3000.0] * 3, "HSBA.L": [1000.0] * 3, "AAA": [50.0] * 3,
                       "XYZ.SR": [10.0] * 3}) for f in data.FIELDS}
    fx = frame({"JPY=X": [150.0, 150.0, np.nan], "GBP=X": [0.8] * 3})  # pas de cours pour SAR
    currency = pd.Series({"7203.T": "JPY", "HSBA.L": "GBP", "AAA": "USD", "XYZ.SR": "SAR"})
    unit = pd.Series({"7203.T": 1.0, "HSBA.L": 0.01, "AAA": 1.0, "XYZ.SR": 1.0})
    usd = data.to_usd(panel, currency, unit, fx)
    assert usd["Close"]["7203.T"].tolist() == pytest.approx([20.0] * 3)  # 3000 ¥ / 150
    assert usd["Close"]["HSBA.L"].iloc[0] == pytest.approx(12.5)        # 1000 pence = 10 £
    assert usd["Close"]["AAA"].iloc[0] == 50.0
    assert "XYZ.SR" not in usd["Close"]                                  # devise sans taux
    assert usd["Volume"]["7203.T"].iloc[0] == 3000.0                     # volume inchangé


def test_alternative_spelling_is_tried_when_main_symbol_has_no_prices(monkeypatch):
    import yfinance as yf

    def fake_download(chunk, start, end, **kwargs):
        idx = pd.bdate_range(start, periods=3)
        raw = pd.DataFrame(np.nan, index=idx, columns=pd.MultiIndex.from_product([data.FIELDS, chunk]))
        for t in chunk:
            if t != "6488.TW":  # absent sous .TW, présent sous .TWO
                raw.loc[:, (slice(None), t)] = 10.0
        return raw

    monkeypatch.setattr(yf, "download", fake_download)
    monkeypatch.setattr(data, "PAUSE", 0)
    panel = data.download_ohlcv(["2330.TW", "6488.TW"], "2024-01-01", "2024-02-01",
                                alternatives={"6488.TW": ["6488.TWO"]})
    assert sorted(panel["Close"].columns) == ["2330.TW", "6488.TW"]
    assert panel["Close"]["6488.TW"].notna().all()


def test_signals_with_different_exchange_holidays():
    idx = pd.bdate_range("2024-01-01", periods=30)
    falling = [100 * 0.98 ** k for k in range(30)]
    close = pd.DataFrame({"US": falling, "JP": falling}, index=idx)
    close.loc[idx[[10, 20, 29]], "JP"] = np.nan  # jours fériés à Tokyo
    panel = {"Open": close, "High": close, "Low": close, "Close": close,
             "Volume": pd.DataFrame(1e6, index=idx, columns=close.columns)}
    cfg = StrategyConfig(rsi_period=2, trend_ma_days=0, min_avg_dollar_volume=0.0)
    _, eligible = compute_signals(panel, cfg)
    assert eligible["JP"].iloc[21:29].all()  # le jour férié n'interrompt pas les signaux
    assert not eligible["JP"].iloc[29]        # pas de signal un jour sans cotation
    assert eligible["US"].iloc[29]


def test_minimum_price_only_applies_to_listed_tickers():
    idx = pd.bdate_range("2024-01-01", periods=20)
    falling = [3.0 * 0.98 ** k for k in range(20)]  # moins de 5 $
    close = pd.DataFrame({"US": falling, "HK": falling}, index=idx)
    panel = {"Open": close, "High": close, "Low": close, "Close": close,
             "Volume": pd.DataFrame(1e7, index=idx, columns=close.columns)}
    cfg = StrategyConfig(rsi_period=2, trend_ma_days=0, min_avg_dollar_volume=0.0)
    _, eligible = compute_signals(panel, cfg, price_filter=["US"])
    assert not eligible["US"].any()
    assert eligible["HK"].iloc[-1]
