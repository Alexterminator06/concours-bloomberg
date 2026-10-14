"""Téléchargement des cours journaliers (OHLCV) depuis Yahoo Finance, avec cache local."""

import hashlib
import logging
import os
from pathlib import Path

import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MAX_CACHE_FILES = 4  # fichiers de cache conservés (les plus récemment utilisés), ~90 Mo chacun


def _download_chunks(tickers: list[str], start: str, end: str | None,
                     chunk_size: int) -> dict[str, list[pd.DataFrame]]:
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
    return frames


def download_ohlcv(tickers: list[str], start: str, end: str | None = None,
                   chunk_size: int = 200,
                   alternatives: dict[str, list[str]] | None = None) -> dict[str, pd.DataFrame]:
    """Télécharge les cours ajustés (splits + dividendes) et renvoie un dict
    {champ: DataFrame dates x tickers} pour les champs Open, High, Low, Close, Volume, en devise
    de cotation. `alternatives` : autres symboles Yahoo à essayer pour un ticker sans cours ; les
    cours trouvés sont rangés sous le ticker d'origine.
    """
    frames = _download_chunks(tickers, start, end, chunk_size)
    found = {t for part in frames["Close"] for t in part.columns[part.notna().any()]}
    retry = {alt: t for t in tickers if t not in found for alt in (alternatives or {}).get(t, [])}
    if retry:
        print(f"  Nouvel essai pour {len(set(retry.values()))} action(s) sous une autre écriture :")
        extra = _download_chunks(list(retry), start, end, chunk_size)
        for f in FIELDS:
            frames[f] += [part.rename(columns=retry) for part in extra[f]]

    if not frames["Close"]:
        raise RuntimeError("Aucune donnée téléchargée depuis Yahoo Finance.")

    panel = {}
    for f in FIELDS:
        df = pd.concat(frames[f], axis=1)
        df = df.loc[:, df.notna().any()]  # une écriture sans cours ne masque pas celle qui en a
        df = df.loc[:, ~df.columns.duplicated()]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        panel[f] = df.sort_index()

    # On écarte les tickers sans aucune donnée et les jours sans aucune cotation.
    valid_cols = panel["Close"].columns[panel["Close"].notna().any()]
    missing = len(set(tickers) - set(valid_cols))
    if missing:
        print(f"  {missing} action(s) sans cours sur la période (cotées plus tard, retirées de la "
              f"cote ou introuvables sur Yahoo) : ignorées.")
    valid_rows = panel["Close"].index[panel["Close"].notna().any(axis=1)]
    # float32 : deux fois moins de mémoire et de disque pour 10 000 actions, précision suffisante.
    return {f: df.reindex(columns=valid_cols).loc[valid_rows].astype("float32")
            for f, df in panel.items()}


def fx_tickers(currencies) -> dict[str, str]:
    """Devise -> symbole Yahoo du cours du dollar dans cette devise (« JPY » -> « JPY=X »)."""
    return {c: f"{c}=X" for c in sorted(set(currencies)) if c != "USD"}


def to_usd(panel: dict[str, pd.DataFrame], currency: pd.Series, unit: pd.Series,
           fx_close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Convertit les cours (Open, High, Low, Close) en dollars.

    `currency` / `unit` : devise et unité de cotation de chaque ticker (0.01 pour les pence) ;
    `fx_close` : cours du dollar dans chaque devise (colonnes « JPY=X »...), au jour le jour.
    Les tickers dont la devise n'a pas de cours de change sont écartés.
    """
    close = panel["Close"]
    fx = fx_close.reindex(close.index).ffill().bfill()
    fx["USD=X"] = 1.0
    rate_column = pd.Series([f"{currency.get(t, 'USD')}=X" for t in close.columns],
                            index=close.columns)
    available = {c for c in fx.columns if fx[c].notna().any()}
    keep = rate_column.index[rate_column.isin(available)]
    if len(keep) < len(close.columns):
        print(f"  {len(close.columns) - len(keep)} action(s) sans taux de change disponible : "
              f"ignorées.")
    factor = (unit.reindex(keep).fillna(1.0).to_numpy()
              / fx[rate_column[keep]].to_numpy())  # unité de cotation -> dollars, jour par jour
    out = {}
    for f, df in panel.items():
        values = df[keep].to_numpy(dtype="float64")
        if f != "Volume":
            values = values * factor
        out[f] = pd.DataFrame(values.astype("float32"), index=df.index, columns=keep)
    return out


def load_ohlcv(tickers: list[str], start: str, end: str | None, cache_dir: Path,
               refresh: bool = False,
               alternatives: dict[str, list[str]] | None = None) -> dict[str, pd.DataFrame]:
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
    panel = download_ohlcv(tickers, start, end, alternatives=alternatives)
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
