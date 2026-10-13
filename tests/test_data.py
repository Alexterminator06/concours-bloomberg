"""Cache des cours : robustesse aux fichiers corrompus, au disque plein, et nettoyage."""

import os

import pandas as pd
import pytest

from rsi_strategy import data


@pytest.fixture
def fake_download(monkeypatch):
    calls = []

    def download(tickers, start, end):
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
