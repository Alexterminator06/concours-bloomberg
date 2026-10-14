"""Téléchargement des cours journaliers (OHLCV) depuis Yahoo Finance, avec cache local."""

import hashlib
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
# Place maximale du cache, en octets : les fichiers les plus récemment utilisés sont gardés.
# Un backtest mondial en occupe environ 250 Mo par période (actions), plus quelques petits
# fichiers (indices, taux de change).
MAX_CACHE_BYTES = 1_500_000_000
THREADS = 8          # requêtes simultanées vers Yahoo : au-delà, Yahoo bloque plus souvent
PAUSE = 1.0          # secondes entre deux paquets d'actions
RETRY_WAITS = (60, 120, 240)  # attentes avant de réessayer quand Yahoo bloque (« Too Many Requests »)


class _ErrorCollector(logging.Handler):
    """Récupère les erreurs de yfinance au lieu de les afficher (une ligne par action)."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _rate_limited(messages: list[str]) -> set[str]:
    """Tickers refusés par Yahoo pour excès de requêtes, d'après les erreurs de yfinance
    (« ['AAA', 'BBB']: YFRateLimitError('Too Many Requests. Rate limited...') »)."""
    tickers = set()
    for message in messages:
        if "Rate limit" in message or "Too Many Requests" in message:
            listed = re.match(r"\s*\[(.*?)\]:", message)
            tickers |= set(re.findall(r"'([^']+)'", listed.group(1))) if listed else set()
    return tickers


def _download_chunks(tickers: list[str], start: str, end: str | None,
                     chunk_size: int) -> tuple[dict[str, list[pd.DataFrame]], set[str]]:
    """Télécharge par paquets ; renvoie les cours et les tickers que Yahoo a refusé de servir
    malgré les nouvelles tentatives (limite de requêtes)."""
    import yfinance as yf

    yf_logger = logging.getLogger("yfinance")
    collector = _ErrorCollector()
    saved = yf_logger.level, yf_logger.propagate
    yf_logger.addHandler(collector)
    yf_logger.setLevel(logging.ERROR)
    yf_logger.propagate = False
    frames = {f: [] for f in FIELDS}
    refused: set[str] = set()
    try:
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            print(f"  Téléchargement {i + 1}-{i + len(chunk)} / {len(tickers)} ...")
            todo = chunk
            for attempt in range(len(RETRY_WAITS) + 1):
                collector.messages.clear()
                raw = yf.download(todo, start=start, end=end, auto_adjust=True, actions=False,
                                  group_by="column", threads=THREADS, progress=False,
                                  multi_level_index=True)
                if raw is not None and not raw.empty:
                    for f in FIELDS:
                        part = raw[f]
                        if isinstance(part, pd.Series):  # un seul ticker
                            part = part.to_frame(todo[0])
                        frames[f].append(part)
                todo = sorted(_rate_limited(collector.messages) & {t.upper() for t in todo})
                if not todo:
                    break
                if attempt < len(RETRY_WAITS):
                    print(f"    Yahoo limite les requêtes : pause de {RETRY_WAITS[attempt]} s, "
                          f"puis nouvel essai pour {len(todo)} action(s)...")
                    time.sleep(RETRY_WAITS[attempt])
            refused |= set(todo)
            time.sleep(PAUSE)
    finally:
        yf_logger.removeHandler(collector)
        yf_logger.setLevel(saved[0])
        yf_logger.propagate = saved[1]
    return frames, refused


def download_ohlcv(tickers: list[str], start: str, end: str | None = None,
                   chunk_size: int = 200,
                   alternatives: dict[str, list[str]] | None = None,
                   report: dict | None = None) -> dict[str, pd.DataFrame]:
    """Télécharge les cours ajustés (splits + dividendes) et renvoie un dict
    {champ: DataFrame dates x tickers} pour les champs Open, High, Low, Close, Volume, en devise
    de cotation, du lundi au vendredi. `alternatives` : autres symboles Yahoo à essayer pour un
    ticker sans cours ; les cours trouvés sont rangés sous le ticker d'origine. `report` reçoit
    sous la clé "refused" les tickers que Yahoo a refusé de servir (limite de requêtes).
    """
    frames, refused = _download_chunks(tickers, start, end, chunk_size)
    found = {t for part in frames["Close"] for t in part.columns[part.notna().any()]}
    retry = {alt: t for t in tickers
             if t not in found and t.upper() not in refused
             for alt in (alternatives or {}).get(t, [])}
    if retry:
        print(f"  Nouvel essai pour {len(set(retry.values()))} action(s) sous une autre écriture :")
        extra, refused_extra = _download_chunks(list(retry), start, end, chunk_size)
        for f in FIELDS:
            frames[f] += [part.rename(columns=retry) for part in extra[f]]
        refused |= {retry[a] for a in retry if a.upper() in refused_extra}
    if report is not None:
        report["refused"] = refused
    if refused:
        print(f"  ! {len(refused)} action(s) non téléchargées : Yahoo a limité le nombre de "
              f"requêtes. Résultats incomplets : réessayez plus tard.")

    if not frames["Close"]:
        raise RuntimeError("Aucune donnée téléchargée depuis Yahoo Finance.")

    panel = {}
    for f in FIELDS:
        df = pd.concat(frames[f], axis=1)
        df = df.loc[:, df.notna().any()]  # une écriture sans cours ne masque pas celle qui en a
        df = df.loc[:, ~df.columns.duplicated()]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        panel[f] = df.sort_index()

    # On écarte les tickers sans aucune donnée et les jours sans aucune cotation, ainsi que les
    # week-ends (quelques places du Moyen-Orient cotent le dimanche ; les indices non).
    valid_cols = panel["Close"].columns[panel["Close"].notna().any()]
    missing = len(set(tickers) - set(valid_cols) - {t for t in tickers if t.upper() in refused})
    if missing:
        print(f"  {missing} action(s) sans cours sur la période (cotées plus tard, retirées de la "
              f"cote ou introuvables sur Yahoo) : ignorées.")
    valid_rows = panel["Close"].index[panel["Close"].notna().any(axis=1)]
    valid_rows = valid_rows[valid_rows.dayofweek < 5]
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
    report: dict = {}
    panel = download_ohlcv(tickers, start, end, alternatives=alternatives, report=report)
    if report.get("refused"):
        print("  Cache non enregistré : téléchargement incomplet.")
    else:
        _write_cache(panel, path)
    return panel


def _write_cache(panel: dict[str, pd.DataFrame], path: Path) -> None:
    """Écrit le cache via un fichier temporaire, puis supprime les plus anciens au-delà de
    `MAX_CACHE_BYTES`. La limite porte sur la place occupée, pas sur le nombre de fichiers : chaque
    backtest écrit aussi de petits fichiers (indices, taux de change), qui ne doivent pas chasser
    les gros fichiers des actions.

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
    total = 0
    for old in files:
        total += old.stat().st_size
        if total > MAX_CACHE_BYTES and old != path:
            old.unlink(missing_ok=True)
    for old in path.parent.glob("ohlcv_*.tmp"):
        old.unlink(missing_ok=True)
