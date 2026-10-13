# Stratégie RSI – small & mid caps US

Stratégie de retour à la moyenne, positions longues uniquement :
on achète les small et mid caps US en tendance haussière qui viennent de subir un repli brutal
(RSI(2) sous 10), avec un stop loss et un take profit à 5 %. Le cash inutilisé est placé dans
les plus grandes valeurs US. La performance est comparée
au WLS, l'indice du concours Bloomberg. Les cours viennent de Yahoo Finance (via `yfinance`).

## Règles

| Élément | Règle |
|---|---|
| Univers | **Small & mid caps US membres du WLS** : actions cotées aux États-Unis dont la capitalisation retenue par l'indice va de **300 M$ à 10 Md$** (small caps 300 M–2 Md$, mid caps 2–10 Md$). Seuls les membres du WLS sont autorisés dans le concours ; la liste s'importe depuis Bloomberg (voir plus bas). Sans elle, l'univers vient du screener Yahoo Finance (cours > 5 $, volume moyen sur 3 mois > 100 000 titres). |
| Signal d'achat | **RSI(2) < 10** à la clôture, et cours **au-dessus de sa moyenne mobile 200 séances** (RSI de Wilder, calculé sur les cours de clôture ajustés). La règle d'origine, RSI(14) < 30 sur 2 séances consécutives sans filtre de tendance, reste disponible : `--rsi-period 14 --rsi-threshold 30 --days 2 --trend-filter 0`. |
| Fréquence | **Un scan par jour**, après la clôture. L'achat se fait à l'ouverture de la séance suivante, ce qui évite tout biais d'anticipation. |
| Stop loss | **-5 %** par rapport au prix d'achat. |
| Take profit | **+5 %** par rapport au prix d'achat. Un objectif relevé à +10 % quand le marché est en tendance haussière existe en option (`--take-profit-bull 0.10`), mais il fait moins bien dans les backtests (voir plus bas). |
| Cash restant | Placé dans les **10 plus grandes capitalisations US du WLS** (une catégorie d'actions par société), à parts à peu près égales, sans stop ni take profit, pour suivre le marché au lieu de dormir. Elles sont vendues en priorité, en partant des plus grosses lignes, pour financer les achats RSI. |
| Sens | Positions longues uniquement, sans levier. |
| Capital | **1 000 000 $**. |
| Taille des positions | **20 % de la valeur totale du portefeuille au maximum par position**, sans limite sur le nombre de positions. Sans levier, c'est le cash disponible qui limite : à 20 % par position, cela fait environ 5 positions simultanées. Les signaux sont exécutés du RSI le plus bas au plus haut tant qu'il reste du cash. On n'ouvre pas de position inférieure à 2 % du portefeuille, et une action déjà détenue n'est pas rachetée. |
| Indice de comparaison | L'indice du concours est le **WLS** (Bloomberg World Large, Mid & Small Cap : actions mondiales de toutes tailles, hors dividendes). Il n'est pas disponible sur Yahoo Finance : le backtest utilise **VT** (Vanguard Total World Stock), l'ETF qui s'en rapproche le plus, comme indice principal. **SMMD** (Russell 2500) et **IWM** (Russell 2000), qui représentent les small & mid caps US, sont affichés à titre indicatif. On compare en achat-conservation, cours ajustés des dividendes, et la ligne « Rendement relatif » donne l'écart de rendement total avec chaque indice. |

Hypothèses du backtest. Elles portent sur des points que l'énoncé ne précisait pas, et toutes se modifient en ligne de commande :

- **Gap à l'ouverture** : si le cours ouvre déjà sous le stop (ou au-dessus de l'objectif), la position est vendue au cours d'ouverture.
- **Stop et objectif touchés la même séance** : les données journalières ne disent pas lequel est venu en premier. Par prudence, on retient le stop.
- **Coûts** : aucun par défaut, car le concours Bloomberg ne facture pas de frais de transaction. On peut simuler une commission et un glissement avec `--commission-bps` et `--slippage-bps`. Le glissement s'applique aux achats et aux stops ; le take profit est un ordre limite, donc sans glissement.
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

## Routine quotidienne pendant le concours

0. **Une seule fois** : importez la liste des membres du WLS, exportée du terminal Bloomberg
   (`WLS Index MEMB <GO>`, export Excel avec les colonnes Ticker, Name, Weight, Shares, Price) :

   ```bash
   python -m rsi_strategy import-wls "chemin/vers/WLS_as_of_Oct_13_2026.xlsx"
   ```

   Les membres US sont enregistrés dans `data/wls_members.csv` (non envoyé sur GitHub).
1. **Chaque soir, après la clôture US** (22h, heure de Paris), mettez à jour `portefeuille.csv`
   avec vos positions et votre cash, tels qu'affichés par la plateforme du concours. Partez du
   modèle `portefeuille.exemple.csv` :

   ```csv
   ticker,quantite,prix_achat
   CASH,612500,
   KFY,2800,71.02
   MOV,6400,31.33
   ```

   La ligne `CASH` donne le cash disponible. Le fichier peut aussi être enregistré depuis Excel
   (séparateur `;` et virgule décimale acceptés). Il n'est pas envoyé sur GitHub.
2. **Lancez le scan** : `python -m rsi_strategy scan`. Il affiche la valeur du portefeuille, le
   poids de chaque position, puis les **ordres de l'ouverture, dans l'ordre où les passer** :
   1. les ventes de grandes valeurs, pour financer les achats RSI ;
   2. les achats RSI, du RSI le plus bas au plus haut, 20 % maximum de la valeur totale chacun ;
   3. les achats de grandes valeurs avec le cash restant.

   Les quantités prévoient une marge de 2 %, au cas où les cours d'ouverture s'écarteraient de la
   dernière clôture. Les ordres sont aussi enregistrés dans `results/ordres_<date>.csv`.
3. **À l'ouverture suivante**, passez ces ordres au marché, dans l'ordre affiché.
4. **Dès qu'un achat RSI est exécuté**, placez sur la plateforme un stop loss à -5 % et un take
   profit à +5 % du prix d'exécution. Les ventes RSI se font ensuite toutes seules. Les grandes
   valeurs n'ont ni stop ni take profit.

Sans fichier `portefeuille.csv`, le scan affiche les signaux du jour avec des quantités
indicatives calculées sur `--capital`.

Options utiles (`python -m rsi_strategy backtest --help` pour la liste complète) :

| Option | Défaut | Rôle |
|---|---|---|
| `--capital` | 1000000 | capital de départ |
| `--position-size` | 0.20 | taille d'une position (fraction de la valeur totale du portefeuille) |
| `--max-positions` | aucune | nombre maximum de positions simultanées (par défaut, seul le cash limite) |
| `--rsi-period` | 2 | nombre de séances du RSI |
| `--rsi-threshold` / `--days` | 10 / 1 | seuil du RSI et nombre de séances consécutives sous ce seuil |
| `--trend-filter N` | 200 | n'acheter que si l'action est au-dessus de sa moyenne mobile N séances (0 = désactivé) |
| `--market-filter N` | 0 (désactivé) | n'acheter que si l'indice principal est au-dessus de sa moyenne mobile N séances (ex. 200) |
| `--stop-loss` / `--take-profit` | 0.05 / 0.05 | niveaux de sortie |
| `--take-profit-bull` | 0 (désactivé) | take profit quand VT est au-dessus de sa moyenne 200 séances (ex. 0.10) |
| `--core-size` | 10 | nombre de grandes valeurs pour le cash restant (0 = le cash reste en cash) |
| `--core-min` | 0 | part du portefeuille réservée aux grandes valeurs, ex. 0.5 : les achats RSI n'utilisent que le reste |
| `--wls-file` | `data/wls_members.csv` | membres US du WLS, créés par `import-wls` |
| `--take-profit-first` | | si stop et objectif sont touchés la même séance, retenir l'objectif (borne optimiste) |
| `--commission-bps` / `--slippage-bps` | 0 / 0 | coûts de transaction, en points de base (1 pb = 0,01 %) |
| `--benchmarks` | VT SMMD IWM | ETF de comparaison (le premier est l'indice principal) |
| `--max-tickers N` | tous | limite l'univers aux N plus grosses capitalisations, pour un essai rapide |
| `--refresh-universe` | | sans liste WLS : ré-interroge le screener Yahoo au lieu de relire `data/universe.csv` |
| `--refresh-data` | | ignore le cache des cours (`data/cache/`) |

Les cours téléchargés sont mis en cache dans `data/cache/` (environ 90 Mo par période pour
2 000 actions). Seuls les 4 fichiers les plus récents sont conservés, et le dossier peut être
supprimé sans risque.

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
python -m rsi_strategy backtest --out results/base            # réglages par défaut
python -m rsi_strategy backtest --rsi-threshold 15 --out results/seuil15
python -m rsi_strategy backtest --market-filter 200 --out results/marche
python -m rsi_strategy backtest --rsi-period 14 --rsi-threshold 30 --days 2 --trend-filter 0 --out results/regle_origine
```

Pour ne pas sur-optimiser, choisissez les réglages sur une période (par exemple `--start 2019-01-01 --end 2022-12-31`), puis vérifiez-les sur une autre (`--start 2023-01-01`).

## Pourquoi ces réglages

Backtests sans frais, sur l'univers actuel de 2 121 actions, avec les réglages de positions par
défaut. Les réglages ont été comparés sur 2023-2026, puis vérifiés sur 2019-2023 :

| Variante | 2019-2023 | 2023-2026 |
|---|---|---|
| Règle d'origine : RSI(14) < 30 deux séances | non testé | +5 % · 0,14 %/trade |
| RSI(14) < 30 deux séances + tendance 200 | +11 % · 0,21 %/trade | +19 % · 0,36 %/trade |
| **RSI(2) < 10 + tendance 200 (défaut)** | **+166 % · 0,66 %/trade** | **+42 % · 0,41 %/trade** |
| Même chose + filtre de marché 200 | +50 % · 0,45 %/trade | non testé |
| Même chose, take profit à 10 % | +48 % | +55 % · 0,96 %/trade |
| Même chose, take profit 10 % si VT > moyenne 200 j, sinon 5 % | +34 % · 0,34 %/trade | +17 % · 0,28 %/trade |
| Même chose, positions de 15 % | +103 % · 0,43 %/trade | +40 % · 0,34 %/trade |
| Même chose, positions de 10 % | +78 % · 0,38 %/trade | +18 % · 0,15 %/trade |
| *Indice SMMD, small & mid caps US (achat-conservation)* | *+49 %* | *+66 %* |

Les résultats du RSI(2) restent proches avec des seuils de 5 ou 15 : l'avantage ne dépend pas d'un
réglage précis. Le filtre de marché réduit le drawdown mais coûte beaucoup de rendement ; il est
désactivé par défaut. Un take profit fixe à 10 % fait mieux sur 2023-2026, marché porteur, mais
beaucoup moins bien sur 2019-2023. Le relever à 10 % seulement quand le marché est en tendance
haussière fait moins bien que les deux objectifs fixes, sur les deux périodes : les rebonds les plus
forts suivent les corrections du marché, quand la tendance est baissière. L'objectif reste donc
fixé à 5 %. Des positions plus
petites obligent à prendre des signaux moins survendus, qui rapportent moins : on garde 20 %, et il
faut respecter l'ordre du scan. Ces chiffres sont flattés par le biais du survivant (voir
ci-dessous).

Ces backtests ont été faits sur l'univers Yahoo, qui contenait des actions refusées par le
concours (fonds cotés comme OXLC ou KYN, sociétés étrangères…). Limitée aux membres du WLS, la
stratégie est nettement moins bonne :

| RSI(2) < 10 + tendance 200, take profit 5 % | 2019-2023 | 2023-2026 | Environ 7 ans |
|---|---|---|---|
| Univers Yahoo | +166 % · 0,66 %/trade | +42 % · 0,41 %/trade | +278 % |
| **Univers WLS (actions autorisées)** | **+66 % · 0,30 %/trade** | **+20 % · 0,18 %/trade** | **+99 %** |
| Indice VT (≈ WLS) | +57 % | +80 % | +182 % |

Sur l'univers WLS, le gain moyen par trade reste positif (environ +0,25 % sur ~1 750 trades), mais
la stratégie ne bat VT que sur 2019-2023. Avec le cash restant seulement, les grandes valeurs ne
pèsent en moyenne que 0,2 à 1,4 % du portefeuille : les achats RSI prennent presque tout le cash.
L'option `--core-min` leur réserve une part fixe, pour suivre davantage le marché :

```bash
python -m rsi_strategy backtest --core-min 0.5 --out results/gv50
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --core-min 0.5 --out results/oos_gv50
```

Dans le backtest, les grandes valeurs sont choisies chaque mois parmi les 100 plus gros membres
actuels du WLS, d'après leur capitalisation **à l'époque** (titres de l'indice aujourd'hui × cours
de la veille). Les choisir d'après leur taille actuelle reviendrait à acheter en 2019 les gagnantes
connues en 2026.

## Limites

- **Biais du survivant** : l'univers correspond aux small et mid caps *d'aujourd'hui*. Les sociétés
  disparues (faillites, rachats) ou passées en large cap entre-temps n'y figurent pas, ce qui
  tend à flatter le backtest.
- **Données journalières** : l'ordre de passage du stop et de l'objectif dans une même séance est
  inconnu. Le cas est traité de façon prudente (voir plus haut).
- **VT et le WLS** : le WLS ne compte pas les dividendes, alors que VT est comparé dividendes
  réinvestis. VT surestime donc le WLS d'environ son rendement en dividendes (de l'ordre de 2 %
  par an), ce qui rend la comparaison un peu défavorable à la stratégie.
- **Actions autorisées** : le concours n'accepte que les actions membres du WLS. Importez la liste
  (`import-wls`) pour que l'univers s'y limite ; sans elle, vérifiez chaque action sur le terminal.
  La composition change au fil du temps : réimportez une liste récente si besoin.
- **SMMD** n'existe que depuis juillet 2017. Pour un backtest antérieur, utiliser par exemple
  `--benchmarks VT IWM`.

## Tests

```bash
python -m pytest
```

Les tests utilisent des données synthétiques et ne nécessitent pas de connexion réseau.
