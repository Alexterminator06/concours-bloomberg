"""Membres de l'indice WLS (Bloomberg World Large, Mid & Small Cap), l'indice du concours.

La liste s'exporte depuis le terminal Bloomberg (`WLS Index MEMB <GO>`, puis export Excel) avec
les colonnes Ticker, Name, Weight, Shares et Price. Seules les actions membres du WLS sont
autorisées dans le concours. Les tickers Bloomberg (« 7203 JT Equity ») sont convertis en
symboles Yahoo Finance (« 7203.T »), avec la devise de cotation de chaque place.
"""

from pathlib import Path

import pandas as pd

# Code de place Bloomberg -> (suffixe Yahoo, devise, unité de cotation en devise).
# Londres, Johannesburg et Tel-Aviv cotent en centièmes (pence, cents, agorot), le Koweït en fils.
EXCHANGES = {
    **{code: ("", "USD", 1.0) for code in ("UN", "UW", "UQ", "UR", "UA", "UF", "UP", "UV", "US",
                                           "VT")},
    "JT": (".T", "JPY", 1.0), "TT": (".TW", "TWD", 1.0), "LN": (".L", "GBP", 0.01),
    "CT": (".TO", "CAD", 1.0), "HK": (".HK", "HKD", 1.0), "KP": (".KS", "KRW", 1.0),
    "KQ": (".KQ", "KRW", 1.0), "FP": (".PA", "EUR", 1.0), "SE": (".SW", "CHF", 1.0),
    "GY": (".DE", "EUR", 1.0), "IS": (".NS", "INR", 1.0), "IB": (".BO", "INR", 1.0),
    "AT": (".AX", "AUD", 1.0), "C1": (".SS", "CNY", 1.0), "C2": (".SZ", "CNY", 1.0),
    "NA": (".AS", "EUR", 1.0), "SQ": (".MC", "EUR", 1.0), "SS": (".ST", "SEK", 1.0),
    "IM": (".MI", "EUR", 1.0), "BS": (".SA", "BRL", 1.0), "SP": (".SI", "SGD", 1.0),
    "AB": (".SR", "SAR", 1.0), "SJ": (".JO", "ZAR", 0.01), "DC": (".CO", "DKK", 1.0),
    "IT": (".TA", "ILS", 0.01), "MF": (".MX", "MXN", 1.0), "FH": (".HE", "EUR", 1.0),
    "BB": (".BR", "EUR", 1.0), "TB": (".BK", "THB", 1.0), "NO": (".OL", "NOK", 1.0),
    "MK": (".KL", "MYR", 1.0), "PW": (".WA", "PLN", 1.0), "AV": (".VI", "EUR", 1.0),
    "IJ": (".JK", "IDR", 1.0), "ID": (".IR", "EUR", 1.0), "TI": (".IS", "TRY", 1.0),
    "CC": (".SN", "CLP", 1.0), "KK": (".KW", "KWD", 0.001), "GA": (".AT", "EUR", 1.0),
    "QD": (".QA", "QAR", 1.0), "PM": (".PS", "PHP", 1.0), "PL": (".LS", "EUR", 1.0),
    "NZ": (".NZ", "NZD", 1.0), "HB": (".BD", "HUF", 1.0), "CX": (".CL", "COP", 1.0),
    "EC": (".CA", "EGP", 1.0), "CK": (".PR", "CZK", 1.0), "PE": (".LM", "PEN", 1.0),
}
US_EXCHANGES = {code for code, (_, currency, _) in EXCHANGES.items() if currency == "USD"}
NORDIC = {"SS", "DC", "FH", "NO"}  # Bloomberg y colle la catégorie d'actions : INVEB = INVE-B
COLUMNS = ["symbol", "bloomberg", "name", "exchange", "currency", "unit", "weight",
           "index_shares_m", "price", "index_mcap", "mcap_usd"]


def _split(ticker: str) -> tuple[str, str] | None:
    parts = str(ticker).upper().replace(" EQUITY", "").split()
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def bloomberg_to_yahoo(ticker: str) -> str | None:
    """« 7203 JT Equity » -> « 7203.T », « BRK/B UN » -> « BRK-B », « 700 HK » -> « 0700.HK » ;
    None si la place n'est pas reconnue."""
    split = _split(ticker)
    if split is None or split[1] not in EXCHANGES:
        return None
    code, exchange = split
    code = code.rstrip("/").replace("/", "-")
    if exchange == "HK":
        code = code.zfill(4)
    return code + EXCHANGES[exchange][0]


def alternative_symbols(symbol: str, exchange: str) -> list[str]:
    """Autres écritures possibles chez Yahoo, essayées si le symbole principal n'a pas de cours."""
    code, _, suffix = symbol.rpartition(".") if "." in symbol else (symbol, "", "")
    alternatives = []
    if exchange == "TT":
        alternatives.append(f"{code}.TWO")  # Taipei Exchange (ex-GreTai)
    if exchange in NORDIC and len(code) >= 4 and code[-1] in "ABCDR" and "-" not in code:
        alternatives.append(f"{code[:-1]}-{code[-1]}.{suffix}")
    return alternatives


def read_bloomberg_export(path: Path) -> pd.DataFrame:
    """Lit l'export Bloomberg (Excel ou CSV) et renvoie les membres reconnus, du plus gros poids
    au plus petit. `index_mcap` est la capitalisation retenue par l'indice, en devise locale ;
    `mcap_usd` l'estime en dollars à partir du poids (proportionnel à la capitalisation en
    dollars), étalonné sur les actions américaines."""
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
    df["exchange"] = df["bloomberg"].map(lambda t: (_split(t) or ("", ""))[1])
    df["symbol"] = df["bloomberg"].map(bloomberg_to_yahoo)
    df = df.dropna(subset=["symbol", "weight", "index_shares_m", "price"])
    df["currency"] = df["exchange"].map(lambda e: EXCHANGES[e][1])
    df["unit"] = df["exchange"].map(lambda e: EXCHANGES[e][2])
    df["index_mcap"] = df["index_shares_m"] * df["price"] * 1e6
    us = df["currency"] == "USD"
    usd_per_weight = (df.loc[us, "index_mcap"] / df.loc[us, "weight"]).median() if us.any() else 0.0
    df["mcap_usd"] = df["weight"] * usd_per_weight
    df = df.drop_duplicates("symbol").sort_values("weight", ascending=False)
    return df[COLUMNS].reset_index(drop=True)


def import_wls(source: Path, destination: Path) -> tuple[pd.DataFrame, int]:
    """Convertit l'export Bloomberg en `destination` (CSV des membres reconnus).

    Renvoie les membres et le nombre de lignes de l'export (pour compter les non reconnus)."""
    raw_count = len(pd.read_excel(source) if source.suffix.lower() in {".xlsx", ".xls"}
                    else pd.read_csv(source))
    members = read_bloomberg_export(source)
    if members.empty:
        raise ValueError(f"{source.name} : aucune action reconnue.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    members.to_csv(destination, index=False)
    return members, raw_count


def load_wls(path: Path) -> pd.DataFrame | None:
    """Membres du WLS enregistrés par `import_wls`, ou None si le fichier n'existe pas."""
    if not path.exists():
        return None
    members = pd.read_csv(path, dtype={"symbol": str, "bloomberg": str})
    if "currency" not in members.columns:  # fichier importé avant la prise en charge du monde
        raise ValueError(f"{path.name} : liste importée avec une ancienne version, relancez "
                         f"import-wls.")
    return members


def us_small_mid_universe(members: pd.DataFrame, min_mcap: float, max_mcap: float) -> pd.DataFrame:
    """Small & mid caps américaines : capitalisation de l'indice dans [min, max] dollars."""
    mask = (members["currency"] == "USD") & members["mcap_usd"].between(min_mcap, max_mcap)
    return members.loc[mask].reset_index(drop=True)


def largest(members: pd.DataFrame, n: int, above: float = 0.0) -> pd.DataFrame:
    """Les `n` plus grandes capitalisations (plus gros poids dans l'indice) au-dessus de `above`
    dollars, une seule catégorie d'actions par société (GOOGL plutôt que GOOGL et GOOG)."""
    ranked = members[members["mcap_usd"] > above].sort_values("weight", ascending=False)
    return ranked.drop_duplicates("name").head(n).reset_index(drop=True)


def bloomberg_lookup(members: pd.DataFrame) -> dict[str, str]:
    """Ticker Bloomberg sans « Equity » (« 7203 JT ») -> symbole Yahoo (« 7203.T »)."""
    keys = members["bloomberg"].str.upper().str.replace(" EQUITY", "", regex=False).str.strip()
    return dict(zip(keys, members["symbol"]))
