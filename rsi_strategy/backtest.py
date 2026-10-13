"""Moteur de backtest journalier de la stratégie RSI (positions longues uniquement).

Déroulé de chaque séance (un seul scan d'opportunités par jour, après la clôture) :
  1. À l'ouverture : exécution des ordres d'achat générés la veille au soir.
  2. Pendant la séance : sortie des positions dont le stop loss ou le take profit est touché.
  3. À la clôture : valorisation du portefeuille.
  4. Après la clôture : scan des actions dont le RSI est sous le seuil depuis N séances
     -> ordres d'achat pour l'ouverture du lendemain (pas de biais d'anticipation).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import oversold_signal, rsi


TRADE_COLUMNS = ["ticker", "signal_date", "signal_rsi", "entry_date", "entry_price", "shares",
                 "exit_date", "exit_price", "exit_reason", "pnl", "return_pct", "holding_days",
                 "costs", "ambiguous"]
OPEN_COLUMNS = ["ticker", "signal_date", "entry_date", "entry_price", "shares", "stop_loss",
                "take_profit", "last_price", "unrealized_pnl"]
ORDER_COLUMNS = ["ticker", "signal_date", "rsi", "close", "budget", "min_budget"]


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


@dataclass
class BacktestResult:
    equity: pd.DataFrame          # par date : cash, invested, equity, n_positions
    trades: pd.DataFrame          # positions clôturées
    open_positions: pd.DataFrame  # positions encore ouvertes à la fin, valorisées à la dernière clôture
    next_orders: pd.DataFrame     # signaux du dernier jour, à exécuter à l'ouverture suivante


def compute_signals(panel: dict[str, pd.DataFrame], cfg: StrategyConfig,
                    market_close: pd.Series | None = None):
    """Renvoie (RSI, signaux d'achat éligibles) sous forme de DataFrames dates x tickers.

    `market_close` (cours de l'indice principal) n'est requis que si `cfg.market_ma_days` > 0.
    """
    close, volume = panel["Close"], panel["Volume"]
    rsi_values = rsi(close, cfg.rsi_period)
    signal = oversold_signal(rsi_values, cfg.rsi_threshold, cfg.consecutive_days)
    avg_dollar_volume = (close * volume).rolling(cfg.avg_volume_window,
                                                 min_periods=cfg.avg_volume_window).mean()
    eligible = signal & (close >= cfg.min_price) & (avg_dollar_volume >= cfg.min_avg_dollar_volume)
    if cfg.trend_ma_days:
        eligible &= close > close.rolling(cfg.trend_ma_days, min_periods=cfg.trend_ma_days).mean()
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


def run_backtest(panel: dict[str, pd.DataFrame], cfg: StrategyConfig,
                 start: str | pd.Timestamp | None = None,
                 end: str | pd.Timestamp | None = None,
                 market_close: pd.Series | None = None) -> BacktestResult:
    """Simule la stratégie sur `panel` ({Open, High, Low, Close, Volume} -> DataFrame dates x tickers).

    Les données avant `start` servent uniquement au calcul des indicateurs (période de chauffe).
    """
    close = panel["Close"]
    rsi_values, eligible = compute_signals(panel, cfg, market_close)

    dates = close.index
    mask = np.ones(len(dates), dtype=bool)
    if start is not None:
        mask &= dates >= pd.Timestamp(start)
    if end is not None:
        mask &= dates <= pd.Timestamp(end)
    day_indices = np.flatnonzero(mask)
    if len(day_indices) == 0:
        raise ValueError("Aucune séance dans la période demandée.")

    tickers = list(close.columns)
    col = {t: j for j, t in enumerate(tickers)}
    O = panel["Open"].to_numpy(dtype=float)
    H = panel["High"].to_numpy(dtype=float)
    L = panel["Low"].to_numpy(dtype=float)
    C_ffill = close.ffill().to_numpy(dtype=float)  # dernier cours connu, pour la valorisation
    RSI = rsi_values.to_numpy(dtype=float)
    ELIG = eligible.to_numpy(dtype=bool)

    comm = cfg.commission_bps / 1e4
    slip = cfg.slippage_bps / 1e4

    cash = cfg.initial_capital
    positions: dict[str, Position] = {}
    pending: list[dict] = []
    trades, records = [], []

    for i in day_indices:
        d = dates[i]

        # 1. Achats à l'ouverture (ordres de la veille, classés du RSI le plus bas au plus haut).
        for order in pending:
            j = col[order["ticker"]]
            px_open = O[i, j]
            if not np.isfinite(px_open) or px_open <= 0:
                continue  # pas de cotation : l'ordre est annulé
            fill = px_open * (1 + slip)
            budget = min(order["budget"], cash)
            shares = int(budget // (fill * (1 + comm)))
            if shares <= 0 or budget < order["min_budget"]:
                continue
            fee = shares * fill * comm
            cash -= shares * fill + fee
            positions[order["ticker"]] = Position(
                ticker=order["ticker"], shares=shares, entry_date=d, entry_idx=i,
                entry_price=fill,
                stop=fill * (1 - cfg.stop_loss_pct),
                target=fill * (1 + cfg.take_profit_pct),
                signal_date=order["signal_date"], signal_rsi=order["rsi"], entry_fee=fee,
                entry_slippage=shares * (fill - px_open),
            )
        pending = []

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
            })
            del positions[t]

        # 3. Valorisation à la clôture.
        invested = sum(pos.shares * C_ffill[i, col[t]] for t, pos in positions.items())
        equity = cash + invested
        records.append({"date": d, "cash": cash, "invested": invested,
                        "equity": equity, "n_positions": len(positions)})

        # 4. Scan quotidien des nouvelles opportunités -> ordres pour demain.
        #    Le lendemain, les ordres sont exécutés dans cet ordre tant qu'il reste du cash.
        cand = [j for j in np.flatnonzero(ELIG[i]) if tickers[j] not in positions]
        cand.sort(key=lambda j: RSI[i, j])
        if cfg.max_positions is not None:
            cand = cand[:max(cfg.max_positions - len(positions), 0)]
        pending = [{"ticker": tickers[j], "signal_date": d, "rsi": RSI[i, j],
                    "close": C_ffill[i, j], "budget": equity * cfg.position_size_pct,
                    "min_budget": equity * cfg.min_position_pct}
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
    )
