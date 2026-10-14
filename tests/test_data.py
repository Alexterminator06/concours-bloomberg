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


def test_old_cache_files_are_pruned(tmp_path, fake_download):
    for k in range(data.MAX_CACHE_FILES + 3):
        data.load_ohlcv(["A"], f"2024-01-{k + 1:02d}", "2024-03-01", tmp_path)
        newest = max(tmp_path.glob("ohlcv_*.pkl"), key=os.path.getmtime)
        os.utime(newest, (1_000_000 + k, 1_000_000 + k))  # horodatages distincts
    assert len(list(tmp_path.glob("ohlcv_*.pkl"))) == data.MAX_CACHE_FILES


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
