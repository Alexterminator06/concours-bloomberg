"""Affichage console, export CSV et graphique de la comparaison stratégie / indices."""

from pathlib import Path

import pandas as pd

# Palette catégorielle (ordre fixe : stratégie puis indices) et couleurs de texte / fond.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"

PCT_KEYS = {"Rendement total", "Rendement relatif", "Rendement annualisé", "Volatilité annualisée",
            "Max drawdown", "Alpha annualisé", "Tracking error", "Taux de réussite", "Gain moyen par trade",
            "Gain moyen (gagnants)", "Perte moyenne (perdants)", "Exposition moyenne",
            "Gain moyen avant frais", "Part moyenne en grandes valeurs",
            "Séances sans grandes valeurs", "Stop moyen sous le prix d'achat",
            "Stop médian sous le prix d'achat"}
MONEY_KEYS = {"Valeur finale ($)", "P&L total réalisé ($)", "Frais et glissement payés ($)",
              "P&L grandes valeurs ($)"}


def _fmt(key: str, value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/d"
    if key in PCT_KEYS:
        return f"{value:+.2%}" if "Rendement" in key or "Alpha" in key else f"{value:.2%}"
    if key in MONEY_KEYS:
        return f"{value:,.0f}".replace(",", " ")
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_table(stats: dict[str, dict]) -> pd.DataFrame:
    """{nom de colonne: {indicateur: valeur}} -> tableau de chaînes formatées."""
    df = pd.DataFrame(stats)
    return df.apply(lambda col: [_fmt(k, v) for k, v in zip(df.index, col)])


def print_section(title: str, table: pd.DataFrame | dict) -> None:
    print(f"\n=== {title} ===")
    if isinstance(table, dict):
        width = max(len(k) for k in table)
        for k, v in table.items():
            print(f"  {k:<{width}}  {_fmt(k, v)}")
    else:
        print(table.to_string())


def save_csv(out_dir: Path, **frames: pd.DataFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(out_dir / f"{name}.csv", index=isinstance(df.index, pd.DatetimeIndex))


def plot_performance(curves: pd.DataFrame, path: Path, title: str) -> None:
    """Deux panneaux : valeur du portefeuille (base 1 M$) et drawdown, stratégie vs indices."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, PercentFormatter

    plt.rcParams.update({
        "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_SECONDARY,
        "xtick.color": TEXT_SECONDARY, "ytick.color": TEXT_SECONDARY, "text.color": TEXT_PRIMARY,
    })
    fig, (ax_eq, ax_dd) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True,
                                       gridspec_kw={"height_ratios": [3, 1.3]})
    fig.patch.set_facecolor(SURFACE)

    drawdowns = curves / curves.cummax() - 1
    for k, name in enumerate(curves.columns):
        color = SERIES_COLORS[k % len(SERIES_COLORS)]
        series = curves[name]
        ax_eq.plot(series.index, series, color=color, linewidth=2.0 if k == 0 else 1.6,
                   label=name, zorder=3 if k == 0 else 2)
        # Étiquette directe en bout de courbe (texte neutre, la couleur est portée par le trait).
        ax_eq.annotate(f"{name}  {series.iloc[-1] / series.iloc[0] - 1:+.1%}",
                       xy=(series.index[-1], series.iloc[-1]), xytext=(6, 0),
                       textcoords="offset points", va="center", fontsize=9, color=TEXT_PRIMARY)
        ax_dd.plot(drawdowns.index, drawdowns[name], color=color, linewidth=1.4)

    ax_eq.set_title(title, loc="left", fontsize=12, color=TEXT_PRIMARY, pad=12)
    ax_eq.set_ylabel("Valeur du portefeuille ($)")
    ax_eq.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:.2f} M"))
    ax_eq.legend(loc="upper left", frameon=False, fontsize=9)
    ax_dd.set_ylabel("Drawdown")
    ax_dd.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))

    for ax in (ax_eq, ax_dd):
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    ax_eq.margins(x=0.0)
    ax_eq.set_xlim(curves.index[0], curves.index[-1] + (curves.index[-1] - curves.index[0]) * 0.18)

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
