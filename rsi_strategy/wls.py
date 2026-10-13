"""Membres de l'indice WLS (Bloomberg World Large, Mid & Small Cap), l'indice du concours.

La liste s'exporte depuis le terminal Bloomberg (`WLS Index MEMB <GO>`, puis export Excel) avec
les colonnes Ticker, Name, Weight, Shares et Price. Seules les actions membres du WLS sont
autorisées dans le concours ; on en garde les actions cotées aux États-Unis.
"""

from pathlib import Path

import pandas as pd

# Codes de place Bloomberg des bourses américaines (UN = NYSE, UW / UQ / UR = Nasdaq, ...).
US_EXCHANGES = {"UN", "UW", "UQ", "UR", "UA", "UF", "UP", "UV", "US"}
COLUMNS = ["symbol", "name", "weight", "index_shares_m", "price", "index_mcap", "bloomberg"]


def bloomberg_to_yahoo(ticker: str) -> str | None:
    """« BRK/B UN Equity » -> « BRK-B » ; None pour une action non cotée aux États-Unis."""
    parts = str(ticker).split()
    if len(parts) < 2 or parts[1] not in US_EXCHANGES:
        return None
    return parts[0].replace("/", "-")


def read_bloomberg_export(path: Path) -> pd.DataFrame:
    """Lit l'export Bloomberg (Excel ou CSV) et renvoie les membres US, du plus gros poids au plus
    petit. `index_mcap` est la capitalisation retenue par l'indice (titres flottants x cours)."""
    raw = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    missing = {"ticker", "weight", "shares", "price"} - set(raw.columns)
    if missing:
        raise ValueError(f"{path.name} : colonnes manquantes : {', '.join(sorted(missing))}.")

    df = pd.DataFrame({
        "bloomberg": raw["ticker"].astype(str).str.strip(),
        "name": raw["name"] if "name" in raw.columns else "",
        "weight": pd.to_numeric(raw["weight"], errors="coerce"),
        "index_shares_m": pd.to_numeric(raw["shares"], errors="coerce"),
        "price": pd.to_numeric(raw["price"], errors="coerce"),
    })
    df["symbol"] = df["bloomberg"].map(bloomberg_to_yahoo)
    df = df.dropna(subset=["symbol", "weight", "index_shares_m", "price"])
    df["index_mcap"] = df["index_shares_m"] * df["price"] * 1e6
    df = df.drop_duplicates("symbol").sort_values("weight", ascending=False)
    return df[COLUMNS].reset_index(drop=True)


def import_wls(source: Path, destination: Path) -> pd.DataFrame:
    """Convertit l'export Bloomberg en `destination` (CSV des membres US)."""
    members = read_bloomberg_export(source)
    if members.empty:
        raise ValueError(f"{source.name} : aucune action américaine trouvée.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    members.to_csv(destination, index=False)
    return members


def load_wls(path: Path) -> pd.DataFrame | None:
    """Membres US du WLS enregistrés par `import_wls`, ou None si le fichier n'existe pas."""
    return pd.read_csv(path) if path.exists() else None


def small_mid_universe(members: pd.DataFrame, min_mcap: float, max_mcap: float) -> pd.DataFrame:
    """Small & mid caps : membres dont la capitalisation de l'indice est dans [min, max]."""
    mask = members["index_mcap"].between(min_mcap, max_mcap)
    return members.loc[mask].reset_index(drop=True)


def largest(members: pd.DataFrame, n: int, above: float = 0.0) -> pd.DataFrame:
    """Les `n` plus grandes capitalisations (plus gros poids dans l'indice) supérieures à `above`,
    une seule catégorie d'actions par société (GOOGL plutôt que GOOGL et GOOG)."""
    ranked = members[members["index_mcap"] > above].sort_values("weight", ascending=False)
    return ranked.drop_duplicates("name").head(n).reset_index(drop=True)
