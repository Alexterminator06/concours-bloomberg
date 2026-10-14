"""Statistiques de performance de la stratégie et comparaison avec l'indice."""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def performance_stats(equity: pd.Series, risk_free: float = 0.0) -> dict:
    """Rendement, volatilité, Sharpe, Sortino, drawdown... d'une courbe de valeur."""
    equity = equity.dropna()
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = rets.std() * np.sqrt(TRADING_DAYS)
    excess = rets - risk_free / TRADING_DAYS
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean()) * np.sqrt(TRADING_DAYS)
    drawdown = equity / equity.cummax() - 1
    max_dd = drawdown.min()
    return {
        "Valeur finale ($)": equity.iloc[-1],
        "Rendement total": total,
        "Rendement annualisé": cagr,
        "Volatilité annualisée": vol,
        "Sharpe": excess.mean() * TRADING_DAYS / vol if vol > 0 else np.nan,
        "Sortino": excess.mean() * TRADING_DAYS / downside if downside > 0 else np.nan,
        "Max drawdown": max_dd,
        "Calmar": cagr / abs(max_dd) if max_dd < 0 else np.nan,
    }


def _total_return(equity: pd.Series) -> float:
    equity = equity.dropna()
    return equity.iloc[-1] / equity.iloc[0] - 1


def relative_stats(strategy: pd.Series, benchmark: pd.Series, risk_free: float = 0.0) -> dict:
    """Rendement relatif, bêta, alpha de Jensen, corrélation, tracking error et ratio
    d'information de la stratégie par rapport à l'indice."""
    rets = pd.concat([strategy.pct_change(), benchmark.pct_change()], axis=1).dropna()
    rets.columns = ["s", "b"]
    rf = risk_free / TRADING_DAYS
    var_b = rets["b"].var()
    beta = rets["s"].cov(rets["b"]) / var_b if var_b > 0 else np.nan
    alpha = ((rets["s"] - rf) - beta * (rets["b"] - rf)).mean() * TRADING_DAYS
    active = rets["s"] - rets["b"]
    te = active.std() * np.sqrt(TRADING_DAYS)
    return {
        "Rendement relatif": _total_return(strategy) - _total_return(benchmark),
        "Bêta": beta,
        "Alpha annualisé": alpha,
        "Corrélation": rets["s"].corr(rets["b"]),
        "Tracking error": te,
        "Ratio d'information": active.mean() * TRADING_DAYS / te if te > 0 else np.nan,
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    """Statistiques sur les positions clôturées."""
    if trades.empty:
        return {"Nombre de trades": 0}
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_loss = -losses["pnl"].sum()
    reasons = trades["exit_reason"].value_counts()
    return {
        "Nombre de trades": len(trades),
        "Taux de réussite": len(wins) / len(trades),
        "Gain moyen par trade": trades["return_pct"].mean(),
        "Gain moyen avant frais": ((trades["pnl"] + trades["costs"])
                                   / (trades["shares"] * trades["entry_price"])).mean(),
        "Gain moyen (gagnants)": wins["return_pct"].mean() if len(wins) else np.nan,
        "Perte moyenne (perdants)": losses["return_pct"].mean() if len(losses) else np.nan,
        "Profit factor": wins["pnl"].sum() / gross_loss if gross_loss > 0 else np.nan,
        "P&L total réalisé ($)": trades["pnl"].sum(),
        "Frais et glissement payés ($)": trades["costs"].sum(),
        "Durée moyenne (séances)": trades["holding_days"].mean(),
        "Sorties take profit": int(reasons.get("take_profit", 0)),
        "Sorties stop loss": int(reasons.get("stop_loss", 0)),
        "Séances ambiguës (stop et objectif touchés)": int(trades["ambiguous"].sum()),
    }


def aligned_prices(close: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Cours de l'indice aux `dates`, repris du dernier jour coté (jour férié de sa bourse)."""
    return close.reindex(close.index.union(dates)).ffill().reindex(dates)


def benchmark_equity(close: pd.Series, dates: pd.DatetimeIndex, capital: float) -> pd.Series:
    """Valeur d'un investissement de `capital` en achat-conservation dans l'indice."""
    px = aligned_prices(close, dates)
    return capital * px / px.iloc[0]
