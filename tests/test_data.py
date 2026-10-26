"""Base de cours locale : téléchargements limités à ce qui manque, robustesse, et Yahoo."""

import numpy as np
import pandas as pd
import pytest

from rsi_strategy import data


class FakeYahoo:
    """Yahoo simulé : un cours par ticker et par jour ouvré jusqu'à « maintenant » (heure de New
    York), multiplié par `scale[ticker]` (ajustement recalculé après un dividende)."""

    def __init__(self, monkeypatch, now="2024-03-04 18:00"):
        self.calls: list[tuple[list[str], str]] = []
        self.now = pd.Timestamp(now)
        self.scale: dict[str, float] = {}
        self.empty: set[str] = set()
        monkeypatch.setattr(data, "download_ohlcv", self.download)
        monkeypatch.setattr(data, "_now", lambda: self.now)

    def download(self, tickers, start, end, **kw):
        self.calls.append((list(tickers), start))
        last = min(pd.Timestamp(end) - pd.Timedelta(days=1), self.now.normalize())
        idx = pd.bdate_range(start, last)
        cols = [t for t in tickers if t not in self.empty]
        if not cols:
            raise RuntimeError("Aucune donnée téléchargée depuis Yahoo Finance.")
        days = (idx - pd.Timestamp("2020-01-01")).days.to_numpy()
        close = pd.DataFrame({t: (100.0 + 0.1 * days) * self.scale.get(t, 1.0) for t in cols},
                             index=idx).astype("float32")
        return {f: close for f in data.FIELDS}


@pytest.fixture
def yahoo(monkeypatch):
    return FakeYahoo(monkeypatch)


def test_store_is_reused_for_the_same_or_a_shorter_period(tmp_path, yahoo):
    first = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    again = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    shorter = data.load_ohlcv(["A"], "2024-01-10", "2024-01-20", tmp_path)
    assert len(yahoo.calls) == 1
    pd.testing.assert_frame_equal(first["Close"], again["Close"])
    assert shorter["Close"].index[0] == pd.Timestamp("2024-01-10")
    assert shorter["Close"].index[-1] == pd.Timestamp("2024-01-19")


def test_only_missing_stocks_and_earlier_history_are_downloaded(tmp_path, yahoo):
    data.load_ohlcv(["A", "B"], "2024-01-01", "2024-02-01", tmp_path)
    data.load_ohlcv(["A", "B", "C"], "2024-01-01", "2024-02-01", tmp_path)
    assert yahoo.calls[1] == (["C"], "2024-01-01")
    panel = data.load_ohlcv(["A"], "2023-12-01", "2024-02-01", tmp_path)
    assert yahoo.calls[2] == (["A"], "2023-12-01")
    assert panel["Close"].index[0] == pd.Timestamp("2023-12-01")


def test_new_sessions_are_added_without_downloading_the_history_again(tmp_path, yahoo):
    data.load_ohlcv(["A", "B"], "2023-06-01", "2024-03-05", tmp_path)
    yahoo.now = pd.Timestamp("2024-03-05 10:00")  # séance du jour pas encore terminée
    data.load_ohlcv(["A", "B"], "2023-06-01", "2024-03-06", tmp_path)
    assert len(yahoo.calls) == 1

    yahoo.now = pd.Timestamp("2024-03-05 18:00")
    panel = data.load_ohlcv(["A", "B"], "2023-06-01", "2024-03-06", tmp_path)
    assert yahoo.calls[1:] == [(["A", "B"], "2024-02-19")]  # 14 jours avant le téléchargement précédent
    expected = yahoo.download(["A", "B"], "2023-06-01", "2024-03-06")["Close"]
    pd.testing.assert_frame_equal(panel["Close"], expected, check_freq=False)


def test_history_is_downloaded_again_when_yahoo_recalculates_the_adjustment(tmp_path, yahoo,
                                                                             capsys):
    data.load_ohlcv(["A", "B"], "2023-06-01", "2024-03-05", tmp_path)
    yahoo.now = pd.Timestamp("2024-03-05 18:00")
    yahoo.scale["B"] = 0.98  # dividende : tout l'historique ajusté de B baisse de 2 %
    panel = data.load_ohlcv(["A", "B"], "2023-06-01", "2024-03-06", tmp_path)
    assert yahoo.calls[1:] == [(["A", "B"], "2024-02-19"), (["B"], "2023-06-01")]
    expected = yahoo.download(["B"], "2023-06-01", "2024-03-06")["Close"]["B"]
    pd.testing.assert_series_equal(panel["Close"]["B"], expected, check_freq=False)
    assert "1 action(s) dont Yahoo a recalculé les cours ajustés" in capsys.readouterr().out


def test_stock_without_new_prices_keeps_the_stored_ones(tmp_path, yahoo):
    before = data.load_ohlcv(["A", "B"], "2024-01-01", "2024-03-05", tmp_path)
    yahoo.now = pd.Timestamp("2024-03-05 18:00")
    yahoo.empty.add("B")  # Yahoo ne renvoie rien pour B ce jour-là
    after = data.load_ohlcv(["A", "B"], "2024-01-01", "2024-03-06", tmp_path)
    pd.testing.assert_series_equal(after["Close"]["B"].dropna(), before["Close"]["B"],
                                   check_freq=False)


def test_truncated_store_is_rebuilt(tmp_path, yahoo, capsys):
    data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    store = tmp_path / "actions.pkl"
    store.write_bytes(store.read_bytes()[:20])  # fichier tronqué, comme après un disque plein

    panel = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert len(yahoo.calls) == 2
    assert list(panel["Close"].columns) == ["A"]
    assert "Base de cours illisible" in capsys.readouterr().out
    pd.read_pickle(store)  # la base a été réécrite correctement


def test_disk_full_does_not_stop_the_run(tmp_path, yahoo, monkeypatch, capsys):
    def disk_full(obj, path):
        open(path, "wb").write(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(data.pd, "to_pickle", disk_full)
    panel = data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert list(panel["Close"].columns) == ["A"]
    assert list(tmp_path.iterdir()) == []  # ni base tronquée, ni fichier temporaire
    assert "Base de cours non enregistrée" in capsys.readouterr().out


def test_old_cache_files_are_removed(tmp_path, yahoo, capsys):
    (tmp_path / "ohlcv_0123456789ab.pkl").write_bytes(b"x" * 1000)
    data.load_ohlcv(["A"], "2024-01-01", "2024-02-01", tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["actions.pkl"]
    assert "Ancien cache des cours supprimé" in capsys.readouterr().out


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


def test_refused_stocks_are_asked_again_next_time(tmp_path, monkeypatch, capsys):
    calls = fake_yahoo(monkeypatch, {"BBB": 4})
    panel = data.load_ohlcv(["AAA", "BBB"], "2024-01-01", "2024-02-01", tmp_path)
    assert list(panel["Close"].columns) == ["AAA"]
    assert "1 action(s) non téléchargées : Yahoo a limité le nombre de requêtes" in (
        capsys.readouterr().out)

    calls.clear()
    panel = data.load_ohlcv(["AAA", "BBB"], "2024-01-01", "2024-02-01", tmp_path)
    assert calls == [["BBB"]]  # AAA vient de la base
    assert list(panel["Close"].columns) == ["AAA", "BBB"]


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
