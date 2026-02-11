# Analyse complète Backtest vs Prod – Distinctions relevées

Analyse ligne par ligne des fichiers :
- **Backtest** : `botEma/backtest/ema_mt5_bot_backtest.py` + `run_backtest.py`
- **Prod** : `botEma/ema_mt5_bot.py`

---

## 1. IDENTIQUE (aucune distinction)

| Élément | Détail |
|--------|--------|
| **Constantes** | EMA_FAST=20, SMA_SLOW=50, RISK_REWARD_RATIO_FLAT=1.0, TRENDING=1.5, SMA_SLOPE_MIN, ATR_*, USE_H1_TREND_FILTER=True, MIN_BARS_BETWEEN_SAME_SETUP=0, COOLDOWN_AFTER_LOSS=0, tous les USE_*_FILTER (False sauf USE_ATR_FILTER, USE_TREND_FILTER) |
| **check_long_entry / check_short_entry** | Même ordre : session → H1 → croisement EMA20/SMA50 (iloc[-1]/[-2]), pas de test NaN |
| **get_trading_session / is_valid_trading_session** | ASIA 0–8h, EUROPE 8–14h, US 14–21h UTC, OFF_HOURS 21–0h → False |
| **check_h1_trend** | 3 dernières H1, LONG : price_last ≥ price_first et rises ≥ 2 ; SHORT : price_last ≤ price_first et falls ≥ 2 |
| **get_h1_data_at_time (filtre temps)** | Les deux utilisent données H1 jusqu’à `current_time` inclus (prod : ts = Timestamp(current_time) + tz si besoin, puis `index <= ts`) |
| **is_ema200_flat** | len(df) < 2 → True ; sinon slope SMA50 vs SMA_SLOPE_MIN |
| **get_risk_reward_ratio** | is_ema200_flat → FLAT sinon TRENDING |
| **calculate_lot_size** | risk_amount = balance × risk_percent/100 ; risk_per_lot (tick_value/tick_size ou contract_size) ; lot = risk_amount/risk_per_lot ; min/max/step |
| **get_daily_loss** | Sur **balance** (backtest : current_balance ; prod : account_info.balance), daily_start_balance, reset si nouveau jour |
| **Prix d’entrée pour SL/TP/lot** | close dernière barre M5 (backtest : current_bar['close'], prod : df['close'].iloc[-1]) |
| **Validations après signal** | lot ≤ 0, SL invalide (LONG : SL≥entry ; SHORT : SL≤entry), sl_distance_pct > 5 %, un seul symbole en position → rejet |
| **find_last_low / find_last_high** | Priorité ATR (ATR_SL_MULTIPLIER), fallback swing (lookback 10), buffer point×5 ou 0.999/1.001 |

---

## 2. DISTINCTIONS (différences de logique ou de contexte)

### 2.1 Limite de perte quotidienne

| | Backtest | Prod |
|---|----------|------|
| **Application** | Optionnelle : `USE_DAILY_LOSS_IN_BACKTEST` (défaut False) → pas d’arrêt par -250/jour | Toujours : `can_trade_today()` applique la limite -250/jour |
| **Effet** | Backtest peut tourner sur toute la période sans stop quotidien | Prod arrête les nouvelles entrées pour la journée si perte du jour ≤ -250 |

---

### 2.2 Exécution et contraintes broker

| | Backtest | Prod |
|---|----------|------|
| **Prix d’exécution** | Simulé au **close** de la barre M5 | Ordre au **marché** (tick.ask / tick.bid) |
| **SL/TP** | Utilisés tels quels | Arrondi `digits`, respect **stops_level** (min_distance), ajustement SL/TP si trop proches |
| **Volume** | `calculate_lot_size` → arrondi step, borné min/max | Idem + contraintes broker réelles (volume_min/max/step) |
| **Balance** | `bot.current_balance` (simulée) | `account_info.balance` (réelle) |

---

### 2.3 check_atr_filter (si appelé – actuellement non utilisé dans la boucle d’entrée)

| | Backtest | Prod |
|---|----------|------|
| **Condition sur len(df)** | `len(df) < ATR_LOOKBACK + 1` (21) → return False | `len(df) < 2 + ATR_LOOKBACK` (22) → return False |
| **Effet** | Avec 21 barres, backtest peut passer le filtre ATR ; prod exige 22 barres | Un bar de moins en prod pour déclencher le filtre (plus restrictif d’1 barre) |

---

### 2.4 check_volatility_filter (USE_VOLATILITY_FILTER = False – inactif)

| | Backtest | Prod |
|---|----------|------|
| **Condition sur len(df)** | `len(df) < ATR_LOOKBACK + 1` (21) | `len(df) < 2 + ATR_LOOKBACK` (22) |
| **Effet** | Même type de décalage d’une barre que pour check_atr_filter si le filtre était activé |

---

### 2.5 check_confirmation_filter (USE_CONFIRMATION_FILTER = False – inactif)

| | Backtest | Prod |
|---|----------|------|
| **LONG** | `recent_closes.iloc[-1] > recent_closes.iloc[0]` (dernière > première = haussier) | `recent_closes.iloc[0] > recent_closes.iloc[-1]` (première > dernière = baissier) |
| **SHORT** | `recent_closes.iloc[-1] < recent_closes.iloc[0]` | `recent_closes.iloc[0] < recent_closes.iloc[-1]` → même sens |
| **Effet** | Si un jour USE_CONFIRMATION_FILTER=True, **prod LONG serait inversé** par rapport au backtest (bug). Actuellement sans impact. |

---

### 2.6 find_last_low / find_last_high – fallback swing (quand ATR non utilisé)

| | Backtest | Prod |
|---|----------|------|
| **Ajustement du lookback** | `if len(df) < lookback: lookback = len(df)` | `if len(df) < 1 + lookback: lookback = max(1, len(df) - 1)` |
| **Exemple len(df)=10** | lookback=10 → 10 barres utilisées | lookback=9 → 9 barres utilisées |
| **Effet** | En cas de peu de barres, backtest utilise légèrement plus de barres pour le swing que la prod (différence mineure). |

---

### 2.7 get_h1_data_at_time – contexte d’appel

| | Backtest | Prod |
|---|----------|------|
| **Source H1** | `self.h1_data[symbol]` (chargé une fois pour toute la période) | Cache `self.h1_data` + rechargement si dernière H1 > 2h avant current_time |
| **Timezone** | Comparaison directe `df_h1.index <= current_time` | `pd.Timestamp(current_time)` + `tz_localize` si index H1 est timezone-aware |
| **Effet** | Comportement équivalent si pas de timezone sur les index ; prod gère en plus le rafraîchissement et le tz. |

---

### 2.8 Données et boucle

| | Backtest | Prod |
|---|----------|------|
| **Données M5** | Historique chargé (ex. 8 mois), rejeu barre à barre | Données live MT5 (copy_rates ou équivalent) |
| **Boucle** | `run_backtest.py` : événements triés par temps, `get_market_data_at_index(symbol, bar_index)` | Boucle temps réel (intervalle UPDATE_INTERVAL), `get_market_data(symbol, count)` |

---

## 3. RÉSUMÉ

- **Stratégie (signaux, filtres actifs, R:R, SL/TP, lot, perte quotidienne sur balance)** : alignée entre backtest et prod.
- **Distinctions à garder en tête :**
  1. **Limite -250/jour** : appliquée en prod, désactivable en backtest.
  2. **Exécution** : backtest au close, prod au marché + contraintes broker (stops_level, digits).
  3. **Filtres inactifs** : si un jour on active ATR/volatility/confirmation, une barre de décalage (ATR/volatility) et la logique LONG de confirmation en prod devront être alignées sur le backtest.
  4. **Swing lookback** : très légère différence sur le nombre de barres en fallback (souvent 1 barre), impact négligeable en pratique.
