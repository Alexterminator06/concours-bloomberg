"""Univers d'actions small & mid caps US, récupéré via le screener de Yahoo Finance."""

from datetime import date
from pathlib import Path

import pandas as pd

from .config import StrategyConfig

PAGE_SIZE = 250  # maximum autorisé par Yahoo par requête


def fetch_small_mid_caps(cfg: StrategyConfig) -> pd.DataFrame:
    """Interroge le screener Yahoo : actions US cotées (NYSE / Nasdaq) dont la capitalisation
    est comprise entre `min_market_cap` et `max_market_cap`, avec un volume moyen suffisant.

    Retourne un DataFrame : symbol, name, exchange, market_cap, segment (small / mid).
    """
    import yfinance as yf
    from yfinance import EquityQuery as Q

    query = Q("and", [
        Q("eq", ["region", "us"]),
        Q("is-in", ["exchange", *cfg.exchanges]),
        Q("btwn", ["intradaymarketcap", cfg.min_market_cap, cfg.max_market_cap]),
        Q("gt", ["intradayprice", cfg.min_price]),
        Q("gt", ["avgdailyvol3m", cfg.min_avg_volume_3m]),
    ])

    rows, offset = [], 0
    while True:
        resp = yf.screen(query, offset=offset, size=PAGE_SIZE,
                         sortField="intradaymarketcap", sortAsc=False)
        quotes = resp.get("quotes", [])
        for q in quotes:
            if q.get("quoteType", "EQUITY") != "EQUITY":
                continue
            rows.append({
                "symbol": q["symbol"],
                "name": q.get("longName") or q.get("shortName", ""),
                "exchange": q.get("exchange", ""),
                "market_cap": q.get("marketCap"),
            })
        offset += len(quotes)
        if len(quotes) < PAGE_SIZE or offset >= resp.get("total", float("inf")):
            break

    df = pd.DataFrame(rows, columns=["symbol", "name", "exchange", "market_cap"])
    df = df.drop_duplicates("symbol")
    df["segment"] = df["market_cap"].apply(lambda c: "small" if c is not None and c < 2e9 else "mid")
    df["as_of"] = date.today().isoformat()
    return df.reset_index(drop=True)


def load_universe(cfg: StrategyConfig, path: Path, refresh: bool = False,
                  max_tickers: int | None = None) -> pd.DataFrame:
    """Charge l'univers depuis `path` s'il existe, sinon l'interroge sur Yahoo et le sauvegarde.

    Le fichier CSV peut aussi être fourni/édité à la main (seule la colonne `symbol` est requise).
    `max_tickers` garde les N premières lignes (les plus grosses capitalisations) sans tronquer le fichier.
    """
    if path.exists() and not refresh:
        df = pd.read_csv(path)
    else:
        df = fetch_small_mid_caps(cfg)
        if df.empty:
            raise RuntimeError("Le screener Yahoo n'a renvoyé aucune action.")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    if max_tickers:
        df = df.head(max_tickers)
    return df
