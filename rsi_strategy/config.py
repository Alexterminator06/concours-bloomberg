"""Paramètres de la stratégie RSI (tous modifiables depuis la ligne de commande)."""

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    # --- Capital & dimensionnement ---
    initial_capital: float = 1_000_000.0
    # Taille d'une nouvelle position, en % de la valeur totale du portefeuille au moment du signal.
    # 0.20 = la limite autorisée de 20 % du capital total par position.
    position_size_pct: float = 0.20
    # Pas de limite sur le nombre de positions (None) : c'est le cash disponible qui limite,
    # sans levier. Mettre un entier pour imposer un maximum.
    max_positions: int | None = None
    # Quand il reste peu de cash, on n'ouvre pas de position plus petite que ce % du portefeuille
    # (évite d'investir des reliquats de quelques dollars).
    min_position_pct: float = 0.02

    # --- Signal d'achat ---
    # RSI(2) < 10 + filtre de tendance 200 séances : variante retenue après les backtests 2019-2026.
    # Règle d'origine : rsi_period=14, rsi_threshold=30, consecutive_days=2, trend_ma_days=0.
    rsi_period: int = 2
    rsi_threshold: float = 10.0
    # Le RSI doit être sous le seuil pendant ce nombre de séances consécutives.
    consecutive_days: int = 1

    # --- Filtres de tendance (0 = désactivé) ---
    # N'acheter que si l'action cote au-dessus de sa moyenne mobile sur N séances.
    trend_ma_days: int = 200
    # N'acheter que si l'indice principal (1er de `benchmarks`) cote au-dessus de sa moyenne N séances.
    market_ma_days: int = 0

    # --- Sorties (en % du prix d'entrée) ---
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.05
    # Si le stop et l'objectif sont touchés dans la même séance, on ne connaît pas l'ordre
    # avec des données journalières : par prudence on suppose que le stop est touché en premier.
    stop_first_if_ambiguous: bool = True

    # --- Coûts de transaction (aucun dans le concours Bloomberg) ---
    commission_bps: float = 0.0  # commission par ordre, en points de base du montant
    slippage_bps: float = 0.0    # glissement sur les ordres au marché / stop (pas sur le take profit limite)

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
