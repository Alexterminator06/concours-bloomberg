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
| Taille des positions | **20 % de la valeur totale du portefeuille au maximum par position**, sans limite sur le nombre de positions. Sans levier, c'est le cash disponible qui limite : à 20 % par position, cela fait environ 5 positions simultanées. Les signaux sont exécutés du RSI le plus bas au plus haut tant qu'il reste du cash. On n'ouvre pas de position inférieure à 2 % du portefeuille, et une action déjà détenue n'est pas rachetée. |
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
| `--position-size` | 0.20 | taille d'une position (fraction de la valeur totale du portefeuille) |
| `--max-positions` | aucune | nombre maximum de positions simultanées (par défaut, seul le cash limite) |
| `--rsi-threshold` / `--days` | 30 / 2 | seuil du RSI et nombre de séances consécutives |
| `--trend-filter N` | 0 (désactivé) | n'acheter que si l'action est au-dessus de sa moyenne mobile N séances (ex. 200) |
| `--market-filter N` | 0 (désactivé) | n'acheter que si l'indice principal est au-dessus de sa moyenne mobile N séances (ex. 200) |
| `--stop-loss` / `--take-profit` | 0.05 / 0.05 | niveaux de sortie |
| `--take-profit-first` | | si stop et objectif sont touchés la même séance, retenir l'objectif (borne optimiste) |
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

## Diagnostiquer la performance

Les statistiques des trades séparent ce qui vient du signal et ce qui vient de l'exécution :

- **Gain moyen avant frais** : rendement moyen d'un trade sans commission ni glissement, ce que le signal rapporte vraiment ;
- **Frais et glissement payés ($)** : coût total des frais, à comparer au P&L réalisé ;
- **Séances ambiguës** : nombre de trades dont le stop et l'objectif ont été touchés la même séance. Par défaut, ils comptent comme des pertes. Relancer avec `--take-profit-first` donne l'autre extrême ; la réalité se situe entre les deux.

Pour comparer des variantes de la stratégie, enregistrez chaque run dans son propre dossier :

```bash
python -m rsi_strategy backtest --out results/base
python -m rsi_strategy backtest --trend-filter 200 --out results/tendance
python -m rsi_strategy backtest --trend-filter 200 --market-filter 200 --out results/tendance_marche
python -m rsi_strategy backtest --rsi-period 2 --rsi-threshold 10 --days 1 --trend-filter 200 --out results/rsi2
```

Pour ne pas sur-optimiser, choisissez les réglages sur une période (par exemple `--start 2019-01-01 --end 2022-12-31`), puis vérifiez-les sur une autre (`--start 2023-01-01`).

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
