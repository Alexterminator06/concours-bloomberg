"""Téléchargement des cours journaliers (OHLCV) depuis Yahoo Finance, avec cache local."""

import hashlib
import logging
import os
from pathlib import Path

import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MAX_CACHE_FILES = 4  # fichiers de cache conservés (les plus récemment utilisés), ~90 Mo chacun


def download_ohlcv(tickers: list[str], start: str, end: str | None = None,
                   chunk_size: int = 200) -> dict[str, pd.DataFrame]:
    """Télécharge les cours ajustés (splits + dividendes) et renvoie un dict
    {champ: DataFrame dates x tickers} pour les champs Open, High, Low, Close, Volume.
    """
    import yfinance as yf

    # yfinance affiche une erreur par action sans cours sur la période (introductions en bourse
    # récentes, par exemple) : on les masque et on affiche un résumé à la place.
    yf_logger = logging.getLogger("yfinance")
    previous_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    frames = {f: [] for f in FIELDS}
    try:
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
    finally:
        yf_logger.setLevel(previous_level)

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
    missing = len(set(tickers) - set(valid_cols))
    if missing:
        print(f"  {missing} action(s) sans cours sur la période (cotées plus tard ou retirées "
              f"de la cote) : ignorées.")
    valid_rows = panel["Close"].index[panel["Close"].notna().any(axis=1)]
    return {f: df.loc[valid_rows, valid_cols] for f, df in panel.items()}


def load_ohlcv(tickers: list[str], start: str, end: str | None, cache_dir: Path,
               refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Comme `download_ohlcv`, mais réutilise un cache pickle pour la même requête."""
    key = hashlib.md5(f"{sorted(tickers)}|{start}|{end}".encode()).hexdigest()[:12]
    path = cache_dir / f"ohlcv_{key}.pkl"
    if path.exists() and not refresh:
        try:
            panel = pd.read_pickle(path)
            path.touch()  # marque le fichier comme récemment utilisé
            return panel
        except Exception:
            print(f"  Cache illisible ({path.name}) : nouveau téléchargement.")
            path.unlink(missing_ok=True)
    panel = download_ohlcv(tickers, start, end)
    _write_cache(panel, path)
    return panel


def _write_cache(panel: dict[str, pd.DataFrame], path: Path) -> None:
    """Écrit le cache via un fichier temporaire, puis supprime les plus anciens.

    Un échec d'écriture (disque plein...) ne laisse aucun fichier tronqué et n'interrompt pas
    le backtest : on continue simplement sans cache.
    """
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(panel, tmp)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"  ! Cache non enregistré ({exc.strerror or exc}) : on continue sans cache.")
        return
    files = sorted(path.parent.glob("ohlcv_*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[MAX_CACHE_FILES:] + list(path.parent.glob("ohlcv_*.tmp")):
        old.unlink(missing_ok=True)
