"""Téléchargement des cours journaliers (OHLCV) depuis Yahoo Finance, avec cache local."""

import hashlib
from pathlib import Path

import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def download_ohlcv(tickers: list[str], start: str, end: str | None = None,
                   chunk_size: int = 200) -> dict[str, pd.DataFrame]:
    """Télécharge les cours ajustés (splits + dividendes) et renvoie un dict
    {champ: DataFrame dates x tickers} pour les champs Open, High, Low, Close, Volume.
    """
    import yfinance as yf

    frames = {f: [] for f in FIELDS}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"  Téléchargement {i + 1}-{i + len(chunk)} / {len(tickers)} ...")
        raw = yf.download(chunk, start=start, end=end, auto_adjust=True, actions=False,
                          group_by="column", threads=True, progress=False,
                          multi_level_index=True)
        if raw is None or raw.empty:
            continue
        for f in FIELDS:
            part = raw[f]
            if isinstance(part, pd.Series):  # un seul ticker
                part = part.to_frame(chunk[0])
            frames[f].append(part)

    if not frames["Close"]:
        raise RuntimeError("Aucune donnée téléchargée depuis Yahoo Finance.")

    panel = {}
    for f in FIELDS:
        df = pd.concat(frames[f], axis=1)
        df = df.loc[:, ~df.columns.duplicated()]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        panel[f] = df.sort_index()

    # On écarte les tickers sans aucune donnée et les jours sans aucune cotation.
    valid_cols = panel["Close"].columns[panel["Close"].notna().any()]
    valid_rows = panel["Close"].index[panel["Close"].notna().any(axis=1)]
    return {f: df.loc[valid_rows, valid_cols] for f, df in panel.items()}


def load_ohlcv(tickers: list[str], start: str, end: str | None, cache_dir: Path,
               refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Comme `download_ohlcv`, mais réutilise un cache pickle pour la même requête."""
    key = hashlib.md5(f"{sorted(tickers)}|{start}|{end}".encode()).hexdigest()[:12]
    path = cache_dir / f"ohlcv_{key}.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    panel = download_ohlcv(tickers, start, end)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(panel, path)
    return panel
