"""Cache des cours : robustesse aux fichiers corrompus, au disque plein, et nettoyage."""

import os

import pandas as pd
import pytest

from rsi_strategy import data


@pytest.fixture
def fake_download(monkeypatch):
    calls = []

    def download(tickers, start, end, **kw):
        calls.append(tickers)
        idx = pd.bdate_range(start, periods=3)
        return {f: pd.DataFrame(1.0, index=idx, columns=tickers) for f in data.FIELDS}

    monkeypatch.setattr(data, "download_ohlcv", download)
    return calls


def test_cache_is_reused(tmp_path, fake_download):
    first = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    second = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert len(fake_download) == 1
    pd.testing.assert_frame_equal(first["Close"], second["Close"])


def test_truncated_cache_is_downloaded_again(tmp_path, fake_download):
    data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    (cached,) = tmp_path.glob("ohlcv_*.pkl")
    cached.write_bytes(cached.read_bytes()[:20])  # fichier tronqué, comme après un disque plein

    panel = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert len(fake_download) == 2
    assert list(panel["Close"].columns) == ["A"]
    pd.read_pickle(cached)  # le cache a été réécrit correctement


def test_disk_full_does_not_stop_the_run(tmp_path, fake_download, monkeypatch, capsys):
    def disk_full(obj, path):
        open(path, "wb").write(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(data.pd, "to_pickle", disk_full)
    panel = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert list(panel["Close"].columns) == ["A"]
    assert list(tmp_path.iterdir()) == []  # ni cache tronqué, ni fichier temporaire
    assert "Cache non enregistré" in capsys.readouterr().out


def _load_with_distinct_mtime(tickers, start, tmp_path, k):
    data.load_ohlcv(tickers, start, "2024-03-01", tmp_path)
    newest = max(tmp_path.glob("ohlcv_*.pkl"), key=os.path.getmtime)
    os.utime(newest, (1_000_000 + k, 1_000_000 + k))  # horodatages distincts
    return newest


def test_old_cache_files_are_pruned_beyond_the_size_budget(tmp_path, fake_download, monkeypatch):
    first = _load_with_distinct_mtime(["A"], "2024-01-01", tmp_path, 0)
    monkeypatch.setattr(data, "MAX_CACHE_BYTES", int(3.5 * first.stat().st_size))
    for k in range(1, 7):
        _load_with_distinct_mtime(["A"], f"2024-01-{k + 1:02d}", tmp_path, k)
    assert len(list(tmp_path.glob("ohlcv_*.pkl"))) == 3


def test_small_cache_files_do_not_evict_the_stock_prices(tmp_path, fake_download, monkeypatch):
    # Chaque backtest écrit les actions, puis les taux de change et les indices (petits fichiers) :
    # les actions du run précédent doivent rester en cache.
    stocks = _load_with_distinct_mtime([f"T{k}" for k in range(300)], "2024-01-01", tmp_path, 0)
    small = _load_with_distinct_mtime(["VT"], "2024-01-01", tmp_path, 1)
    monkeypatch.setattr(data, "MAX_CACHE_BYTES", stocks.stat().st_size + 10 * small.stat().st_size)
    for k in range(2, 8):
        _load_with_distinct_mtime([f"FX{k}"], "2024-01-01", tmp_path, k)
    assert stocks.exists()


def test_download_summarises_tickers_without_prices(monkeypatch, capsys):
    import logging

    import yfinance as yf

    def fake_yf_download(chunk, start, end, **kwargs):
        logging.getLogger("yfinance").error("$NEW: Data doesn't exist")
        idx = pd.bdate_range(start, periods=3)
        cols = pd.MultiIndex.from_product([data.FIELDS, chunk])
        raw = pd.DataFrame(1.0, index=idx, columns=cols)
        raw.loc[:, (slice(None), "NEW")] = float("nan")  # introduite en bourse plus tard
        return raw

    monkeypatch.setattr(yf, "download", fake_yf_download)
    monkeypatch.setattr(data, "PAUSE", 0)
    level = logging.getLogger("yfinance").level
    panel = data.download_ohlcv(["OLD", "NEW"], "2019-01-01", "2019-02-01")

    assert list(panel["Close"].columns) == ["OLD"]
    out = capsys.readouterr()
    assert "1 action(s) sans cours sur la période" in out.out
    assert "Data doesn't exist" not in out.err
    assert logging.getLogger("yfinance").level == level


RATE_LIMIT = "YFRateLimitError('Too Many Requests. Rate limited. Try after a while.')"


def fake_yahoo(monkeypatch, refuse_times: dict[str, int]):
    """yf.download simulé : chaque ticker de `refuse_times` est refusé (limite de requêtes) le
    nombre de fois indiqué, puis servi. Le samedi et le dimanche n'ont pas de cotation."""
    import logging

    import yfinance as yf

    calls = []

    def download(chunk, start, end, **kwargs):
        calls.append(list(chunk))
        idx = pd.date_range(start, periods=7)  # du lundi au dimanche
        raw = pd.DataFrame(1.0, index=idx, columns=pd.MultiIndex.from_product([data.FIELDS, chunk]))
        refused = [t for t in chunk if refuse_times.get(t, 0) > 0]
        for t in refused:
            refuse_times[t] -= 1
            raw.loc[:, (slice(None), t)] = float("nan")
        if refused:
            logging.getLogger("yfinance").error(f"{refused}: {RATE_LIMIT}")
        return raw

    monkeypatch.setattr(yf, "download", download)
    monkeypatch.setattr(data, "PAUSE", 0)
    monkeypatch.setattr(data, "RETRY_WAITS", (0, 0, 0))
    return calls


def test_rate_limited_tickers_are_downloaded_again(monkeypatch, capsys):
    calls = fake_yahoo(monkeypatch, {"BBB": 2})
    report = {}
    panel = data.download_ohlcv(["AAA", "BBB"], "2024-01-01", "2024-02-01", report=report)
    assert sorted(panel["Close"].columns) == ["AAA", "BBB"]
    assert calls == [["AAA", "BBB"], ["BBB"], ["BBB"]]
    assert report["refused"] == set()
    assert list(panel["Close"].index.dayofweek) == [0, 1, 2, 3, 4]  # pas de week-end
    assert "Too Many Requests" not in capsys.readouterr().err


def test_incomplete_download_is_reported_and_not_cached(tmp_path, monkeypatch, capsys):
    fake_yahoo(monkeypatch, {"BBB": 99})
    panel = data.load_ohlcv(["AAA", "BBB"], "2024-01-01", "2024-02-01", tmp_path)
    assert list(panel["Close"].columns) == ["AAA"]
    out = capsys.readouterr().out
    assert "1 action(s) non téléchargées : Yahoo a limité le nombre de requêtes" in out
    assert "Cache non enregistré : téléchargement incomplet" in out
    assert not list(tmp_path.glob("ohlcv_*.pkl"))


def test_sectors_are_fetched_once_and_cached(tmp_path):
    from rsi_strategy.data import load_sectors

    calls = []

    def fetch(symbols):  # Yahoo refuse LIMIT (limite de requêtes) et n'a pas de secteur pour X
        calls.append(list(symbols))
        return {s: "" if s == "X" else "Tech" for s in symbols if s != "LIMIT"}

    path = tmp_path / "data" / "secteurs.csv"
    assert load_sectors(["A", "LIMIT", "X"], path, fetch=fetch) == {"A": "Tech", "X": ""}
    assert load_sectors(["A", "C", "LIMIT", "X"], path, fetch=fetch) == {
        "A": "Tech", "C": "Tech", "X": ""}
    assert calls == [["A", "LIMIT", "X"], ["C", "LIMIT", "X"]]  # LIMIT et X redemandées, pas A


def test_sectors_try_the_other_yahoo_spellings(monkeypatch):
    import yfinance as yf
    from rsi_strategy.data import fetch_sectors

    known = {"NOVO-B.CO": "Healthcare", "AAPL": "Technology"}

    class FakeTicker:  # Yahoo connaît Novo Nordisk sous NOVO-B.CO, pas sous NOVOB.CO
        def __init__(self, symbol):
            self.symbol = symbol

        def get_info(self):
            if self.symbol not in known:
                raise ValueError("HTTP Error 404: Quote not found")
            return {"sector": known[self.symbol]}

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    assert fetch_sectors(["NOVOB.CO", "AAPL", "NONE"], {"NOVOB.CO": ["NOVO-B.CO"]}) == {
        "NOVOB.CO": "Healthcare", "AAPL": "Technology", "NONE": ""}


def test_one_day_price_spikes_are_removed_but_lasting_moves_are_kept():
    from rsi_strategy.data import remove_price_spikes

    idx = pd.bdate_range("2016-12-19", periods=6)
    close = pd.DataFrame({
        "LTM": [100.0, 101.0, 9_000.0, 102.0, 103.0, 104.0],  # x90 un seul jour, comme LTM.SN
        "DROP": [50.0, 50.0, 5.0, 51.0, 52.0, 53.0],          # /10 un seul jour
        "JUMP": [10.0, 10.0, 40.0, 41.0, 42.0, 43.0],         # x4 qui tient : un vrai mouvement
        "CRASH": [80.0, 80.0, 8.0, 8.5, 9.0, 9.0],            # /10 sans retour : réel aussi
    }, index=idx)
    panel = {f: close.copy() for f in ["Open", "High", "Low", "Close"]}
    panel["Volume"] = close * 0 + 1_000.0
    cleaned, count = remove_price_spikes(panel)
    assert count == 2
    for f in ["Open", "High", "Low", "Close"]:
        assert cleaned[f]["LTM"].isna().tolist() == [False, False, True, False, False, False]
        assert cleaned[f]["DROP"].isna().sum() == 1
        assert cleaned[f][["JUMP", "CRASH"]].notna().all().all()
    assert cleaned["Volume"].notna().all().all()
