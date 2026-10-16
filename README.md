# Stratégie RSI – actions mondiales du WLS

Stratégie de retour à la moyenne, positions longues uniquement :
parmi les quelque 10 000 actions de l'indice WLS (l'indice du concours Bloomberg), on achète
celles en tendance haussière qui viennent de subir un repli brutal (RSI(2) sous 10), avec un stop
loss et un take profit à 5 %. Au moins la moitié du portefeuille, plus le cash que les achats RSI
n'utilisent pas, est placée dans 10 grandes valeurs du WLS : chaque mois, celles qui ont le plus
monté sur 12 mois parmi les 50 plus grandes (momentum), 3 au plus par secteur. Les cours viennent de Yahoo Finance (via
`yfinance`) et sont convertis en dollars.

## Règles

| Élément | Règle |
|---|---|
| Univers | **Tous les membres du WLS**, toutes places et toutes tailles (seuls les membres du WLS sont autorisés dans le concours) : environ 10 350 actions sur 49 places et en 36 devises, sauf les 100 plus grandes, réservées à la part grandes valeurs. La liste s'importe depuis Bloomberg (voir plus bas). Les tickers Bloomberg sont convertis en symboles Yahoo et les cours en dollars ; les actions que Yahoo ne connaît pas sont ignorées (voir les limites). `--universe us` restreint aux small & mid caps américaines du WLS (capitalisation de 300 M$ à 10 Md$). Sans liste WLS, l'univers vient du screener Yahoo Finance (small & mid caps US). |
| Signal d'achat | **RSI(2) < 10** à la clôture, et cours **au-dessus de sa moyenne mobile 200 séances** (RSI de Wilder, calculé sur les cours de clôture ajustés). La règle d'origine, RSI(14) < 30 sur 2 séances consécutives sans filtre de tendance, reste disponible : `--rsi-period 14 --rsi-threshold 30 --days 2 --trend-filter 0`. |
| Fréquence | **Un scan par jour**, après la clôture. L'achat se fait à l'ouverture de la séance suivante, ce qui évite tout biais d'anticipation. |
| Stop loss | **-5 %** par rapport au prix d'achat. |
| Take profit | **+5 %** par rapport au prix d'achat. Un objectif relevé à +10 % quand le marché est en tendance haussière existe en option (`--take-profit-bull 0.10`), mais il fait moins bien dans les backtests (voir plus bas). |
| Grandes valeurs | **Au moins 50 % du portefeuille**, plus le cash que les achats RSI n'utilisent pas, dans **10 grandes valeurs du WLS choisies chaque mois par momentum** : parmi les 50 plus grandes capitalisations (une catégorie d'actions par société), les 10 qui ont le plus monté sur 12 mois, sans compter le dernier mois, **3 au plus par secteur** (une fois 3 valeurs d'un secteur retenues, on passe aux suivantes du classement ; secteurs Yahoo Finance, gardés dans `data/secteurs.csv`). Elles sont détenues à parts à peu près égales, sans stop ni take profit. `--core-rel-momentum 0` revient aux 10 plus grandes capitalisations, `--core-sector-max 0` supprime le plafond. Les achats RSI n'utilisent donc que l'autre moitié ; au-delà des 50 %, les grandes valeurs sont vendues en priorité, en partant des plus grosses lignes, pour les financer. `--core-min 0` revient à placer seulement le cash restant (0,2 à 1,4 % du portefeuille en moyenne). |
| Sens | Positions longues uniquement, sans levier. |
| Capital | **1 000 000 $**. |
| Taille des positions | **20 % de la valeur totale du portefeuille au maximum par position**, sans limite sur le nombre de positions. Sans levier, c'est le cash disponible qui limite : à 20 % par position, cela fait environ 5 positions simultanées. Les signaux sont exécutés du RSI le plus bas au plus haut tant qu'il reste du cash. On n'ouvre pas de position inférieure à 2 % du portefeuille, et une action déjà détenue n'est pas rachetée. |
| Indice de comparaison | L'indice du concours est le **WLS** (Bloomberg World Large, Mid & Small Cap : actions mondiales de toutes tailles, hors dividendes). Il n'est pas disponible sur Yahoo Finance : le backtest utilise **VT** (Vanguard Total World Stock), l'ETF qui s'en rapproche le plus, comme indice principal. **SMMD** (Russell 2500) et **IWM** (Russell 2000), qui représentent les small & mid caps US, sont affichés à titre indicatif. On compare en achat-conservation, cours ajustés des dividendes, et la ligne « Rendement relatif » donne l'écart de rendement total avec chaque indice. |

Hypothèses du backtest. Elles portent sur des points que l'énoncé ne précisait pas, et toutes se modifient en ligne de commande :

- **Gap à l'ouverture** : si le cours ouvre déjà sous le stop (ou au-dessus de l'objectif), la position est vendue au cours d'ouverture.
- **Stop et objectif touchés la même séance** : les données journalières ne disent pas lequel est venu en premier. Par prudence, on retient le stop.
- **Coûts** : aucun par défaut, car le concours Bloomberg ne facture pas de frais de transaction. On peut simuler une commission et un glissement avec `--commission-bps` et `--slippage-bps`. Le glissement s'applique aux achats et aux stops ; le take profit est un ordre limite, donc sans glissement.
- **Liquidité** : au moment du signal, on exige un volume moyen sur 20 séances ≥ 1 M$ par jour, et un cours ≥ 5 $ pour les actions américaines (ailleurs, un cours bas en devise locale n'indique pas une action marginale).
- **Bourses du monde** : chaque bourse a ses jours fériés. Les indicateurs sont calculés sur le dernier cours connu, et un signal n'est retenu que si l'action a coté ce jour-là. Un ordre est annulé si sa bourse est fermée le lendemain.

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

   Les membres sont enregistrés dans `data/wls_members.csv` (non envoyé sur GitHub). Si la liste
   a été importée avec une version antérieure (membres US seulement), réimportez-la.
1. **Chaque soir, après la clôture US** (22h, heure de Paris), mettez à jour `portefeuille.csv`
   avec vos positions et votre cash, tels qu'affichés par la plateforme du concours. Partez du
   modèle `portefeuille.exemple.csv` :

   ```csv
   ticker,montant_usd,prix_achat
   CASH,612500,
   KFY,198856,71.02
   7203 JT,36700,2984
   ```

   `montant_usd` est la valeur de chaque position en dollars, telle qu'affichée par la plateforme ;
   la ligne `CASH` donne le cash disponible, en dollars. Le scan en déduit le nombre d'actions au
   dernier cours, arrondi en dessous pour ne jamais vendre plus que ce qui est détenu. On peut aussi
   donner le nombre d'actions dans une colonne `quantite` (il l'emporte sur le montant quand les
   deux sont remplis). Les tickers peuvent être recopiés tels que la plateforme les affiche
   (`7203 JT` ou `7203 JT Equity`) ou au format Yahoo (`7203.T`). Le prix d'achat, facultatif, est
   en devise de cotation ; il ne sert qu'à afficher le niveau du stop. Le fichier peut aussi être enregistré depuis Excel
   (séparateur `;` et virgule décimale acceptés). Il n'est pas envoyé sur GitHub.
2. **Lancez le scan** : `python -m rsi_strategy scan`. Il affiche la valeur du portefeuille, le
   poids de chaque position, puis les **ordres de l'ouverture, dans l'ordre où les passer** :
   1. les ventes de grandes valeurs : celles qui sortent de la sélection du mois, et de quoi
      financer les achats RSI ;
   2. les achats RSI, du RSI le plus bas au plus haut, 20 % maximum de la valeur totale chacun,
      dans la limite de la moitié du portefeuille ;
   3. les achats de grandes valeurs, pour qu'elles pèsent au moins 50 % du portefeuille, avec le
      cash restant.

   Le scan affiche aussi la sélection du mois, avec la hausse sur 12 mois et le secteur de chaque
   grande valeur.
   Elle ne change qu'au début de chaque mois ; le premier scan avec le momentum remplace d'un coup
   les grandes valeurs détenues qui n'en font pas partie.

   Chaque ordre indique le ticker Bloomberg à saisir sur la plateforme. Cours et montants sont en
   dollars. Les quantités prévoient une marge de 2 %, au cas où les cours d'ouverture
   s'écarteraient de la dernière clôture. Les ordres sont aussi enregistrés dans
   `results/ordres_<date>.csv`.
3. **Passez ces ordres au marché**, dans l'ordre affiché : ils s'exécutent à l'ouverture de leur
   bourse. Les places asiatiques ouvrent dans la nuit (heure de Paris), l'Europe à 9h et
   Wall Street à 15h30.
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
| `--stop-atr K` | 0 (désactivé) | backtest : stop loss à K × ATR(14) sous le prix d'achat (ATR = amplitude moyenne d'une séance, gaps compris, mesurée à la clôture du signal), au lieu du pourcentage de `--stop-loss` |
| `--take-profit-bull` | 0 (désactivé) | take profit quand VT est au-dessus de sa moyenne 200 séances (ex. 0.10) |
| `--core-size` | 10 | nombre de grandes valeurs pour le cash restant (0 = le cash reste en cash) |
| `--core-min` | 0.5 | part du portefeuille réservée aux grandes valeurs ; les achats RSI n'utilisent que le reste (0 = seulement le cash restant) |
| `--core-abs-momentum MOIS` | 0 (désactivé) | momentum absolu : au début de chaque mois, si VT a baissé sur ce nombre de mois (ex. 12), les grandes valeurs sont vendues et leur part reste en cash jusqu'au mois suivant |
| `--core-rel-momentum MOIS` | 12 | momentum relatif : chaque mois, garder les `--core-size` grandes valeurs qui ont le plus monté sur ce nombre de mois, hors dernier mois, parmi les `--core-momentum-pool` plus grandes (0 = les plus grandes capitalisations) |
| `--core-momentum-pool N` | 50 | nombre de plus grandes capitalisations parmi lesquelles le momentum relatif choisit |
| `--core-sector-max N` | 3 | au plus N grandes valeurs du même secteur, en descendant le classement du mois (0 = pas de plafond) ; les secteurs viennent de Yahoo Finance et sont gardés dans `data/secteurs.csv` (modifiable à la main) |
| `--core-inverse-vol` | | backtest : pondérer chaque grande valeur par l'inverse de sa volatilité sur 63 séances, avec un rééquilibrage chaque mois, au lieu de parts à peu près égales |
| `--core-candidates N` | 2000 | backtest : chercher chaque mois les plus grandes capitalisations de l'époque parmi les N plus grandes actuelles du WLS, pour retrouver celles qui ne sont plus parmi les plus grandes aujourd'hui (biais du survivant ; 0 = les 100 plus grandes) |
| `--universe` | monde | `monde` : tous les membres du WLS ; `us` : small & mid caps américaines du WLS |
| `--wls-file` | `data/wls_members.csv` | membres du WLS, créés par `import-wls` |
| `--take-profit-first` | | si stop et objectif sont touchés la même séance, retenir l'objectif (borne optimiste) |
| `--commission-bps` / `--slippage-bps` | 0 / 0 | coûts de transaction, en points de base (1 pb = 0,01 %) |
| `--benchmarks` | VT SMMD IWM | ETF de comparaison (le premier est l'indice principal) |
| `--max-tickers N` | tous | limite l'univers aux N plus grosses capitalisations, pour un essai rapide |
| `--refresh-universe` | | sans liste WLS : ré-interroge le screener Yahoo au lieu de relire `data/universe.csv` |
| `--refresh-data` | | ignore le cache des cours (`data/cache/`) |

Les cours téléchargés sont mis en cache dans `data/cache/` (environ 40 Mo par période pour
l'univers US, 250 Mo pour l'univers mondial). Un backtest relancé sur la même période réutilise
ces cours au lieu de les retélécharger. Le cache garde les fichiers les plus récemment utilisés,
dans la limite de 1,5 Go (`MAX_CACHE_BYTES` dans `rsi_strategy/data.py`), et le dossier peut être
supprimé sans risque. La date de fin par défaut étant le jour même, un backtest lancé le lendemain
retélécharge tout : pour comparer plusieurs runs sur plusieurs jours, fixez `--end`.

Au premier lancement, l'univers est enregistré dans `data/universe.csv`. On peut éditer ce fichier
ou le remplacer par sa propre liste : seule la colonne `symbol` est obligatoire.

## Résultats

Le backtest affiche dans la console et écrit dans `results/` :

- `performance.png` : valeur du portefeuille et drawdown de la stratégie et des indices ;
- `summary.csv` : rendement total et annualisé, volatilité, Sharpe, Sortino, drawdown maximum, Calmar, bêta, alpha, corrélation, ratio d'information ;
- `trades.csv` : chaque trade (signal, entrée, sortie, motif, P&L) ;
- `equity_curve.csv` : valeur quotidienne de la stratégie et des indices ;
- `open_positions.csv` : positions encore ouvertes en fin de période, avec leurs niveaux de stop et d'objectif ;
- `next_orders.csv` : ordres à passer à l'ouverture suivante ;
- `grandes_valeurs.csv` : chaque grande valeur détenue, avec le nombre de mois où elle a été retenue, le premier et le dernier de ces mois et son P&L réalisé et latent.
- `grandes_valeurs_jour.csv` : P&L de chaque grande valeur à chaque séance ; la console affiche les 8 séances où les grandes valeurs ont le plus varié, avec l'action qui y a le plus contribué.

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
| Univers WLS (actions autorisées), RSI seul | +66 % · 0,30 %/trade | +20 % · 0,18 %/trade | +99 % |
| Univers WLS US + 50 % réservés aux 10 plus grandes valeurs (ancien run) | +80 % | +101 % | +275 % |
| Indice VT (≈ WLS) | +57 % | +80 % | +182 % |

Sur l'univers WLS, le gain moyen par trade reste positif (environ +0,25 % sur ~1 750 trades), mais
la stratégie ne bat VT que sur 2019-2023. Avec le cash restant seulement, les grandes valeurs ne
pèsent en moyenne que 0,2 à 1,4 % du portefeuille : les achats RSI prennent presque tout le cash.
Leur réserver la moitié du portefeuille (`--core-min 0.5`, le défaut) a d'abord été testé sur
l'univers américain, et a fait mieux que VT sur les deux périodes : +23 points sur 2019-2023 et
+22 points sur 2023-2026, avec un drawdown plus faible que l'indice sur 2019-2023 (-29 % contre
-34 %). L'essentiel de ce gain vient des grandes
valeurs (+0,6 à +0,7 M$ par période, contre +0,2 à +0,4 M$ pour les trades RSI) : la stratégie
parie en partie sur les grandes valeurs américaines, qui ont dominé le marché mondial de 2019 à
2026. Rien ne garantit que cela continue.

L'univers mondial (tous les membres du WLS) a ensuite été comparé à l'univers américain, en RSI
seul puis avec la réserve de 50 %, avec la même version du code :

| Face à VT (≈ WLS) | 2019-2023 | 2023-2026 |
|---|---|---|
| **Univers mondial + 50 % grandes valeurs (défaut)** | **+99 % → +42 pts** (≈ +55 pts une fois retirées les erreurs de données) | **+145 % → +68 pts** |
| Univers US + 50 % grandes valeurs | +93 % → +36 pts | +74 % → -4 pts |
| Univers mondial, RSI seul (`--core-min 0`) | +97 % → +40 pts (≈ +16 pts corrigé) | +9 % → -70 pts |
| Univers US, RSI seul (`--core-min 0`) | +64 % → +6 pts | +52 % → -28 pts |

Avec la réserve de 50 %, l'univers mondial fait mieux que l'univers américain sur les deux
périodes : c'est le réglage par défaut. Dans tous ces runs, les 10 grandes valeurs sont les plus
grandes du WLS entier (TSMC comprise) et rapportent de 0,58 à 0,78 M$ par période. La moitié RSI,
qui ne tient que 2 ou 3 positions à la fois, varie beaucoup d'un run à l'autre : une partie de
l'avance de l'univers mondial vient du hasard.

Les fichiers de trades de l'univers mondial sur 2019-2023 contiennent des gains impossibles (sauts
de cours au-delà des plafonds de variation chinois et taïwanais) et un achat pendant une
suspension de cotation (Mesoblast, août 2023), d'où les valeurs corrigées. Pour rejouer ces runs :

```bash
python -m rsi_strategy backtest --core-rel-momentum 0 --core-candidates 0 --out results/monde_gv50
python -m rsi_strategy backtest --core-rel-momentum 0 --core-candidates 0 --universe us --out results/us_gv50
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --core-rel-momentum 0 --core-candidates 0 --out results/oos_monde_gv50
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --core-rel-momentum 0 --core-candidates 0 --universe us --out results/oos_us_gv50
```

Dans le backtest, les grandes valeurs sont choisies chaque mois d'après leur capitalisation **à
l'époque** (titres de l'indice aujourd'hui × cours de la veille), parmi les 2000 plus gros membres
actuels du WLS. Les choisir d'après leur taille actuelle reviendrait à acheter en 2019 les
gagnantes connues en 2026 ; se limiter aux 100 plus grandes actuelles oublierait les grandes
valeurs d'hier qui ont reculé depuis. Une candidate est ignorée quand sa capitalisation estimée
dépasse 50 fois sa capitalisation actuelle : c'est le signe d'une émission massive d'actions que
Yahoo n'ajuste pas (LATAM, restructurée en 2022, paraissait sinon valoir des milliers de
milliards de dollars en 2019).

### Momentum des grandes valeurs

Pour limiter les chutes alignées sur celles du marché, deux règles de momentum ont été testées sur
les grandes valeurs, sur l'univers mondial, avec la règle fixée avant les tests : garder une
variante seulement si elle fait mieux que les 10 plus grandes sur les deux périodes.

| Face à VT (drawdown max) | 2019-2023 | 2023-2026 |
|---|---|---|
| 10 plus grandes (100 candidates) | +42 pts (-28 %) | +68 pts (-20 %) |
| Momentum absolu (en cash quand VT a baissé sur 12 mois) | -6 pts (-24 %) | +69 pts (-20 %) |
| Momentum relatif parmi les 50 plus grandes (100 candidates) | +63 pts (-27 %) | +115 pts (-19 %) |
| Momentum relatif parmi les 20 plus grandes (100 candidates) | +32 pts (-27 %) | +72 pts (-20 %) |
| 10 plus grandes (2000 candidates) | +40 pts (-30 %) | +69 pts (-20 %) |
| **Momentum relatif parmi les 50 plus grandes (2000 candidates)** | **+60 pts** (-27 %) | **+98 pts** (-19 %) |

- **Momentum absolu, rejeté** : VT n'a jamais baissé sur 12 mois entre 2023 et 2026, et sur
  2019-2023 le signal arrive trop tard, après les baisses, et fait rater des reprises (début 2019,
  avril 2020). Les grandes valeurs restent en cash 35 % du temps et ne rapportent que 0,30 M$,
  contre 0,70 M$.
- **Momentum relatif, retenu** : avec les 100 plus grandes actuelles comme candidates, on pouvait
  craindre un biais du survivant. Avec 2000 candidates, les anciennes géantes qui ont reculé
  reviennent dans la course (Pfizer, par exemple) : l'avance baisse un peu sur 2019-2023 (de +63 à
  +60 pts) et davantage sur 2023-2026 (de +115 à +98 pts), mais reste de 20 à 30 points au-dessus
  des 10 plus grandes sur les deux périodes, avec un drawdown un peu plus faible. Tesla, Nvidia et
  Eli Lilly, les plus gros gains de 2019-2023, sont retenues aux mêmes dates avec 2000 candidates :
  elles comptaient bien parmi les 50 plus grandes de l'époque.
- **Limites** : l'avance dépend de la largeur du choix (parmi les 20 plus grandes seulement, le
  momentum fait moins bien sur 2019-2023) et du nombre de valeurs détenues (avec 20 au lieu de 10,
  elle disparaît), et les gains viennent de quelques valeurs : Tesla, Nvidia et Eli Lilly sur
  2019-2023, Micron, AMD, SK Hynix et Samsung sur 2023-2026. Sur 6 mois au lieu de 12, le momentum
  bat aussi les 10 plus grandes sur les deux périodes : l'effet ne tient pas à ce seul réglage.

Sans plafond, le momentum concentrait les grandes valeurs sur un secteur : à l'été 2026, presque
toutes étaient des valeurs de mémoire et de semi-conducteurs (Micron, SK Hynix, SanDisk, Lam
Research...). Les 8 plus fortes variations d'une séance de la part grandes valeurs, sur 3 ans, ont
toutes eu lieu en juin et juillet 2026, à 3 ou 4 % du portefeuille chacune, alors que VT bougeait
à peine. Deux corrections ont été testées, avec la même règle fixée avant les tests : moins de
volatilité et un Sharpe au moins égal sur les deux périodes, en battant toujours les 10 plus
grandes.

| Face à VT (volatilité, Sharpe) | 2019-2023 | 2023-2026 |
|---|---|---|
| Momentum sans plafond | +60 pts (17,6 %, 0,98) | +98 pts (19,2 %, 1,79) |
| **3 au plus par secteur (défaut)** | **+64 pts** (16,9 %, 1,03) | **+97 pts** (16,6 %, 2,04) |
| Pondération par l'inverse de la volatilité (`--core-inverse-vol`) | +41 pts (16,1 %, 0,94) | +82 pts (17,6 %, 1,83) |

Le plafond par secteur réduit la volatilité pour le même rendement : les pires séances de l'été
2026 passent de -4,2 % à -2,8 % du portefeuille. La pondération par la volatilité coûte trop de
rendement sur 2019-2023 (Sharpe plus faible) : elle reste une option du backtest.

```bash
python -m rsi_strategy backtest --start 2023-10-14 --end 2026-10-14 --out results/monde_secteur3
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --out results/oos_monde_secteur3
python -m rsi_strategy backtest --start 2023-10-14 --end 2026-10-14 --core-sector-max 0 --out results/monde_rel_c2000
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --core-sector-max 0 --out results/oos_monde_rel_c2000
python -m rsi_strategy backtest --start 2023-10-14 --end 2026-10-14 --core-rel-momentum 0 --core-sector-max 0 --out results/monde_c2000
python -m rsi_strategy backtest --start 2019-01-01 --end 2023-10-13 --core-rel-momentum 0 --core-sector-max 0 --out results/oos_monde_c2000
```

## Limites

- **Biais du survivant** : l'univers correspond aux membres *d'aujourd'hui* du WLS. Les sociétés
  disparues (faillites, rachats) ou passées en large cap entre-temps n'y figurent pas, ce qui
  tend à flatter le backtest. Pour les grandes valeurs, les 2000 candidates limitent ce biais, mais
  les géantes rachetées ou retirées de la cote manquent toujours.
- **Données journalières** : l'ordre de passage du stop et de l'objectif dans une même séance est
  inconnu. Le cas est traité de façon prudente (voir plus haut).
- **VT et le WLS** : le WLS ne compte pas les dividendes, alors que VT est comparé dividendes
  réinvestis. VT surestime donc le WLS d'environ son rendement en dividendes (de l'ordre de 2 %
  par an), ce qui rend la comparaison un peu défavorable à la stratégie.
- **Actions autorisées** : le concours n'accepte que les actions membres du WLS. Importez la liste
  (`import-wls`) pour que l'univers s'y limite ; sans elle, vérifiez chaque action sur le terminal.
  La composition change au fil du temps : réimportez une liste récente si besoin.
- **Couverture de Yahoo** : pour l'Inde, Singapour, l'Arabie saoudite, la Malaisie ou le Chili,
  Bloomberg utilise ses propres abréviations (« HDFCB » pour HDFC Bank) que Yahoo ne connaît pas ;
  la plupart de ces actions sont ignorées, ainsi que les places de Dubaï, Abou Dhabi et Bahreïn.
  Le nombre d'actions ignorées s'affiche à chaque téléchargement.
- **Devises et lots** : les cours sont convertis en dollars au taux de clôture du jour, ce qui
  ajoute le risque de change aux positions étrangères. Certaines places imposent des lots
  (100 actions au Japon, en Chine…) : arrondissez si la plateforme les exige.
- **Volume de données** : télécharger 10 000 actions prend nettement plus de temps qu'avant
  (de l'ordre de 15 à 30 minutes par backtest sans cache, et pour chaque scan quotidien) ; le
  cache d'une période pèse environ 250 Mo. Au-delà d'un certain nombre de requêtes, Yahoo bloque
  temporairement (« Too Many Requests ») : le téléchargement ralentit, attend et réessaie
  automatiquement. Si des actions manquent encore, un avertissement l'indique, les résultats
  sont incomplets et rien n'est mis en cache : relancez plus tard.
- **SMMD** n'existe que depuis juillet 2017. Pour un backtest antérieur, utiliser par exemple
  `--benchmarks VT IWM`.

## Tests

```bash
python -m pytest
```

Les tests utilisent des données synthétiques et ne nécessitent pas de connexion réseau.
