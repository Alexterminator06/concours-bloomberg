"""Ligne de commande.

    python -m rsi_strategy backtest [--start 2023-01-01] [--end 2026-10-13]
    python -m rsi_strategy scan       # opportunités du jour, à lancer une fois par jour après la clôture
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .backtest import compute_signals, run_backtest
from .config import StrategyConfig
from .data import download_ohlcv, load_ohlcv
from .metrics import benchmark_equity, performance_stats, relative_stats, trade_stats
from .report import format_table, plot_performance, print_section, save_csv
from .universe import load_universe

ROOT = Path(__file__).resolve().parent.parent
# Jours calendaires téléchargés avant le début pour initialiser les indicateurs
# (RSI et moyennes mobiles jusqu'à environ 250 séances).
WARMUP_DAYS = 400


def build_parser() -> argparse.ArgumentParser:
    defaults = StrategyConfig()
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("univers et données")
    g.add_argument("--universe-file", type=Path, default=ROOT / "data" / "universe.csv")
    g.add_argument("--refresh-universe", action="store_true",
                   help="ré-interroger le screener Yahoo au lieu du fichier sauvegardé")
    g.add_argument("--refresh-data", action="store_true", help="ignorer le cache des cours")
    g.add_argument("--max-tickers", type=int, default=None,
                   help="limiter l'univers aux N plus grosses capitalisations (tests rapides)")
    s = common.add_argument_group("stratégie")
    s.add_argument("--capital", type=float, default=defaults.initial_capital)
    s.add_argument("--position-size", type=float, default=defaults.position_size_pct,
                   help="taille d'une position en fraction du portefeuille (défaut 0.20 = 20 %%)")
    s.add_argument("--max-positions", type=int, default=defaults.max_positions,
                   help="nombre maximum de positions simultanées (défaut : pas de limite)")
    s.add_argument("--rsi-period", type=int, default=defaults.rsi_period)
    s.add_argument("--rsi-threshold", type=float, default=defaults.rsi_threshold)
    s.add_argument("--days", type=int, default=defaults.consecutive_days,
                   help="nombre de séances consécutives sous le seuil")
    s.add_argument("--trend-filter", type=int, default=defaults.trend_ma_days, metavar="N",
                   help="n'acheter que si l'action est au-dessus de sa moyenne mobile N séances "
                        "(ex. 200 ; 0 = désactivé)")
    s.add_argument("--market-filter", type=int, default=defaults.market_ma_days, metavar="N",
                   help="n'acheter que si l'indice principal est au-dessus de sa moyenne mobile "
                        "N séances (ex. 200 ; 0 = désactivé)")
    s.add_argument("--stop-loss", type=float, default=defaults.stop_loss_pct)
    s.add_argument("--take-profit", type=float, default=defaults.take_profit_pct)
    s.add_argument("--take-profit-first", action="store_true",
                   help="si stop et objectif sont touchés la même séance, retenir l'objectif "
                        "(hypothèse optimiste ; par défaut on retient le stop)")
    s.add_argument("--commission-bps", type=float, default=defaults.commission_bps)
    s.add_argument("--slippage-bps", type=float, default=defaults.slippage_bps)
    s.add_argument("--benchmarks", nargs="+", default=defaults.benchmarks,
                   help="ETF de comparaison ; le premier est l'indice principal")

    parser = argparse.ArgumentParser(prog="rsi_strategy", description="Stratégie RSI small & mid caps")
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("backtest", parents=[common], help="simuler la stratégie et la comparer à l'indice")
    bt.add_argument("--start", default=(date.today() - timedelta(days=3 * 365)).isoformat())
    bt.add_argument("--end", default=date.today().isoformat())
    bt.add_argument("--out", type=Path, default=ROOT / "results")
    sub.add_parser("scan", parents=[common], help="lister les signaux d'achat du jour")
    return parser


def config_from_args(args) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=args.capital, position_size_pct=args.position_size,
        max_positions=args.max_positions, rsi_period=args.rsi_period,
        rsi_threshold=args.rsi_threshold, consecutive_days=args.days,
        trend_ma_days=args.trend_filter, market_ma_days=args.market_filter,
        stop_loss_pct=args.stop_loss, take_profit_pct=args.take_profit,
        stop_first_if_ambiguous=not args.take_profit_first,
        commission_bps=args.commission_bps, slippage_bps=args.slippage_bps,
        benchmarks=args.benchmarks,
    )


def _universe_tickers(args, cfg: StrategyConfig) -> list[str]:
    universe = load_universe(cfg, args.universe_file, refresh=args.refresh_universe,
                             max_tickers=args.max_tickers)
    print(f"Univers : {len(universe)} actions small & mid caps ({args.universe_file})")
    return [t for t in universe["symbol"].astype(str) if t not in cfg.benchmarks]


def cmd_backtest(args) -> None:
    cfg = config_from_args(args)
    tickers = _universe_tickers(args, cfg)
    data_start = (pd.Timestamp(args.start) - timedelta(days=WARMUP_DAYS)).date().isoformat()
    data_end = (pd.Timestamp(args.end) + timedelta(days=1)).date().isoformat()  # borne exclusive chez Yahoo
    cache = ROOT / "data" / "cache"

    print("Cours des actions :")
    panel = load_ohlcv(tickers, data_start, data_end, cache, refresh=args.refresh_data)
    print("Cours des indices :")
    bench_close = load_ohlcv(cfg.benchmarks, data_start, data_end, cache,
                             refresh=args.refresh_data)["Close"]

    market = cfg.benchmarks[0]
    if cfg.market_ma_days and market not in bench_close:
        raise SystemExit(f"Filtre de marché : pas de cours pour l'indice {market}.")
    result = run_backtest(panel, cfg, start=args.start, end=args.end,
                          market_close=bench_close.get(market))
    equity = result.equity["equity"]
    dates = equity.index

    curves = {"Stratégie RSI": equity}
    for b in cfg.benchmarks:
        if b not in bench_close or pd.isna(bench_close[b].reindex(dates).iloc[0]):
            print(f"  ! {b} : pas de cours au {dates[0].date()}, indice ignoré")
            continue
        curves[b] = benchmark_equity(bench_close[b], dates, cfg.initial_capital)
    curves = pd.DataFrame(curves)
    names = list(curves.columns)

    perf = {n: performance_stats(curves[n]) for n in names}
    rel = {n: relative_stats(equity, curves[n]) for n in names[1:]}
    tstats = trade_stats(result.trades)
    tstats["Exposition moyenne"] = (result.equity["invested"] / equity).mean()

    print(f"\nPériode : {dates[0].date()} -> {dates[-1].date()} ({len(dates)} séances), "
          f"capital initial {cfg.initial_capital:,.0f} $".replace(",", " "))
    print_section("Performance : stratégie vs indices", format_table(perf))
    if rel:
        print_section("Stratégie relativement à chaque indice", format_table(rel))
    print_section("Statistiques des trades", tstats)
    if not result.open_positions.empty:
        print_section("Positions ouvertes en fin de période",
                      result.open_positions.set_index("ticker").round(
                          {"entry_price": 2, "stop_loss": 2, "take_profit": 2, "last_price": 2,
                           "unrealized_pnl": 0}))

    save_csv(args.out, trades=result.trades, open_positions=result.open_positions,
             equity_curve=curves, next_orders=result.next_orders,
             summary=format_table({**perf, **{f"vs {k}": v for k, v in rel.items()}}).reset_index())
    plot_performance(curves, args.out / "performance.png",
                     f"Stratégie RSI(<{cfg.rsi_threshold:g} sur {cfg.consecutive_days} j) "
                     f"small & mid caps vs indices")
    print(f"\nRésultats enregistrés dans {args.out}/")


def cmd_scan(args) -> None:
    cfg = config_from_args(args)
    tickers = _universe_tickers(args, cfg)
    start = (date.today() - timedelta(days=WARMUP_DAYS)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    panel = download_ohlcv(tickers, start, end)
    market_close = None
    if cfg.market_ma_days:
        market_close = download_ohlcv([cfg.benchmarks[0]], start, end)["Close"].iloc[:, 0]

    rsi_values, eligible = compute_signals(panel, cfg, market_close)
    last = panel["Close"].index[-1]
    if market_close is not None:
        bullish = market_close.iloc[-1] > market_close.rolling(cfg.market_ma_days).mean().iloc[-1]
        print(f"Filtre de marché : {cfg.benchmarks[0]} "
              f"{'au-dessus' if bullish else 'en dessous'} de sa moyenne {cfg.market_ma_days} séances"
              f"{'' if bullish else ' -> aucun achat'}")
    hits = eligible.columns[eligible.loc[last].to_numpy(dtype=bool)]
    close = panel["Close"].loc[last, hits]
    budget = cfg.initial_capital * cfg.position_size_pct
    orders = pd.DataFrame({
        "rsi": rsi_values.loc[last, hits],
        "rsi_veille": rsi_values.shift(1).loc[last, hits],
        "dernier_cours": close,
        "quantite_indicative": (budget // close).astype(int),
        "stop_loss_indicatif": close * (1 - cfg.stop_loss_pct),
        "take_profit_indicatif": close * (1 + cfg.take_profit_pct),
    }).sort_values("rsi").round(2)
    if cfg.max_positions is not None:
        orders = orders.head(cfg.max_positions)

    print(f"\nSignaux au {last.date()} : RSI({cfg.rsi_period}) < {cfg.rsi_threshold:g} "
          f"sur {cfg.consecutive_days} séances -> {len(orders)} action(s)")
    if orders.empty:
        print("Aucune opportunité aujourd'hui.")
        return
    print(orders.to_string())
    print(f"\nBudget par position : {budget:,.0f} $ ({cfg.position_size_pct:.0%} du capital). "
          f"Acheter dans l'ordre du tableau (RSI le plus bas d'abord) tant qu'il reste du cash. "
          f"Achat à l'ouverture ; stop loss et take profit à recalculer sur le prix d'exécution "
          f"réel (-{cfg.stop_loss_pct:.0%} / +{cfg.take_profit_pct:.0%}).".replace(",", " "))
    out = ROOT / "results" / f"signals_{last.date()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    orders.to_csv(out)
    print(f"Enregistré dans {out}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    {"backtest": cmd_backtest, "scan": cmd_scan}[args.command](args)


if __name__ == "__main__":
    main()
