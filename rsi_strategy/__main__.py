"""Ligne de commande.

    python -m rsi_strategy import-wls WLS.xlsx   # liste des membres du WLS exportée de Bloomberg
    python -m rsi_strategy backtest [--start 2023-01-01] [--end 2026-10-13]
    python -m rsi_strategy scan       # ordres du jour, à lancer une fois par jour après la clôture
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .backtest import compute_signals, market_is_bullish, run_backtest
from .config import StrategyConfig
from .data import download_ohlcv, fx_tickers, load_ohlcv, to_usd
from .metrics import (aligned_prices, benchmark_equity, performance_stats, relative_stats,
                      trade_stats)
from .portfolio import load_portfolio, plan_orders
from .report import format_table, plot_performance, print_section, save_csv
from .universe import load_universe
from .wls import (alternative_symbols, bloomberg_lookup, import_wls, largest, load_wls,
                  us_small_mid_universe)

ROOT = Path(__file__).resolve().parent.parent
# Jours calendaires téléchargés avant le début pour initialiser les indicateurs
# (RSI et moyennes mobiles jusqu'à environ 250 séances).
WARMUP_DAYS = 400


def build_parser() -> argparse.ArgumentParser:
    defaults = StrategyConfig()
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("univers et données")
    g.add_argument("--wls-file", type=Path, default=ROOT / "data" / "wls_members.csv",
                   help="membres du WLS (créé par import-wls) : univers et grandes valeurs")
    g.add_argument("--universe", choices=["monde", "us"], default="monde",
                   help="monde : tous les membres du WLS (défaut) ; us : small & mid caps "
                        "américaines du WLS")
    g.add_argument("--universe-file", type=Path, default=ROOT / "data" / "universe.csv",
                   help="univers Yahoo, utilisé seulement sans liste WLS")
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
    s.add_argument("--take-profit-bull", type=float, default=defaults.take_profit_bull_pct,
                   help="take profit quand l'indice principal est au-dessus de sa moyenne "
                        f"{defaults.bull_ma_days} séances (0 = toujours --take-profit)")
    s.add_argument("--core-size", type=int, default=defaults.core_size,
                   help="nombre de grandes valeurs où placer le cash inutilisé (0 = désactivé)")
    s.add_argument("--core-min", type=float, default=defaults.core_min_pct,
                   help=f"part du portefeuille réservée aux grandes valeurs (défaut "
                        f"{defaults.core_min_pct:g} ; 0 = seulement le cash que les achats RSI "
                        f"n'utilisent pas)")
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
    sc = sub.add_parser("scan", parents=[common], help="lister les ordres d'achat du jour")
    sc.add_argument("--portfolio-file", type=Path, default=ROOT / "portefeuille.csv",
                    help="positions et cash actuels (défaut : portefeuille.csv s'il existe)")
    wl = sub.add_parser("import-wls", help="importer la liste des membres du WLS (export Bloomberg)")
    wl.add_argument("source", type=Path, help="fichier Excel ou CSV exporté de Bloomberg")
    wl.add_argument("--wls-file", type=Path, default=ROOT / "data" / "wls_members.csv")
    return parser


def config_from_args(args) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=args.capital, position_size_pct=args.position_size,
        max_positions=args.max_positions, rsi_period=args.rsi_period,
        rsi_threshold=args.rsi_threshold, consecutive_days=args.days,
        trend_ma_days=args.trend_filter, market_ma_days=args.market_filter,
        stop_loss_pct=args.stop_loss, take_profit_pct=args.take_profit,
        take_profit_bull_pct=args.take_profit_bull, core_size=args.core_size,
        core_min_pct=args.core_min,
        stop_first_if_ambiguous=not args.take_profit_first,
        commission_bps=args.commission_bps, slippage_bps=args.slippage_bps,
        benchmarks=args.benchmarks,
    )


def signal_label(cfg: StrategyConfig) -> str:
    """Description courte du signal d'achat, ex. « RSI(2) < 10, au-dessus de la moyenne 200 j »."""
    label = f"RSI({cfg.rsi_period}) < {cfg.rsi_threshold:g}"
    if cfg.consecutive_days > 1:
        label += f" {cfg.consecutive_days} séances de suite"
    if cfg.trend_ma_days:
        label += f", au-dessus de la moyenne {cfg.trend_ma_days} j"
    if cfg.market_ma_days:
        label += f", marché au-dessus de sa moyenne {cfg.market_ma_days} j"
    return label


def _universe(args, cfg: StrategyConfig) -> tuple[list[str], pd.DataFrame | None]:
    """Tickers où chercher les signaux RSI, et membres du WLS s'ils ont été importés."""
    try:
        wls = load_wls(args.wls_file)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if wls is not None and args.universe == "monde":
        universe = wls
        if cfg.core_size:  # les grandes valeurs servent au cash restant, pas aux signaux RSI
            universe = wls[~wls["symbol"].isin(_core_pool(wls, cfg)["symbol"])]
        if args.max_tickers:
            universe = universe.head(args.max_tickers)
        print(f"Univers : {len(universe)} actions membres du WLS, toutes places et tailles "
              f"({args.wls_file})")
    elif wls is not None:
        universe = us_small_mid_universe(wls, cfg.min_market_cap, cfg.max_market_cap)
        if args.max_tickers:
            universe = universe.head(args.max_tickers)
        print(f"Univers : {len(universe)} small & mid caps US membres du WLS ({args.wls_file})")
    else:
        universe = load_universe(cfg, args.universe_file, refresh=args.refresh_universe,
                                 max_tickers=args.max_tickers)
        print(f"Univers : {len(universe)} actions small & mid caps ({args.universe_file})")
        print("  ! Liste du WLS absente : certaines actions peuvent être refusées par le concours "
              "(voir import-wls).")
    if cfg.core_size and wls is None:
        print("  ! Grandes valeurs désactivées : elles demandent la liste du WLS (import-wls).")
    return [t for t in universe["symbol"].astype(str) if t not in cfg.benchmarks], wls


def _core_pool(wls: pd.DataFrame, cfg: StrategyConfig, size: int | None = None) -> pd.DataFrame:
    """Plus grandes valeurs du WLS (au-dessus de la limite des mid caps)."""
    return largest(wls, size or cfg.core_pool_size, above=cfg.max_market_cap)


def _load_prices(tickers: list[str], wls: pd.DataFrame | None, start: str, end: str,
                 cache: Path | None, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Cours des tickers, convertis en dollars. Avec `cache` : cache disque (backtest) ;
    sans : téléchargement direct (scan quotidien)."""
    alternatives = None
    if wls is not None:
        info = wls.set_index("symbol")
        alternatives = {t: alt for t in tickers if t in info.index
                        for alt in [alternative_symbols(t, info.at[t, "exchange"])] if alt}

    def load(symbols, alts=None):
        if cache is None:
            return download_ohlcv(symbols, start, end, alternatives=alts)
        return load_ohlcv(symbols, start, end, cache, refresh=refresh, alternatives=alts)

    panel = load(tickers, alternatives)
    if wls is None:
        return panel
    info = wls.set_index("symbol").reindex(panel["Close"].columns)
    currency = info["currency"].fillna("USD")
    fx_symbols = fx_tickers(currency)
    if not fx_symbols:
        return panel
    print(f"Taux de change ({len(fx_symbols)} devises) :")
    fx_close = load(list(fx_symbols.values()))["Close"]
    return to_usd(panel, currency, info["unit"].fillna(1.0), fx_close)


def _us_symbols(wls: pd.DataFrame | None) -> list[str] | None:
    """Tickers américains, seuls soumis au cours minimum (les autres places cotent en devise
    locale, où un cours bas n'indique pas une action marginale)."""
    return None if wls is None else list(wls.loc[wls["currency"] == "USD", "symbol"])


def _core_label(cfg: StrategyConfig) -> str:
    if cfg.core_min_pct:
        return f"au moins {cfg.core_min_pct:.0%} du portefeuille et le cash restant"
    return "cash restant"


def _needs_market(cfg: StrategyConfig) -> bool:
    return bool(cfg.market_ma_days or cfg.take_profit_bull_pct)


def take_profit_label(cfg: StrategyConfig) -> str:
    if not cfg.take_profit_bull_pct:
        return f"+{cfg.take_profit_pct:.0%}"
    return (f"+{cfg.take_profit_bull_pct:.0%} si {cfg.benchmarks[0]} est au-dessus de sa moyenne "
            f"{cfg.bull_ma_days} j, +{cfg.take_profit_pct:.0%} sinon")


def cmd_backtest(args) -> None:
    cfg = config_from_args(args)
    tickers, wls = _universe(args, cfg)
    core_pool = None
    if cfg.core_size and wls is not None:
        core_pool = _core_pool(wls, cfg)
    if core_pool is not None:
        tickers = sorted(set(tickers) | set(core_pool["symbol"]))
    data_start = (pd.Timestamp(args.start) - timedelta(days=WARMUP_DAYS)).date().isoformat()
    data_end = (pd.Timestamp(args.end) + timedelta(days=1)).date().isoformat()  # borne exclusive chez Yahoo
    cache = ROOT / "data" / "cache"

    print("Cours des actions :")
    panel = _load_prices(tickers, wls, data_start, data_end, cache, refresh=args.refresh_data)
    print("Cours des indices :")
    bench_close = load_ohlcv(cfg.benchmarks, data_start, data_end, cache,
                             refresh=args.refresh_data)["Close"]

    market = cfg.benchmarks[0]
    if _needs_market(cfg) and market not in bench_close:
        raise SystemExit(f"Pas de cours pour l'indice {market} (filtre de marché ou take profit "
                         f"selon la tendance).")
    core_shares = None
    if core_pool is not None:
        core_shares = core_pool.set_index("symbol")["index_shares_m"]
        core_shares = core_shares[core_shares.index.isin(panel["Close"].columns)]
    result = run_backtest(panel, cfg, start=args.start, end=args.end,
                          market_close=bench_close.get(market), core_shares=core_shares,
                          price_filter=_us_symbols(wls))
    equity = result.equity["equity"]
    dates = equity.index

    curves = {"Stratégie RSI": equity}
    for b in cfg.benchmarks:
        if b not in bench_close or pd.isna(aligned_prices(bench_close[b], dates).iloc[0]):
            print(f"  ! {b} : pas de cours au {dates[0].date()}, indice ignoré")
            continue
        curves[b] = benchmark_equity(bench_close[b], dates, cfg.initial_capital)
    curves = pd.DataFrame(curves)
    names = list(curves.columns)

    perf = {n: performance_stats(curves[n]) for n in names}
    rel = {n: relative_stats(equity, curves[n]) for n in names[1:]}
    tstats = trade_stats(result.trades)
    tstats["Exposition moyenne"] = (result.equity["invested"] / equity).mean()
    if core_shares is not None:
        tstats["Part moyenne en grandes valeurs"] = (result.equity["core"] / equity).mean()
        tstats["P&L grandes valeurs ($)"] = result.core_pnl

    print(f"\nPériode : {dates[0].date()} -> {dates[-1].date()} ({len(dates)} séances), "
          f"capital initial {cfg.initial_capital:,.0f} $".replace(",", " "))
    print(f"Signal : {signal_label(cfg)} · stop -{cfg.stop_loss_pct:.0%} · take profit "
          f"{take_profit_label(cfg)}"
          + (f" · {_core_label(cfg)} dans les {cfg.core_size} plus grandes valeurs du WLS"
             if core_shares is not None else ""))
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
                     f"Stratégie {signal_label(cfg)} – small & mid caps vs indices")
    print(f"\nRésultats enregistrés dans {args.out}/")


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def cmd_scan(args) -> None:
    cfg = config_from_args(args)
    tickers, wls = _universe(args, cfg)
    portfolio = None
    if args.portfolio_file.exists():
        try:
            portfolio = load_portfolio(args.portfolio_file)
        except ValueError as exc:
            raise SystemExit(f"Erreur dans le portefeuille : {exc}")
        if wls is not None:  # tickers recopiés de la plateforme : « 7203 JT » -> « 7203.T »
            lookup = bloomberg_lookup(wls)
            portfolio.positions.index = [lookup.get(t.replace(" EQUITY", "").strip(), t)
                                         for t in portfolio.positions.index]
        print(f"Portefeuille : {args.portfolio_file.name} ({len(portfolio.positions)} position(s), "
              f"cash {_money(portfolio.cash)} $)")
    held = [] if portfolio is None else list(portfolio.positions.index)
    bbg = {} if wls is None else dict(zip(wls["symbol"],
                                          wls["bloomberg"].str.replace(" Equity", "", regex=False)))
    core_members, core_universe = [], []
    if cfg.core_size and wls is not None:
        core_members = list(_core_pool(wls, cfg, cfg.core_size)["symbol"])
        core_universe = list(_core_pool(wls, cfg)["symbol"])

    start = (date.today() - timedelta(days=WARMUP_DAYS)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    panel = _load_prices(sorted(set(tickers) | set(held) | set(core_members)), wls, start, end,
                         cache=None)
    market_close = None
    if _needs_market(cfg):
        market_close = download_ohlcv([cfg.benchmarks[0]], start, end)["Close"].iloc[:, 0]

    rsi_values, eligible = compute_signals(panel, cfg, market_close, _us_symbols(wls))
    last = panel["Close"].index[-1]
    bullish = False
    if market_close is not None:
        if cfg.market_ma_days:
            ok = market_is_bullish(market_close, panel["Close"].index, cfg.market_ma_days)[-1]
            print(f"Filtre de marché : {cfg.benchmarks[0]} "
                  f"{'au-dessus' if ok else 'en dessous'} de sa moyenne {cfg.market_ma_days} séances"
                  f"{'' if ok else ' -> aucun achat'}")
        if cfg.take_profit_bull_pct:
            bullish = bool(market_is_bullish(market_close, panel["Close"].index,
                                             cfg.bull_ma_days)[-1])
            tp = cfg.take_profit_bull_pct if bullish else cfg.take_profit_pct
            print(f"Tendance : {cfg.benchmarks[0]} {'au-dessus' if bullish else 'en dessous'} de sa "
                  f"moyenne {cfg.bull_ma_days} séances -> take profit à +{tp:.0%}")
    today = eligible.loc[last, eligible.columns.intersection(tickers)]
    hits = today.index[today.to_numpy(dtype=bool)]
    signals = pd.DataFrame({
        "rsi": rsi_values.loc[last, hits],
        "rsi_veille": rsi_values.shift(1).loc[last, hits],
        "dernier_cours": panel["Close"].loc[last, hits],
    }).sort_values("rsi")

    print(f"\nSignaux au {last.date()} : {signal_label(cfg)} -> {len(signals)} action(s)")
    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    if portfolio is None:
        _print_indicative_orders(signals, cfg, bullish, out_dir / f"signals_{last.date()}.csv",
                                 bbg)
    else:
        try:
            plan = plan_orders(signals, portfolio, panel["Close"].ffill().iloc[-1], cfg,
                               core_members=core_members, core_universe=core_universe,
                               bullish=bullish)
        except ValueError as exc:
            raise SystemExit(f"Erreur dans le portefeuille : {exc}")
        _print_portfolio_orders(plan, portfolio, cfg, last, out_dir / f"ordres_{last.date()}.csv",
                                bbg)


def _with_bloomberg(frame: pd.DataFrame, bbg: dict[str, str]) -> pd.DataFrame:
    """Ajoute en tête le ticker Bloomberg, à saisir sur la plateforme du concours."""
    if not bbg:
        return frame
    return frame.assign(bloomberg=[bbg.get(t, "") for t in frame.index])[
        ["bloomberg", *frame.columns]]


def _print_indicative_orders(signals: pd.DataFrame, cfg: StrategyConfig, bullish: bool,
                             out: Path, bbg: dict[str, str]) -> None:
    """Sans fichier de portefeuille : signaux du jour avec un budget indicatif sur `--capital`."""
    if cfg.max_positions is not None:
        signals = signals.head(cfg.max_positions)
    if signals.empty:
        print("Aucune opportunité aujourd'hui.")
        return
    tp = cfg.take_profit_bull_pct if bullish and cfg.take_profit_bull_pct else cfg.take_profit_pct
    budget = cfg.initial_capital * cfg.position_size_pct
    orders = signals.assign(
        quantite_indicative=(budget // signals["dernier_cours"]).astype(int),
        stop_loss_indicatif=signals["dernier_cours"] * (1 - cfg.stop_loss_pct),
        take_profit_indicatif=signals["dernier_cours"] * (1 + tp),
    ).round(2)
    orders = _with_bloomberg(orders, bbg)
    print(orders.to_string())
    print(f"\nCours en dollars. Budget par position : {_money(budget)} $ "
          f"({cfg.position_size_pct:.0%} du capital). "
          f"Acheter dans l'ordre du tableau (RSI le plus bas d'abord) tant qu'il reste du cash, "
          f"puis placer stop loss et take profit à -{cfg.stop_loss_pct:.0%} / +{tp:.0%} du prix "
          f"d'exécution.\n"
          f"Pour des quantités calculées sur votre cash réel, créez portefeuille.csv "
          f"(modèle : portefeuille.exemple.csv).")
    orders.to_csv(out)
    print(f"Enregistré dans {out}")


def _orders_table(plan, cfg: StrategyConfig, bbg: dict[str, str]) -> pd.DataFrame:
    """Tous les ordres de l'ouverture, dans l'ordre où les passer (montants en dollars)."""
    rows = []
    for t, r in plan.core_sells.iterrows():
        rows.append(["1. vente", t, bbg.get(t, ""), "grande valeur", r["quantite"],
                     r["dernier_cours"], r["montant_estime"], None, None])
    for t, r in plan.orders.iterrows():
        rows.append(["2. achat", t, bbg.get(t, ""), "RSI", r["quantite"], r["dernier_cours"],
                     r["montant_estime"], -cfg.stop_loss_pct, plan.take_profit_pct])
    for t, r in plan.core_buys.iterrows():
        rows.append(["3. achat", t, bbg.get(t, ""), "grande valeur", r["quantite"],
                     r["dernier_cours"], r["montant_estime"], None, None])
    return pd.DataFrame(rows, columns=["etape", "ticker", "bloomberg", "type", "quantite",
                                       "dernier_cours_usd", "montant_estime_usd",
                                       "stop_loss_pct", "take_profit_pct"])


def _print_portfolio_orders(plan, portfolio, cfg: StrategyConfig, last, out: Path,
                            bbg: dict[str, str]) -> None:
    """Avec le fichier de portefeuille : ordres exacts à passer à l'ouverture suivante."""
    print(f"\n=== Portefeuille au {last.date()} ===")
    print(f"Valeur totale estimée : {_money(plan.equity)} $ · cash {_money(portfolio.cash)} $ "
          f"({portfolio.cash / plan.equity:.0%}) · taille max d'une position : "
          f"{_money(plan.equity * cfg.position_size_pct)} $ ({cfg.position_size_pct:.0%})")
    if not plan.positions.empty:
        shown = plan.positions[["type", "quantite", "prix_achat", "dernier_cours", "valeur",
                                "poids", "stop_loss"]].round(
            {"prix_achat": 2, "dernier_cours": 2, "valeur": 0, "stop_loss": 2})
        if (shown["quantite"] % 1 == 0).all():
            shown["quantite"] = shown["quantite"].astype(int)
        shown["poids"] = shown["poids"].map("{:.1%}".format)
        shown = shown.rename(columns={"prix_achat": "prix_achat_local", "dernier_cours": "cours_usd",
                                      "valeur": "valeur_usd", "stop_loss": "stop_loss_local"})
        print(_with_bloomberg(shown, bbg).to_string())
    for ticker in plan.unpriced:
        print(f"  ! {ticker} : aucun cours récent trouvé, valorisé au prix d'achat.")
    for ticker, weight in plan.positions["poids"].items():
        if weight > cfg.position_size_pct:
            print(f"  ! {ticker} pèse {weight:.1%} du portefeuille "
                  f"(limite {cfg.position_size_pct:.0%}).")

    table = _orders_table(plan, cfg, bbg)
    print("\n=== Ordres pour l'ouverture, dans cet ordre (cours et montants en dollars) ===")
    if table.empty:
        print("Aucun ordre : " + ("pas de nouveau signal aujourd'hui." if not plan.skipped else
              f"pas assez de cash pour une position d'au moins {cfg.min_position_pct:.0%} "
              f"du portefeuille."))
    else:
        sections = [
            (plan.core_sells, "Vendre ces grandes valeurs (au marché)"),
            (plan.orders, f"Acheter ces actions RSI (au marché), puis placer stop loss "
                          f"-{cfg.stop_loss_pct:.0%} et take profit +{plan.take_profit_pct:.0%} "
                          f"du prix d'exécution"),
            (plan.core_buys, "Acheter ces grandes valeurs avec le cash restant (au marché, "
                             "sans stop ni take profit)"),
        ]
        steps = [(frame, title) for frame, title in sections if not frame.empty]
        for number, (frame, title) in enumerate(steps, start=1):
            shown = frame.round({"rsi": 2, "dernier_cours": 2, "montant_estime": 0})
            if "poids_estime" in shown:
                shown["poids_estime"] = shown["poids_estime"].map("{:.1%}".format)
            print(f"\n{number}. {title} :")
            print(_with_bloomberg(shown, bbg).to_string())
        flows = (plan.core_sells["montant_estime"].sum() - plan.orders["montant_estime"].sum()
                 - plan.core_buys["montant_estime"].sum())
        print(f"\nCash restant estimé : {_money(portfolio.cash + flows)} $")
        print("Ensuite : mettre à jour portefeuille.csv (positions, prix d'achat et cash).")
        table.to_csv(out, index=False)
        print(f"Enregistré dans {out}")
    if plan.skipped:
        print(f"Signaux non retenus faute de cash : {', '.join(plan.skipped)}")


def cmd_import_wls(args) -> None:
    try:
        members, raw_count = import_wls(args.source, args.wls_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Import impossible : {exc}")
    cfg = StrategyConfig()
    us = members["currency"] == "USD"
    print(f"{len(members)} actions membres du WLS enregistrées dans {args.wls_file} "
          f"(sur {raw_count} lignes ; places non reconnues ignorées)")
    print(f"  dont {us.sum()} américaines et {(~us).sum()} cotées sur {members.loc[~us, 'exchange'].nunique()} "
          f"autres places, en {members['currency'].nunique()} devises")
    us_small_mid = us_small_mid_universe(members, cfg.min_market_cap, cfg.max_market_cap)
    print(f"  {len(us_small_mid)} small & mid caps américaines (option --universe us)")
    biggest = ", ".join(_core_pool(members, cfg, cfg.core_size)["symbol"])
    print(f"  {cfg.core_size} plus grandes valeurs pour le cash restant : {biggest}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    {"backtest": cmd_backtest, "scan": cmd_scan, "import-wls": cmd_import_wls}[args.command](args)


if __name__ == "__main__":
    main()
