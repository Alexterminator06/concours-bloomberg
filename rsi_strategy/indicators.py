"""Indicateurs techniques."""

import pandas as pd


def rsi(close: pd.DataFrame | pd.Series, period: int = 14) -> pd.DataFrame | pd.Series:
    """RSI de Wilder (moyennes mobiles exponentielles avec alpha = 1/period).

    Fonctionne colonne par colonne sur un DataFrame (une colonne par action).
    Les `period` premières valeurs de chaque série valent NaN.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 -> rs infini -> RSI = 100 ; avg_gain == avg_loss == 0 (cours plat) -> 50.
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out


def oversold_signal(rsi_values: pd.DataFrame, threshold: float = 30.0, days: int = 2) -> pd.DataFrame:
    """True quand le RSI est strictement sous `threshold` sur les `days` dernières séances."""
    # Un RSI manquant (NaN) compte comme "pas sous le seuil".
    below = (rsi_values < threshold).astype(float)
    return below.rolling(days, min_periods=days).sum() == days
