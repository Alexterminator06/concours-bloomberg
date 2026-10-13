"""Paramètres de la stratégie RSI (tous modifiables depuis la ligne de commande)."""

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    # --- Capital & dimensionnement ---
    initial_capital: float = 1_000_000.0
    # Taille d'une nouvelle position, en % de la valeur du portefeuille au moment du signal.
    position_size_pct: float = 0.05
    # Nombre maximum de positions ouvertes en même temps (0.05 x 20 = 100 % investi au maximum).
    max_positions: int = 20

    # --- Signal d'achat ---
    rsi_period: int = 14
    rsi_threshold: float = 30.0
    # Le RSI doit être sous le seuil pendant ce nombre de séances consécutives.
    consecutive_days: int = 2

    # --- Sorties (en % du prix d'entrée) ---
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.05
    # Si le stop et l'objectif sont touchés dans la même séance, on ne connaît pas l'ordre
    # avec des données journalières : par prudence on suppose que le stop est touché en premier.
    stop_first_if_ambiguous: bool = True

    # --- Coûts de transaction ---
    commission_bps: float = 5.0  # commission par ordre, en points de base du montant
    slippage_bps: float = 5.0    # glissement sur les ordres au marché / stop (pas sur le take profit limite)

    # --- Filtres de liquidité appliqués au moment du signal ---
    min_price: float = 5.0
    min_avg_dollar_volume: float = 1_000_000.0  # moyenne 20 séances de prix x volume
    avg_volume_window: int = 20

    # --- Indices de comparaison (ETF, cours ajustés des dividendes) ---
    # SMMD = iShares Russell 2500 (small + mid caps US), IWM = Russell 2000, IJH = S&P MidCap 400.
    benchmarks: list[str] = field(default_factory=lambda: ["SMMD", "IWM", "IJH"])

    # --- Univers (screener Yahoo Finance) ---
    min_market_cap: float = 300e6   # small caps : 300 M$ – 2 Md$
    max_market_cap: float = 10e9    # mid caps   : 2 Md$ – 10 Md$
    min_avg_volume_3m: int = 100_000
    exchanges: list[str] = field(default_factory=lambda: ["NMS", "NGM", "NCM", "NYQ", "ASE"])
