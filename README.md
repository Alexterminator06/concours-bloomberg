# Stratégie RSI – small & mid caps US

Stratégie de retour à la moyenne, positions longues uniquement :
on achète les small et mid caps survendues (RSI sous 30 pendant 2 séances), avec un stop loss et
un take profit à 5 %. La performance est comparée à un indice équivalent. Toutes les données
viennent de Yahoo Finance (via `yfinance`).

## Règles

| Élément | Règle |
|---|---|
| Univers | Actions US cotées au NYSE / Nasdaq avec une capitalisation de **300 M$ à 10 Md$** (small caps 300 M–2 Md$, mid caps 2–10 Md$), un cours > 5 $ et un volume moyen sur 3 mois > 100 000 titres. La liste vient du screener Yahoo Finance. |
| Signal d'achat | **RSI(14) < 30 sur 2 séances consécutives** (RSI de Wilder, calculé sur les cours de clôture ajustés). |
| Fréquence | **Un scan par jour**, après la clôture. L'achat se fait à l'ouverture de la séance suivante, ce qui évite tout biais d'anticipation. |
| Stop loss | **-5 %** par rapport au prix d'achat. |
| Take profit | **+5 %** par rapport au prix d'achat. |
| Sens | Positions longues uniquement, sans levier. |
| Capital | **1 000 000 $**. |
| Taille des positions | 5 % du portefeuille par position, 20 positions au maximum. Quand il y a plus de signaux que de places, on achète les RSI les plus bas en premier. Une action déjà détenue n'est pas rachetée. |
| Indice de comparaison | **SMMD** (iShares Russell 2500 : small + mid caps US) comme indice principal. **IWM** (Russell 2000) et **IJH** (S&P MidCap 400) sont affichés à titre indicatif. On compare en achat-conservation, cours ajustés des dividendes. |

Hypothèses du backtest. Elles portent sur des points que l'énoncé ne précisait pas, et toutes se modifient en ligne de commande :

- **Gap à l'ouverture** : si le cours ouvre déjà sous le stop (ou au-dessus de l'objectif), la position est vendue au cours d'ouverture.
- **Stop et objectif touchés la même séance** : les données journalières ne disent pas lequel est venu en premier. Par prudence, on retient le stop.
- **Coûts** : 5 pb de commission par ordre, plus 5 pb de glissement sur les achats et les stops. Le take profit est un ordre limite, donc sans glissement.
- **Liquidité** : au moment du signal, on exige un cours ≥ 5 $ et un volume moyen sur 20 séances ≥ 1 M$ par jour.

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

```bash
# Backtest sur les 3 dernières années (par défaut), comparé aux indices
python -m rsi_strategy backtest

# Période personnalisée
python -m rsi_strategy backtest --start 2021-01-01 --end 2025-12-31

# Opportunités du jour (à lancer une fois par jour après la clôture US, 22h heure de Paris)
python -m rsi_strategy scan
```

Options utiles (`python -m rsi_strategy backtest --help` pour la liste complète) :

| Option | Défaut | Rôle |
|---|---|---|
| `--capital` | 1000000 | capital de départ |
| `--position-size` | 0.05 | taille d'une position (fraction du portefeuille) |
| `--max-positions` | 20 | nombre maximum de positions simultanées |
| `--rsi-threshold` / `--days` | 30 / 2 | seuil du RSI et nombre de séances consécutives |
| `--stop-loss` / `--take-profit` | 0.05 / 0.05 | niveaux de sortie |
| `--commission-bps` / `--slippage-bps` | 5 / 5 | coûts de transaction |
| `--benchmarks` | SMMD IWM IJH | ETF de comparaison (le premier est l'indice principal) |
| `--max-tickers N` | tous | limite l'univers aux N plus grosses capitalisations, pour un essai rapide |
| `--refresh-universe` | | ré-interroge le screener Yahoo au lieu de relire `data/universe.csv` |
| `--refresh-data` | | ignore le cache des cours (`data/cache/`) |

Au premier lancement, l'univers est enregistré dans `data/universe.csv`. On peut éditer ce fichier
ou le remplacer par sa propre liste : seule la colonne `symbol` est obligatoire.

## Résultats

Le backtest affiche dans la console et écrit dans `results/` :

- `performance.png` : valeur du portefeuille et drawdown de la stratégie et des indices ;
- `summary.csv` : rendement total et annualisé, volatilité, Sharpe, Sortino, drawdown maximum, Calmar, bêta, alpha, corrélation, ratio d'information ;
- `trades.csv` : chaque trade (signal, entrée, sortie, motif, P&L) ;
- `equity_curve.csv` : valeur quotidienne de la stratégie et des indices ;
- `open_positions.csv` : positions encore ouvertes en fin de période, avec leurs niveaux de stop et d'objectif ;
- `next_orders.csv` : ordres à passer à l'ouverture suivante.

## Limites

- **Biais du survivant** : l'univers correspond aux small et mid caps *d'aujourd'hui*. Les sociétés
  disparues (faillites, rachats) ou passées en large cap entre-temps n'y figurent pas, ce qui
  tend à flatter le backtest.
- **Données journalières** : l'ordre de passage du stop et de l'objectif dans une même séance est
  inconnu. Le cas est traité de façon prudente (voir plus haut).
- **SMMD** n'existe que depuis juillet 2017. Pour un backtest antérieur, utiliser par exemple
  `--benchmarks IWM IJH`.

## Tests

```bash
python -m pytest
```

Les tests utilisent des données synthétiques et ne nécessitent pas de connexion réseau.
