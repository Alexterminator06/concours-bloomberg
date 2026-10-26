"""Téléchargement des cours journaliers (OHLCV) depuis Yahoo Finance, avec une base locale."""

import logging
import os
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
# Base de cours locale (dans le dossier du cache) : environ 55 Mo par année d'historique pour
# les 10 000 actions du WLS.
STORE_VERSION = 1
MARKET_TZ = "America/New_York"
# Heure de New York à laquelle toutes les places ont clôturé (les dernières sont en Amérique).
DAY_COMPLETE = pd.Timedelta(hours=17)
# Jours repris avant le téléchargement précédent pour vérifier que l'ajustement n'a pas changé.
OVERLAP_DAYS = 14
ADJUSTMENT_TOLERANCE = 5e-4
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
               alternatives: dict[str, list[str]] | None = None,
               store: str = "actions") -> dict[str, pd.DataFrame]:
    """Comme `download_ohlcv`, mais à partir d'une base de cours locale (`cache_dir/<store>.pkl` :
    une pour les actions, de petites bases à part pour les indices et les taux de change, plus
    rapides à relire), complétée au fil des lancements : seuls les cours qui y manquent sont
    téléchargés.

    Une action est à jour si la base la couvre depuis `start` et si ses cours ont été téléchargés
    après la dernière clôture utile (`DAY_COMPLETE`, heure de New York, du lundi au vendredi,
    sans dépasser `end`). Sinon :
    - si la base ne remonte pas jusqu'à `start` (ou avec `refresh`), tout l'historique est
      téléchargé à nouveau ;
    - sinon, seules les dernières séances le sont, à partir de `OVERLAP_DAYS` jours avant le
      téléchargement précédent. Si les cours déjà connus de ces jours-là ont changé (dividende
      ou division d'actions : Yahoo recalcule tout l'historique ajusté), l'historique complet de
      l'action est téléchargé à nouveau, pour ne jamais raccorder deux ajustements différents.
    Les actions que Yahoo refuse de servir (limite de requêtes) seront redemandées au lancement
    suivant ; tout le reste est enregistré.
    """
    path = cache_dir / f"{store}.pkl"
    saved = _read_store(path)
    fields, coverage = saved["fields"], saved["coverage"]
    now = _now()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else now.normalize() + pd.Timedelta(days=1)
    needed = min(_last_close(now), end_ts - pd.Timedelta(days=1) + DAY_COMPLETE)
    tickers = list(dict.fromkeys(tickers))

    full: dict[str, pd.Timestamp] = {}   # ticker -> début du téléchargement complet
    tail: dict[str, pd.Timestamp] = {}   # ticker -> début des dernières séances à reprendre
    for t in tickers:
        known = t in coverage.index
        if refresh or not known or coverage.at[t, "debut"] > start_ts:
            full[t] = min(start_ts, coverage.at[t, "debut"]) if known else start_ts
        elif coverage.at[t, "complet"] < needed:
            tail[t] = coverage.at[t, "complet"].normalize() - pd.Timedelta(days=OVERLAP_DAYS)
    print(f"  Base de cours locale : {len(tickers) - len(full) - len(tail)} / {len(tickers)} "
          f"à jour" + (f", {len(full)} à télécharger" if full else "")
          + (f", {len(tail)} à compléter (dernières séances)" if tail else "")
          + ("" if full or tail else ", aucun téléchargement") + ".")

    if full or tail:
        fetch_end = max(end_ts, now.normalize() + pd.Timedelta(days=1)).date().isoformat()
        updates: list[tuple[list[str], dict[str, pd.DataFrame], dict[str, pd.Timestamp]]] = []
        recalculated = []
        for served, panel in _fetch_groups(tail, fetch_end, alternatives):
            changed = _adjustment_changed(fields, coverage, panel, served)
            recalculated += changed
            served = [t for t in served if t not in changed]
            updates.append((served, {f: df.drop(columns=changed) for f, df in panel.items()},
                            {t: tail[t] for t in served}))
        if recalculated:
            print(f"  {len(recalculated)} action(s) dont Yahoo a recalculé les cours ajustés "
                  f"(dividende, division) : historique complet téléchargé à nouveau.")
            full.update({t: coverage.at[t, "debut"] for t in recalculated})
        for served, panel in _fetch_groups(full, fetch_end, alternatives):
            updates.append((served, panel, {t: full[t] for t in served}))
        if updates:
            for served, panel, first in updates:
                # Une action sans cours cette fois garde ceux déjà enregistrés.
                fields = _merge(fields, panel, {t: first[t] for t in panel["Close"].columns
                                                if t in first})
            done = pd.DataFrame(
                [(t, first[t] if t in full else coverage.at[t, "debut"], now)
                 for served, _, first in updates for t in served],
                columns=["ticker", "debut", "complet"]).set_index("ticker")
            coverage = pd.concat([coverage.drop(done.index, errors="ignore"), done])
            _write_store(path, {"version": STORE_VERSION, "fields": fields, "coverage": coverage})

    close = fields.get("Close")
    cols = pd.Index([t for t in tickers if close is not None and t in close.columns])
    if not len(cols):
        raise RuntimeError("Aucune donnée téléchargée depuis Yahoo Finance.")
    window = close.loc[(close.index >= start_ts) & (close.index < end_ts), cols]
    cols = window.columns[window.notna().any()]
    rows = window.index[window[cols].notna().any(axis=1)]
    return {f: fields[f].loc[rows, cols] for f in FIELDS}


def _now() -> pd.Timestamp:
    """Heure de New York, sans fuseau (comme les dates des cours)."""
    return pd.Timestamp.now(tz=MARKET_TZ).tz_localize(None)


def _last_close(now: pd.Timestamp) -> pd.Timestamp:
    """Dernière heure `DAY_COMPLETE` passée un jour de semaine : à ce moment, toutes les places
    ont clôturé la séance du jour."""
    close = now.normalize() + DAY_COMPLETE
    if close > now:
        close -= pd.Timedelta(days=1)
    while close.dayofweek >= 5:
        close -= pd.Timedelta(days=1)
    return close


def _fetch_groups(starts: dict[str, pd.Timestamp], end: str,
                  alternatives: dict[str, list[str]] | None):
    """Télécharge les tickers de `starts`, groupés par date de début. Renvoie, pour chaque groupe,
    les tickers servis par Yahoo (avec ou sans cours) et leurs cours. Un groupe dont aucune
    action n'a de cours (Yahoo injoignable...) est laissé de côté : il sera redemandé."""
    groups: dict[pd.Timestamp, list[str]] = {}
    for t, first in starts.items():
        groups.setdefault(first, []).append(t)
    for first, group in sorted(groups.items()):
        report: dict = {}
        alts = {t: alternatives[t] for t in group if t in (alternatives or {})}
        try:
            panel = download_ohlcv(group, first.date().isoformat(), end, alternatives=alts,
                                   report=report)
        except RuntimeError:
            continue
        refused = report.get("refused", set())
        yield [t for t in group if t.upper() not in refused], panel


def _adjustment_changed(fields: dict[str, pd.DataFrame], coverage: pd.DataFrame,
                        panel: dict[str, pd.DataFrame], tickers: list[str]) -> list[str]:
    """Tickers dont les clôtures déjà enregistrées (séances terminées lors du téléchargement
    précédent) diffèrent des nouvelles : Yahoo a recalculé leur historique ajusté."""
    old, new = fields.get("Close"), panel["Close"]
    if old is None:
        return []
    cols = [t for t in tickers if t in old.columns and t in new.columns]
    if not cols:
        return []
    done = (coverage.loc[cols, "complet"] - DAY_COMPLETE).dt.normalize()
    dates = new.index.intersection(old.index)
    before = old.loc[dates, cols].to_numpy(dtype=float)
    after = new.loc[dates, cols].to_numpy(dtype=float)
    comparable = (dates.to_numpy()[:, None] <= done.to_numpy()[None, :]) & (before > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = np.abs(after / before - 1)
    differs = np.where(comparable & np.isfinite(gap), gap > ADJUSTMENT_TOLERANCE, False)
    return [t for t, d in zip(cols, differs.any(axis=0)) if d]


def _merge(fields: dict[str, pd.DataFrame], panel: dict[str, pd.DataFrame],
           clear_from: dict[str, pd.Timestamp]) -> dict[str, pd.DataFrame]:
    """Range dans la base les cours téléchargés : pour chaque ticker de `clear_from`, les cours
    enregistrés à partir de cette date sont remplacés par les nouveaux (effacés s'il n'y en a
    plus)."""
    if not clear_from:
        return fields
    out = {}
    for f in FIELDS:
        old = fields.get(f, pd.DataFrame(index=pd.DatetimeIndex([]), dtype="float32"))
        new = panel[f]
        index = old.index.union(new.index)
        columns = old.columns.append(new.columns.difference(old.columns, sort=False))
        if index.equals(old.index) and columns.equals(old.columns):
            values = old.to_numpy(dtype="float32", copy=True)
        else:
            values = old.reindex(index=index, columns=columns).to_numpy(dtype="float32", copy=True)
        position = pd.Series(np.arange(len(columns)), index=columns)
        dates = index.to_numpy()
        for first, group in pd.Series(clear_from).groupby(pd.Series(clear_from)):
            values[np.ix_(dates >= first.to_datetime64(), position[group.index].to_numpy())] = np.nan
        if len(new.columns):
            rows = index.get_indexer(new.index)
            values[np.ix_(rows, position[new.columns].to_numpy())] = new.to_numpy(dtype="float32")
        out[f] = pd.DataFrame(values, index=index, columns=columns)
    return out


def _empty_store() -> dict:
    coverage = pd.DataFrame({"debut": pd.Series(dtype="datetime64[ns]"),
                             "complet": pd.Series(dtype="datetime64[ns]")})
    return {"version": STORE_VERSION, "fields": {}, "coverage": coverage}


def _read_store(path: Path) -> dict:
    """Base de cours locale ; vide si elle n'existe pas ou est illisible. Supprime les fichiers
    de l'ancien cache (un fichier par requête), remplacés par la base."""
    old = list(path.parent.glob("ohlcv_*.pkl")) + list(path.parent.glob("ohlcv_*.tmp"))
    if old:
        size = sum(p.stat().st_size for p in old)
        for p in old:
            p.unlink(missing_ok=True)
        print(f"  Ancien cache des cours supprimé ({size / 1e9:.1f} Go) : remplacé par la base "
              f"de cours locale.")
    if not path.exists():
        return _empty_store()
    try:
        store = pd.read_pickle(path)
        if store.get("version") == STORE_VERSION:
            return store
    except Exception:
        pass
    print(f"  Base de cours illisible ({path.name}) : elle est reconstruite.")
    path.unlink(missing_ok=True)
    return _empty_store()


def _write_store(path: Path, store: dict) -> None:
    """Écrit la base via un fichier temporaire. Un échec d'écriture (disque plein...) ne laisse
    aucun fichier tronqué et n'interrompt pas le lancement : on continue sans enregistrer."""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(store, tmp)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"  ! Base de cours non enregistrée ({exc.strerror or exc}) : on continue sans.")


def _sector(symbol: str) -> str | None:
    """Secteur Yahoo d'un symbole, « » s'il n'en a pas ; None si Yahoo limite les requêtes
    malgré les nouvelles tentatives."""
    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError

    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return str(yf.Ticker(symbol).get_info().get("sector") or "")
        except YFRateLimitError:
            if attempt == len(RETRY_WAITS):
                return None
            print(f"    Yahoo limite les requêtes : pause de {RETRY_WAITS[attempt]} s...")
            time.sleep(RETRY_WAITS[attempt])
        except Exception:  # symbole inconnu de Yahoo, réponse incomplète...
            return ""
    return None


def fetch_sectors(symbols: list[str],
                  alternatives: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Secteur Yahoo Finance de chaque action (« Technology », « Healthcare »...), en essayant
    ses autres écritures (`alternatives`) si besoin ; « » si Yahoo n'en donne pas. Les actions
    que Yahoo refuse de servir (limite de requêtes) sont absentes."""
    print(f"Secteurs de {len(symbols)} action(s) (Yahoo Finance) ...")
    yf_logger = logging.getLogger("yfinance")
    saved = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)  # pas une ligne d'erreur par symbole inconnu
    sectors: dict[str, str] = {}
    try:
        for k, symbol in enumerate(symbols):
            for candidate in [symbol, *(alternatives or {}).get(symbol, [])]:
                sector = _sector(candidate)
                if sector is not None:
                    sectors[symbol] = sector
                if sector:
                    break
            if (k + 1) % 50 == 0:
                print(f"  {k + 1} / {len(symbols)}")
    finally:
        yf_logger.setLevel(saved)
    return sectors


def load_sectors(symbols: list[str], path: Path, fetch=fetch_sectors) -> dict[str, str]:
    """Secteurs de `symbols`, lus dans `path` (colonnes symbol, secteur ; modifiable à la main) ;
    ceux qui manquent ou sont vides sont demandés à Yahoo et ajoutés au fichier."""
    known = {}
    if path.exists():
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        known = dict(zip(table["symbol"], table["secteur"]))
    missing = [s for s in symbols if not known.get(s)]
    if missing:
        fetched = fetch(missing)
        if len(fetched) < len(missing):
            print(f"  ! Secteur inconnu pour {len(missing) - len(fetched)} action(s) (Yahoo a "
                  f"limité les requêtes) : pas de plafond pour elles cette fois.")
        known.update(fetched)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(sorted(known.items()), columns=["symbol", "secteur"]).to_csv(path, index=False)
    return {s: known[s] for s in symbols if s in known}


# Un cours qui saute d'au moins ce facteur en une séance et revient dès la suivante est une erreur
# de données (LTM.SN le 22 décembre 2016 : 90 fois son cours, un seul jour), pas un vrai mouvement.
SPIKE_FACTOR = 3.0


def remove_price_spikes(panel: dict[str, pd.DataFrame],
                        factor: float = SPIKE_FACTOR) -> tuple[dict[str, pd.DataFrame], int]:
    """Efface (NaN) les séances dont la clôture s'écarte d'au moins `factor` à la fois de la
    clôture précédente et de la suivante, dans le même sens : un pic d'un jour qui revient aussitôt.
    Les indicateurs reprennent alors le dernier cours connu. Renvoie le panel et le nombre de
    séances effacées."""
    close = panel["Close"]
    before = close.ffill().shift(1)
    after = close.bfill().shift(-1)
    up = (close >= factor * before) & (close >= factor * after)
    down = (close * factor <= before) & (close * factor <= after)
    spikes = up | down
    count = int(spikes.to_numpy().sum())
    if not count:
        return panel, 0
    return {f: df.mask(spikes) if f != "Volume" else df for f, df in panel.items()}, count
