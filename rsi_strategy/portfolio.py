"""Portefeuille réel (positions et cash) et ordres d'achat du jour, pour le scan quotidien."""

import io
import math
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import StrategyConfig

CASH_TICKER = "CASH"
# Les achats sont exécutés à l'ouverture, à un cours encore inconnu la veille au soir. On calcule
# les quantités comme si l'action ouvrait 2 % plus haut que la dernière clôture : la position reste
# sous la limite de taille et le total des achats sous le cash disponible.
PRICE_BUFFER = 0.02
ORDER_COLUMNS = ["rsi", "dernier_cours", "quantite", "montant_estime", "poids_estime"]
CORE_COLUMNS = ["dernier_cours", "quantite", "montant_estime"]
CORE_LABEL = "grande valeur"


@dataclass
class Portfolio:
    cash: float
    # index : ticker ; colonnes : quantite, montant_usd, prix_achat (NaN si inconnu). Une position
    # donnée par son montant en dollars (montant_usd) n'a pas de quantité : le scan la déduit du
    # dernier cours.
    positions: pd.DataFrame


@dataclass
class OrderPlan:
    equity: float               # valeur totale estimée du portefeuille
    positions: pd.DataFrame     # positions valorisées à la dernière clôture (colonne `type`)
    orders: pd.DataFrame        # achats RSI à passer à l'ouverture, dans l'ordre
    take_profit_pct: float      # objectif des achats RSI du jour, selon la tendance du marché
    core_sells: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CORE_COLUMNS))
    core_buys: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CORE_COLUMNS))
    skipped: list[str] = field(default_factory=list)   # signaux non retenus faute de cash
    unpriced: list[str] = field(default_factory=list)  # positions sans cours récent


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return name.strip().lower().replace(" ", "_")


def _parse_number(value) -> float:
    """Accepte « 1234.5 », « 1 234,5 » (Excel en français) ou « 1,234.5 »."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    text = str(value)
    for char in (" ", " ", " ", "$", "€"):
        text = text.replace(char, "")
    if not text:
        return math.nan
    text = text.replace(",", "") if "." in text else text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"valeur numérique invalide : « {value} »") from None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:  # fichier enregistré par Excel sous Windows
        return path.read_text(encoding="cp1252")


AMOUNT_COLUMNS = ("montant_usd", "montant", "valeur_usd", "valeur")


def load_portfolio(path: Path) -> Portfolio:
    """Lit le portefeuille : colonne `ticker`, puis `montant_usd` (valeur de la position en
    dollars) ou `quantite` (nombre d'actions), et en option `prix_achat` (devise de cotation).

    Une ligne dont le ticker est `CASH` donne le cash disponible, en dollars, dans la colonne
    `montant_usd` ou `quantite`. Les séparateurs `,` et `;` sont acceptés, ainsi que la virgule
    décimale.
    """
    text = _read_text(path)
    header = text.splitlines()[0] if text.strip() else ""
    sep = ";" if ";" in header else "\t" if "\t" in header else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, skipinitialspace=True)
    df.columns = [_normalize(c) for c in df.columns]
    amount_column = next((c for c in AMOUNT_COLUMNS if c in df.columns), None)
    if amount_column:
        df = df.rename(columns={amount_column: "montant_usd"})
    if "ticker" not in df.columns or not {"quantite", "montant_usd"} & set(df.columns):
        raise ValueError(f"{path.name} : colonnes attendues « ticker, montant_usd, prix_achat » "
                         f"(ou « quantite » au lieu de « montant_usd »).")

    df["ticker"] = df["ticker"].fillna("").str.strip().str.upper()
    df = df[df["ticker"] != ""].copy()
    try:
        for column in ("quantite", "montant_usd", "prix_achat"):
            df[column] = df[column].map(_parse_number) if column in df else math.nan
    except ValueError as exc:
        raise ValueError(f"{path.name} : {exc}") from None

    is_cash = df["ticker"] == CASH_TICKER
    if not is_cash.any():
        raise ValueError(f"{path.name} : ajoutez une ligne « CASH » avec votre cash disponible.")
    # Une quantité donnée l'emporte sur le montant.
    df.loc[df["quantite"].notna(), "montant_usd"] = math.nan
    held = (df["quantite"] > 0) | (df["montant_usd"] > 0)
    positions = df.loc[~is_cash & held, ["ticker", "quantite", "montant_usd", "prix_achat"]]
    duplicated = positions["ticker"][positions["ticker"].duplicated()].unique()
    if len(duplicated):
        raise ValueError(f"{path.name} : ticker présent plusieurs fois : {', '.join(duplicated)}.")
    cash = df.loc[is_cash, "quantite"].fillna(df.loc[is_cash, "montant_usd"])
    return Portfolio(cash=float(cash.sum()), positions=positions.set_index("ticker"))


def plan_orders(signals: pd.DataFrame, portfolio: Portfolio, last_prices: pd.Series,
                cfg: StrategyConfig, core_members: list[str] = (), core_universe: list[str] = (),
                bullish: bool = False) -> OrderPlan:
    """Ordres de l'ouverture suivante selon le cash réel et les positions déjà détenues.

    `signals` : index ticker, colonnes `rsi` et `dernier_cours`, triées par RSI croissant.
    `core_members` : grandes valeurs où placer le cash restant ; `core_universe` : tickers à
    traiter comme des grandes valeurs s'ils sont détenus (ceux qui ne sont plus dans
    `core_members` sont vendus). `bullish` : tendance haussière du marché (take profit relevé).

    Mêmes règles que le backtest : chaque achat RSI vaut au plus `position_size_pct` de la valeur
    totale du portefeuille, les signaux sont pris dans l'ordre tant qu'il reste du cash (grandes
    valeurs comprises, vendues en premier des plus grosses lignes), on n'ouvre pas de position
    inférieure à `min_position_pct`, et le cash restant va aux grandes valeurs les moins
    représentées.
    """
    pos = portfolio.positions.copy()
    if "montant_usd" not in pos:
        pos["montant_usd"] = math.nan
    pos["dernier_cours"] = last_prices.reindex(pos.index)
    unpriced = list(pos.index[pos["dernier_cours"].isna()])
    # Positions données en dollars : nombre d'actions estimé au dernier cours, arrondi en dessous
    # pour ne jamais vendre plus que ce qui est détenu.
    from_amount = pos["quantite"].isna() & pos["montant_usd"].notna()
    estimated = (pos["montant_usd"] / pos["dernier_cours"]).apply(
        lambda q: math.floor(q + 1e-3) if pd.notna(q) else q)
    pos["quantite"] = pos["quantite"].fillna(estimated)
    pos["dernier_cours"] = pos["dernier_cours"].fillna(pos["prix_achat"])  # à défaut, prix d'achat
    pos["valeur"] = pos["quantite"] * pos["dernier_cours"]
    # Montant donné : c'est la valeur de la position (sans cours, la seule connue).
    pos.loc[from_amount, "valeur"] = pos.loc[from_amount, "montant_usd"]
    equity = portfolio.cash + pos["valeur"].sum()  # sum() ignore les positions sans aucun prix
    if equity <= 0:
        raise ValueError("Portefeuille vide : indiquez votre cash sur la ligne CASH.")
    pos["poids"] = pos["valeur"] / equity
    is_core = pos.index.isin(list(core_universe) + list(core_members))
    pos["type"] = [CORE_LABEL if c else "RSI" for c in is_core]
    pos["stop_loss"] = (pos["prix_achat"] * (1 - cfg.stop_loss_pct)).where(~is_core)
    take_profit = (cfg.take_profit_bull_pct if bullish and cfg.take_profit_bull_pct
                   else cfg.take_profit_pct)

    price = last_prices.to_dict()
    price.update(pos["dernier_cours"].to_dict())
    members = [t for t in core_members if pd.notna(price.get(t))]
    sell_unit = 1 - PRICE_BUFFER
    buy_unit = (1 + PRICE_BUFFER) * (1 + cfg.commission_bps / 1e4)
    target = equity * cfg.position_size_pct
    minimum = equity * cfg.min_position_pct

    # 1. Grandes valeurs sorties du classement : vendues.
    sells: dict[str, int] = {}
    cash = portfolio.cash
    kept: dict[str, float] = {}
    for t, row in pos[is_core].iterrows():
        if t not in members:
            sells[t] = int(row["quantite"])
            cash += row["valeur"] * sell_unit
            continue
        kept[t] = row["valeur"]
        # Limite du concours : une grande valeur montée au-dessus de 20 % est ramenée à 20 %.
        if row["valeur"] > target and pd.notna(row["quantite"]):
            quantity = min(int(row["quantite"]), math.ceil((row["valeur"] - target) / price[t]))
            if quantity > 0:
                sells[t] = quantity
                cash += quantity * price[t] * sell_unit
                kept[t] -= quantity * price[t]

    # 2. Achats RSI, financés par le cash puis par les grandes valeurs.
    candidates = signals.drop(index=pos.index, errors="ignore")
    if cfg.max_positions is not None:
        candidates = candidates.head(max(cfg.max_positions - int((~is_core).sum()), 0))
    liquidity = cash + sum(kept.values()) * sell_unit
    # Part réservée aux grandes valeurs : les achats RSI n'utilisent que le reste.
    rsi_room = math.inf
    # (aussi quand les grandes valeurs sont passées en cash : leur part ne sert pas aux achats RSI)
    if cfg.core_min_pct and (members or len(core_universe)):
        rsi_room = (1 - cfg.core_min_pct) * equity - pos.loc[~is_core, "valeur"].sum()
    spent = 0.0
    rows, skipped = {}, []
    for ticker, sig in candidates.iterrows():
        budget = min(target, liquidity, rsi_room)
        quantity = int(budget // (sig["dernier_cours"] * buy_unit)) if budget >= minimum else 0
        if quantity <= 0:
            skipped.append(ticker)
            continue
        liquidity -= quantity * sig["dernier_cours"] * buy_unit
        rsi_room -= quantity * sig["dernier_cours"] * buy_unit
        spent += quantity * sig["dernier_cours"] * buy_unit
        amount = quantity * sig["dernier_cours"]
        rows[ticker] = [sig["rsi"], sig["dernier_cours"], quantity, amount, amount / equity]

    # 3. Financement : on vend des grandes valeurs, des plus grosses lignes aux plus petites.
    for t in sorted(kept, key=kept.get, reverse=True):
        if cash >= spent:
            break
        held = int(pos.loc[t, "quantite"]) - sells.get(t, 0)
        quantity = min(held, math.ceil((spent - cash) / (price[t] * sell_unit)))
        sells[t] = sells.get(t, 0) + quantity
        cash += quantity * price[t] * sell_unit
        kept[t] -= quantity * price[t]
    cash -= spent

    # 4. Le cash restant va aux grandes valeurs les moins représentées.
    buys: dict[str, int] = {}
    if members and cash >= minimum:
        values = {t: kept.get(t, 0.0) for t in members}
        count = max(1, min(len(values), int(cash // minimum)))
        for t in sorted(values, key=values.get)[:count]:
            amount = min(cash / count, target - values[t])
            quantity = int(amount // (price[t] * buy_unit)) if amount > 0 else 0
            if quantity > 0:
                buys[t] = quantity

    def table(quantities: dict[str, int]) -> pd.DataFrame:
        return pd.DataFrame.from_dict(
            {t: [price[t], q, q * price[t]] for t, q in quantities.items()},
            orient="index", columns=CORE_COLUMNS)

    orders = pd.DataFrame.from_dict(rows, orient="index", columns=ORDER_COLUMNS)
    return OrderPlan(equity=equity, positions=pos, orders=orders, take_profit_pct=take_profit,
                     core_sells=table(sells), core_buys=table(buys), skipped=skipped,
                     unpriced=unpriced)
