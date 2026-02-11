# Distinctions restantes Backtest vs Prod (dernière revue)

Après alignement des filtres (ATR, volatility, confirmation, find_last, check_distance, check_ema_spread), il reste **uniquement** les distinctions suivantes.

---

## 1. Limite de perte quotidienne

| | Backtest | Prod |
|---|----------|------|
| **Application** | Optionnelle via `USE_DAILY_LOSS_IN_BACKTEST` (défaut **False**) | **Toujours** : `can_trade_today()` applique la limite -250/jour |
| **Effet** | Le backtest peut tourner sur toute la période sans stop quotidien | En prod, si perte du jour ≤ -250 €, plus d’ouverture de trade jusqu’au lendemain |

→ **Volontaire** : en backtest tu désactives pour avoir la courbe sur toute la période ; en prod la protection reste active.

---

## 2. Exécution et contraintes broker

| | Backtest | Prod |
|---|----------|------|
| **Prix d’exécution** | Simulé au **close** de la barre M5 | Ordre au **marché** (tick.ask / tick.bid) |
| **SL/TP** | Utilisés tels quels | Arrondi au **digits** du symbole, respect du **stops_level** (distance min), ajustement SL/TP si trop proches de l’entrée |
| **Volume** | Même formule (balance, risk_per_lot), arrondi step | Idem + contraintes réelles du broker (volume_min/max/step) |
| **Balance** | `bot.current_balance` (simulée) | `account_info.balance` (réelle) |

→ **Normale** : le backtest ne peut pas simuler le marché réel ni les règles du broker.

---

## 3. Signatures / contexte d’appel (pas de différence de logique)

| | Backtest | Prod |
|---|----------|------|
| **get_daily_loss** | `get_daily_loss(self, current_date: datetime.date)` – date passée par la boucle de rejeu | `get_daily_loss(self)` – utilise `datetime.now().date()` en interne |
| **can_trade_today** | `can_trade_today(self, current_date: datetime.date)` – idem, date de la barre rejouée | `can_trade_today(self)` – pas d’argument, “aujourd’hui” = maintenant |

→ Même logique (balance, daily_start_balance, reset nouveau jour) ; seule la façon d’obtenir la date change (rejeu vs temps réel).

---

## 4. Données et boucle

| | Backtest | Prod |
|---|----------|------|
| **Données M5** | Historique chargé (ex. 8 mois), rejeu barre à barre | Données **live** MT5 |
| **Données H1** | Chargées une fois pour toute la période | Cache + rechargement si dernière H1 > 2h avant `current_time` |
| **Boucle** | `run_backtest.py` : événements triés par temps, `get_market_data_at_index(symbol, bar_index)` | Boucle temps réel (intervalle UPDATE_INTERVAL), `get_market_data(symbol, count)` |

→ **Normale** : rejeu historique vs exécution en direct.

---

## 5. Défense NaN en prod (très mineur)

| | Backtest | Prod |
|---|----------|------|
| **check_distance_from_ema200 / check_ema_spread** | `if sma50 <= 0: return True` | `if sma50 <= 0 or pd.isna(sma50): return True` |

→ En prod, si `sma50` est NaN, le filtre est considéré comme “passant” (pas de rejet supplémentaire). En backtest, pas de test NaN à cet endroit. Impact négligeable et plus sûr en prod.

---

## Résumé

- **Logique de décision (signaux, filtres, R:R, SL/TP, lot, perte quotidienne sur balance)** : **alignée** entre backtest et prod.
- **Distinctions restantes** :
  1. **Limite -250/jour** : désactivable en backtest, toujours active en prod.
  2. **Exécution** : backtest au close sans contraintes broker ; prod au marché avec stops_level, digits, volume.
  3. **Contexte** : backtest = rejeu historique ; prod = live + rechargement H1.
  4. **Signatures** : get_daily_loss / can_trade_today prennent la date en backtest, pas en prod (même logique).
  5. **NaN** : prod a un garde-fou `pd.isna(sma50)` dans 2 filtres, backtest non (optionnel à garder en prod).

Aucune autre distinction de logique métier entre les deux procédés.
