"""Moteur de backtest journalier de la stratégie RSI (positions longues uniquement).

Déroulé de chaque séance (un seul scan d'opportunités par jour, après la clôture) :
  1. À l'ouverture : exécution des ordres d'achat générés la veille au soir, financés si besoin
     par la vente de grandes valeurs ; le cash restant est placé dans les grandes valeurs.
  2. Pendant la séance : sortie des positions dont le stop loss ou le take profit est touché.
  3. À la clôture : valorisation du portefeuille.
  4. Après la clôture : scan des actions dont le RSI est sous le seuil depuis N séances
     -> ordres d'achat pour l'ouverture du lendemain (pas de biais d'anticipation).
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import oversold_signal, rsi


TRADE_COLUMNS = ["ticker", "signal_date", "signal_rsi", "entry_date", "entry_price", "shares",
                 "exit_date", "exit_price", "exit_reason", "pnl", "return_pct", "holding_days",
                 "costs", "ambiguous", "take_profit_pct"]
OPEN_COLUMNS = ["ticker", "signal_date", "entry_date", "entry_price", "shares", "stop_loss",
                "take_profit", "last_price", "unrealized_pnl"]
ORDER_COLUMNS = ["ticker", "signal_date", "rsi", "close", "budget", "min_budget",
                 "take_profit_pct"]


@dataclass
class Position:
    ticker: str
    shares: int
    entry_date: pd.Timestamp
    entry_idx: int
    entry_price: float
    stop: float
    target: float
    signal_date: pd.Timestamp
    signal_rsi: float
    entry_fee: float
    entry_slippage: float  # coût du glissement à l'achat, en $
    take_profit_pct: float


@dataclass
class BacktestResult:
    equity: pd.DataFrame          # par date : cash, invested, core, equity, n_positions
    trades: pd.DataFrame          # positions RSI clôturées
    open_positions: pd.DataFrame  # positions RSI encore ouvertes, valorisées à la dernière clôture
    next_orders: pd.DataFrame     # signaux du dernier jour, à exécuter à l'ouverture suivante
    core_pnl: float = 0.0         # gain des grandes valeurs ($), réalisé et latent
    core_holdings: dict | None = None  # grandes valeurs détenues à la fin : ticker -> titres


def compute_signals(panel: dict[str, pd.DataFrame], cfg: StrategyConfig,
                    market_close: pd.Series | None = None,
                    price_filter: list[str] | None = None):
    """Renvoie (RSI, signaux d'achat éligibles) sous forme de DataFrames dates x tickers.

    Les bourses du monde n'ont pas les mêmes jours fériés : les indicateurs sont calculés sur le
    dernier cours connu, et un signal n'est retenu que si l'action a coté ce jour-là.
    `market_close` (cours de l'indice principal) n'est requis que si `cfg.market_ma_days` > 0.
    `price_filter` : tickers soumis au cours minimum `cfg.min_price` (par défaut, tous).
    """
    close, volume = panel["Close"], panel["Volume"]
    close_ff = close.ffill()
    rsi_values = rsi(close_ff, cfg.rsi_period)
    signal = oversold_signal(rsi_values, cfg.rsi_threshold, cfg.consecutive_days)
    window = cfg.avg_volume_window
    avg_dollar_volume = (close * volume).rolling(window, min_periods=max(1, window // 2)).mean()
    price_ok = close_ff >= cfg.min_price
    if price_filter is not None:
        price_ok.loc[:, close.columns.difference(price_filter)] = True
    eligible = (signal & close.notna() & price_ok
                & (avg_dollar_volume >= cfg.min_avg_dollar_volume))
    if cfg.trend_ma_days:
        eligible &= close_ff > close_ff.rolling(cfg.trend_ma_days,
                                                min_periods=cfg.trend_ma_days).mean()
    if cfg.market_ma_days:
        if market_close is None:
            raise ValueError("Le filtre de marché demande les cours de l'indice (market_close).")
        mkt = market_close.reindex(close.index).ffill()
        bullish = mkt > mkt.rolling(cfg.market_ma_days, min_periods=cfg.market_ma_days).mean()
        eligible = eligible.mul(bullish.astype(int), axis=0).astype(bool)
    return rsi_values, eligible


def _exit_price(pos: Position, o: float, h: float, l: float, entered_today: bool,
                cfg: StrategyConfig, slip: float) -> tuple[float | None, str | None, bool]:
    """(prix, motif, ambigu) de sortie pour la séance, ou (None, None, False) si la position reste
    ouverte. `ambigu` signale une séance où le stop et l'objectif ont tous deux été touchés."""
    if not entered_today and np.isfinite(o):
        # Gap à l'ouverture au-delà d'un des niveaux : exécution au cours d'ouverture.
        if o <= pos.stop:
            return o * (1 - slip), "stop_loss", False
        if o >= pos.target:
            return o, "take_profit", False
    hit_stop = l <= pos.stop
    hit_target = h >= pos.target
    ambiguous = hit_stop and hit_target
    if hit_stop and (not hit_target or cfg.stop_first_if_ambiguous):
        return pos.stop * (1 - slip), "stop_loss", ambiguous
    if hit_target:
        return pos.target, "take_profit", ambiguous  # ordre limite : pas de glissement
    return None, None, False


def market_is_bullish(market_close: pd.Series, dates: pd.DatetimeIndex, ma_days: int) -> np.ndarray:
    """Pour chaque date : l'indice cote-t-il au-dessus de sa moyenne mobile `ma_days` séances ?"""
    mkt = market_close.reindex(dates).ffill()
    return (mkt > mkt.rolling(ma_days, min_periods=ma_days).mean()).to_numpy()


class CoreSleeve:
    """Grandes valeurs détenues avec le cash que les achats RSI n'utilisent pas.

    Chaque mois, on retient les `size` plus grandes capitalisations parmi les candidates, estimées
    avec les cours de l'époque (titres de l'indice aujourd'hui x cours de la veille) : on ne
    choisit donc pas les gagnantes connues aujourd'hui. Les achats vont aux valeurs les moins
    représentées, les ventes partent des plus grosses lignes : peu d'ordres, poids proches.
    """

    def __init__(self, col: dict[str, int], index_shares: pd.Series, size: int,
                 comm: float, slip: float):
        self.col, self.size, self.comm, self.slip = col, size, comm, slip
        self.index_shares = {t: float(n) for t, n in index_shares.items() if t in col}
        self.members: list[str] = []
        self.holdings: dict[str, int] = {}
        self.cash_flow = 0.0  # ventes - achats : P&L = cash_flow + valeur détenue

    def _price(self, ticker: str, prices: np.ndarray) -> float:
        return prices[self.col[ticker]]

    def select(self, prices: np.ndarray) -> None:
        caps = {t: n * self._price(t, prices) for t, n in self.index_shares.items()
                if np.isfinite(self._price(t, prices))}
        self.members = sorted(caps, key=caps.get, reverse=True)[:self.size]

    def value(self, prices: np.ndarray) -> float:
        return sum(n * self._price(t, prices) for t, n in self.holdings.items()
                   if np.isfinite(self._price(t, prices)))

    def _sell(self, ticker: str, shares: int, price: float) -> float:
        proceeds = shares * price * (1 - self.slip) * (1 - self.comm)
        self.holdings[ticker] -= shares
        if self.holdings[ticker] == 0:
            del self.holdings[ticker]
        self.cash_flow += proceeds
        return proceeds

    def _buy(self, ticker: str, shares: int, price: float) -> float:
        cost = shares * price * (1 + self.slip) * (1 + self.comm)
        self.holdings[ticker] = self.holdings.get(ticker, 0) + shares
        self.cash_flow -= cost
        return cost

    def sell_non_members(self, prices: np.ndarray) -> float:
        """Vend les lignes sorties du classement ; renvoie le cash obtenu."""
        cash = 0.0
        for t in [t for t in self.holdings if t not in self.members]:
            if np.isfinite(self._price(t, prices)):
                cash += self._sell(t, self.holdings[t], self._price(t, prices))
        return cash

    def raise_cash(self, amount: float, prices: np.ndarray) -> float:
        """Vend des grandes valeurs, des plus grosses lignes aux plus petites, pour obtenir au
        moins `amount` ; renvoie le cash obtenu."""
        cash = 0.0
        by_value = sorted(self.holdings, key=lambda t: -self.holdings[t] * self._price(t, prices))
        for t in by_value:
            price = self._price(t, prices)
            if cash >= amount or not np.isfinite(price):
                continue
            unit = price * (1 - self.slip) * (1 - self.comm)
            shares = min(self.holdings[t], math.ceil((amount - cash) / unit))
            cash += self._sell(t, shares, price)
        return cash

    def invest(self, cash: float, min_order: float, max_value: float, prices: np.ndarray) -> float:
        """Place `cash` dans les grandes valeurs les moins représentées, par ordres d'au moins
        `min_order`, sans dépasser `max_value` par ligne ; renvoie le montant dépensé."""
        values = {t: self.holdings.get(t, 0) * self._price(t, prices) for t in self.members
                  if np.isfinite(self._price(t, prices))}
        if cash < min_order or not values:
            return 0.0
        count = max(1, min(len(values), int(cash // min_order)))
        spent = 0.0
        for t in sorted(values, key=values.get)[:count]:
            amount = min(cash / count, max_value - values[t])
            unit = self._price(t, prices) * (1 + self.slip) * (1 + self.comm)
            shares = int(amount // unit) if amount > 0 else 0
            if shares > 0:
                spent += self._buy(t, shares, self._price(t, prices))
        return spent


def run_backtest(panel: dict[str, pd.DataFrame], cfg: StrategyConfig,
                 start: str | pd.Timestamp | None = None,
                 end: str | pd.Timestamp | None = None,
                 market_close: pd.Series | None = None,
                 core_shares: pd.Series | None = None,
                 price_filter: list[str] | None = None) -> BacktestResult:
    """Simule la stratégie sur `panel` ({Open, High, Low, Close, Volume} -> DataFrame dates x tickers).

    Les données avant `start` servent uniquement au calcul des indicateurs (période de chauffe).
    `market_close` : cours de l'indice principal, requis pour le filtre de marché et le take profit
    adapté à la tendance. `core_shares` : titres de l'indice (en millions) des grandes valeurs
    candidates, dont les cours figurent dans `panel` ; sans elles, le cash reste en cash.
    `price_filter` : tickers soumis au cours minimum (voir `compute_signals`).
    """
    close = panel["Close"]
    rsi_values, eligible = compute_signals(panel, cfg, market_close, price_filter)

    dates = close.index
    mask = np.ones(len(dates), dtype=bool)
    if start is not None:
        mask &= dates >= pd.Timestamp(start)
    if end is not None:
        mask &= dates <= pd.Timestamp(end)
    day_indices = np.flatnonzero(mask)
    if len(day_indices) == 0:
        raise ValueError("Aucune séance dans la période demandée.")

    bullish = None
    if cfg.take_profit_bull_pct:
        if market_close is None:
            raise ValueError("Le take profit adapté à la tendance demande les cours de l'indice "
                             "(market_close).")
        bullish = market_is_bullish(market_close, dates, cfg.bull_ma_days)

    tickers = list(close.columns)
    col = {t: j for j, t in enumerate(tickers)}
    O = panel["Open"].to_numpy(dtype=float)
    H = panel["High"].to_numpy(dtype=float)
    L = panel["Low"].to_numpy(dtype=float)
    C_ffill = close.ffill().to_numpy(dtype=float)  # dernier cours connu, pour la valorisation
    RSI = rsi_values.to_numpy(dtype=float)
    ELIG = eligible.to_numpy(dtype=bool, copy=True)

    comm = cfg.commission_bps / 1e4
    slip = cfg.slippage_bps / 1e4

    core = None
    if cfg.core_size and core_shares is not None and len(core_shares):
        core = CoreSleeve(col, core_shares, cfg.core_size, comm, slip)
        ELIG[:, [col[t] for t in core.index_shares]] = False  # pas de signal RSI sur les grandes valeurs

    cash = cfg.initial_capital
    positions: dict[str, Position] = {}
    pending: list[dict] = []
    trades, records = [], []

    for i in day_indices:
        d = dates[i]
        prev = max(i - 1, 0)
        prev_equity = records[-1]["equity"] if records else cfg.initial_capital
        open_px = np.where(np.isfinite(O[i]), O[i], C_ffill[prev])  # à défaut, clôture de la veille

        # 0. Grandes valeurs : nouveau classement chaque mois, sur les cours de la veille.
        if core is not None and (not records or dates[prev].month != d.month):
            core.select(C_ffill[prev])
            cash += core.sell_non_members(open_px)

        # 1. Achats RSI à l'ouverture (ordres de la veille, du RSI le plus bas au plus haut),
        #    financés si besoin en vendant des grandes valeurs, sans entamer leur part réservée.
        rsi_room = math.inf
        if core is not None and cfg.core_min_pct:
            rsi_open = sum(p.shares * open_px[col[t]] for t, p in positions.items())
            rsi_room = (1 - cfg.core_min_pct) * prev_equity - rsi_open
        for order in pending:
            j = col[order["ticker"]]
            px_open = O[i, j]
            if not np.isfinite(px_open) or px_open <= 0:
                continue  # pas de cotation : l'ordre est annulé
            fill = px_open * (1 + slip)
            liquidity = cash + (core.value(open_px) if core is not None else 0.0)
            budget = min(order["budget"], liquidity, rsi_room)
            shares = int(budget // (fill * (1 + comm)))
            if shares <= 0 or budget < order["min_budget"]:
                continue
            if core is not None and shares * fill * (1 + comm) > cash:
                cash += core.raise_cash(shares * fill * (1 + comm) - cash, open_px)
                shares = min(shares, int(cash // (fill * (1 + comm))))
                if shares <= 0:
                    continue
            fee = shares * fill * comm
            cash -= shares * fill + fee
            rsi_room -= shares * fill + fee
            tp = order["take_profit_pct"]
            positions[order["ticker"]] = Position(
                ticker=order["ticker"], shares=shares, entry_date=d, entry_idx=i,
                entry_price=fill,
                stop=fill * (1 - cfg.stop_loss_pct),
                target=fill * (1 + tp),
                signal_date=order["signal_date"], signal_rsi=order["rsi"], entry_fee=fee,
                entry_slippage=shares * (fill - px_open), take_profit_pct=tp,
            )
        pending = []

        #    Le cash restant va dans les grandes valeurs.
        if core is not None:
            cash -= core.invest(cash, prev_equity * cfg.min_position_pct,
                                prev_equity * cfg.position_size_pct, open_px)

        # 2. Sorties sur stop loss / take profit.
        for t, pos in list(positions.items()):
            j = col[t]
            h, l = H[i, j], L[i, j]
            if not (np.isfinite(h) and np.isfinite(l)):
                continue  # pas de cotation aujourd'hui
            price, reason, ambiguous = _exit_price(pos, O[i, j], h, l, pos.entry_idx == i, cfg, slip)
            if price is None:
                continue
            proceeds = pos.shares * price
            fee = proceeds * comm
            exit_slippage = proceeds * slip / (1 - slip) if reason == "stop_loss" else 0.0
            cash += proceeds - fee
            pnl = proceeds - fee - pos.shares * pos.entry_price - pos.entry_fee
            trades.append({
                "ticker": t,
                "signal_date": pos.signal_date,
                "signal_rsi": pos.signal_rsi,
                "entry_date": pos.entry_date,
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "exit_date": d,
                "exit_price": price,
                "exit_reason": reason,
                "pnl": pnl,
                "return_pct": pnl / (pos.shares * pos.entry_price + pos.entry_fee),
                "holding_days": i - pos.entry_idx,
                "costs": pos.entry_fee + pos.entry_slippage + fee + exit_slippage,
                "ambiguous": ambiguous,
                "take_profit_pct": pos.take_profit_pct,
            })
            del positions[t]

        # 3. Valorisation à la clôture.
        core_value = core.value(C_ffill[i]) if core is not None else 0.0
        invested = sum(pos.shares * C_ffill[i, col[t]] for t, pos in positions.items())
        equity = cash + invested + core_value
        records.append({"date": d, "cash": cash, "invested": invested + core_value,
                        "core": core_value, "equity": equity, "n_positions": len(positions)})

        # 4. Scan quotidien des nouvelles opportunités -> ordres pour demain.
        #    Le lendemain, les ordres sont exécutés dans cet ordre tant qu'il reste du cash.
        cand = [j for j in np.flatnonzero(ELIG[i]) if tickers[j] not in positions]
        cand.sort(key=lambda j: RSI[i, j])
        if cfg.max_positions is not None:
            cand = cand[:max(cfg.max_positions - len(positions), 0)]
        tp = cfg.take_profit_bull_pct if bullish is not None and bullish[i] else cfg.take_profit_pct
        pending = [{"ticker": tickers[j], "signal_date": d, "rsi": RSI[i, j],
                    "close": C_ffill[i, j], "budget": equity * cfg.position_size_pct,
                    "min_budget": equity * cfg.min_position_pct, "take_profit_pct": tp}
                   for j in cand]

    last = day_indices[-1]
    open_rows = []
    for t, pos in positions.items():
        px = C_ffill[last, col[t]]
        open_rows.append({
            "ticker": t, "signal_date": pos.signal_date, "entry_date": pos.entry_date,
            "entry_price": pos.entry_price, "shares": pos.shares,
            "stop_loss": pos.stop, "take_profit": pos.target, "last_price": px,
            "unrealized_pnl": pos.shares * (px - pos.entry_price) - pos.entry_fee,
        })

    return BacktestResult(
        equity=pd.DataFrame(records).set_index("date"),
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        open_positions=pd.DataFrame(open_rows, columns=OPEN_COLUMNS),
        next_orders=pd.DataFrame(pending, columns=ORDER_COLUMNS),
        core_pnl=core.cash_flow + core.value(C_ffill[last]) if core is not None else 0.0,
        core_holdings=dict(core.holdings) if core is not None else None,
    )
