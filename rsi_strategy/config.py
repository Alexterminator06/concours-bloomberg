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
    # Take profit adapté à la tendance (0 = désactivé, toujours `take_profit_pct`) : si, au moment
    # du signal, l'indice principal (VT, proche du WLS) est au-dessus de sa moyenne mobile
    # `bull_ma_days` séances, l'objectif passe à `take_profit_bull_pct`. Désactivé : avec 10 %,
    # les backtests 2019-2023 et 2023-2026 font nettement moins bien qu'un objectif fixe à 5 %.
    take_profit_bull_pct: float = 0.0
    bull_ma_days: int = 200
    # Si le stop et l'objectif sont touchés dans la même séance, on ne connaît pas l'ordre
    # avec des données journalières : par prudence on suppose que le stop est touché en premier.
    stop_first_if_ambiguous: bool = True

    # --- Grandes valeurs pour le cash non utilisé (0 = désactivé) ---
    # Le cash que les achats RSI n'utilisent pas est placé dans les `core_size` plus grandes
    # capitalisations US du WLS, à parts à peu près égales, sans stop ni objectif. Elles sont
    # vendues en priorité pour financer les achats RSI. Nécessite la liste des membres du WLS.
    core_size: int = 10
    # Part minimale du portefeuille réservée aux grandes valeurs (0 = seulement le cash restant).
    # Les achats RSI ne peuvent alors utiliser que (1 - core_min_pct) de la valeur totale.
    # 50 % : seule configuration qui bat VT sur 2019-2023 et sur 2023-2026 (univers US), surtout
    # grâce aux grandes valeurs.
    core_min_pct: float = 0.5
    # Backtest : candidates (plus gros poids actuels du WLS) parmi lesquelles on retient chaque mois
    # les `core_size` plus grandes capitalisations de l'époque. Ces actions n'ont jamais de signal RSI.
    core_pool_size: int = 100
    # Backtest : élargit les candidates aux N plus gros poids actuels (0 = `core_pool_size`), pour
    # retrouver les grandes valeurs d'hier qui ne sont plus parmi les plus grandes aujourd'hui. Les
    # candidates au-delà de `core_pool_size` restent ouvertes au RSI, sauf le mois où elles sont
    # retenues.
    core_candidates: int = 0
    # Momentum absolu (0 = désactivé) : au début de chaque mois, si l'indice principal a baissé sur
    # ce nombre de mois, les grandes valeurs sont vendues et leur part reste en cash jusqu'au mois
    # suivant.
    core_abs_momentum_months: int = 0
    # Momentum relatif (0 = désactivé) : au début de chaque mois, parmi les `core_momentum_pool`
    # plus grandes capitalisations, on retient les `core_size` qui ont le plus monté sur ce nombre
    # de mois, en ignorant le dernier mois, au lieu des `core_size` plus grandes.
    core_rel_momentum_months: int = 0
    core_momentum_pool: int = 50

    # --- Coûts de transaction (aucun dans le concours Bloomberg) ---
    commission_bps: float = 0.0  # commission par ordre, en points de base du montant
    slippage_bps: float = 0.0    # glissement sur les ordres au marché / stop (pas sur le take profit limite)

    # --- Filtres de liquidité appliqués au moment du signal ---
    min_price: float = 5.0
    min_avg_dollar_volume: float = 1_000_000.0  # moyenne 20 séances de prix x volume
    avg_volume_window: int = 20

    # --- Indices de comparaison (ETF, cours ajustés des dividendes) ---
    # L'indice du concours est le WLS (Bloomberg World Large, Mid & Small Cap), absent de Yahoo
    # Finance. VT (Vanguard Total World Stock : actions mondiales de toutes tailles) est l'ETF qui
    # s'en rapproche le plus ; c'est l'indice principal. SMMD (Russell 2500) et IWM (Russell 2000)
    # représentent les small & mid caps US, l'univers de la stratégie.
    benchmarks: list[str] = field(default_factory=lambda: ["VT", "SMMD", "IWM"])

    # --- Univers (screener Yahoo Finance) ---
    min_market_cap: float = 300e6   # small caps : 300 M$ – 2 Md$
    max_market_cap: float = 10e9    # mid caps   : 2 Md$ – 10 Md$
    min_avg_volume_3m: int = 100_000
    exchanges: list[str] = field(default_factory=lambda: ["NMS", "NGM", "NCM", "NYQ", "ASE"])
