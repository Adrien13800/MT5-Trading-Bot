# Vérification finale Backtest vs Prod

## Ce qui est identique (aucune action)

| Élément | Statut |
|--------|--------|
| Constantes (EMA_FAST, SMA_SLOW, R:R, SMA_SLOPE_MIN, ATR, USE_ATR_SL, etc.) | Identiques |
| MIN_BARS_BETWEEN_SAME_SETUP = 0, COOLDOWN_AFTER_LOSS = 0 | Identiques |
| Données M5 : barres fermées uniquement, iloc[-1] / iloc[-2] | Identiques |
| get_h1_data_at_time : cutoff = current_time - 1h (barres H1 fermées uniquement) | Identiques |
| check_h1_trend : 3 dernières H1, LONG haussier / SHORT baissier | Identiques |
| get_trading_session / is_valid_trading_session (UTC, OFF 21h-00h) | Identiques |
| is_ema200_flat (len < 2), get_risk_reward_ratio | Identiques |
| find_last_low / find_last_high (ATR puis swing, lookback 10) | Identiques |
| Prix de référence pour SL/TP/lot = close dernière barre M5 | Identiques |
| calculate_lot_size : formule (risk_amount, tick_value, etc.) | Identiques (source balance) |
| Protection quotidienne : daily_loss sur **balance** | Identiques (prod alignée) |
| Validations après signal : lot <= 0, SL invalide, sl_distance_pct > 5%, one symbol at a time | Identiques |
| Filtre ATR : non appelé avant entrée dans les deux | Identiques |

---

## Distinction relevée (à corriger pour alignement strict)

### 1. Vérification NaN des indicateurs dans check_long_entry / check_short_entry

| | Backtest | Prod |
|-|----------|------|
| **Comportement** | Aucune vérification de `pd.isna()` sur ema20_current, sma50_current, ema20_prev, sma50_prev. | Si l’un est NaN → `return False` (pas de signal). |

**Effet :** En cas de NaN (données manquantes ou artefact), le backtest pourrait accepter un signal (comparaisons avec NaN donnent souvent False, mais le `return True` final peut être atteint). La prod refuserait le signal. Pour que les deux se comportent pareil, le backtest doit ajouter la même vérification NaN que la prod.

**Action :** Ajouter dans le backtest (`check_long_entry` et `check_short_entry`), après la récupération des ema/sma et avant les conditions de croisement :

```python
if pd.isna(ema20_current) or pd.isna(sma50_current) or pd.isna(ema20_prev) or pd.isna(sma50_prev):
    return False
```

---

## Résumé final

- **Une seule distinction** avait été relevée (vérification NaN) ; elle a été **corrigée** dans le backtest.
- La logique de décision (signaux, filtres, SL/TP, lot, protection quotidienne) est **alignée** entre backtest et prod.
- Les différences restantes sont **volontaires** : prod a les contraintes broker (stops_level, arrondi digits) et envoie l’ordre au marché (tick.ask/bid) ; le backtest n’a pas ces étapes.
