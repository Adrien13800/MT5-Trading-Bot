#!/usr/bin/env python3
"""
Backtest identique à run_backtest.py mais limité aux N dernières heures de données.
Permet de vérifier que les trades pris par le bot en prod correspondent à ceux
que le backtest aurait pris (même logique, même moteur).

Usage:
  python run_backtest_last24h.py              # défaut: 24 h
  python run_backtest_last24h.py --hours 12   # 12 dernières heures
  python run_backtest_last24h.py -H 48       # 48 dernières heures
"""

import sys
import os
import argparse
from datetime import datetime, timedelta, timezone

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pass

# S'assurer que le dossier backtest est en premier dans le path (config.py)
_backtest_dir = os.path.dirname(os.path.abspath(__file__))
if _backtest_dir not in sys.path:
    sys.path.insert(0, _backtest_dir)
else:
    sys.path.remove(_backtest_dir)
    sys.path.insert(0, _backtest_dir)

from run_backtest import load_config, run_backtest_engine
from ema_mt5_bot_backtest import (
    MT5BacktestBot, TradeType, USE_H1_TREND_FILTER,
    EMA_FAST, SMA_SLOW, USE_ATR_FILTER, ATR_PERIOD
)

# Marge pour le warm-up des indicateurs (SMA 50 + marge)
WARMUP_HOURS = 5


def _to_naive(ts):
    """Convertit un timestamp timezone-aware en naive si besoin pour comparaison."""
    if hasattr(ts, 'to_pydatetime'):
        ts = ts.to_pydatetime()
    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return ts


def _refresh_m5_tail(bot, validated_symbols):
    """
    Récupère les toutes dernières barres M5 depuis MT5 et les ajoute aux données.
    Utilise le même symbole que le bot (résolution MT5) pour que copy_rates fonctionne.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return
    added_any = False
    for symbol in validated_symbols:
        if symbol not in bot.historical_data or bot.historical_data[symbol] is None:
            continue
        df = bot.historical_data[symbol]
        last_ts = _to_naive(df.index[-1])
        mt5_symbol = bot.find_symbol_variant(symbol)
        if mt5_symbol is None:
            mt5_symbol = symbol
        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 1, 50)
        if rates is None or len(rates) == 0:
            continue
        tail = pd.DataFrame(rates)
        tail['time'] = pd.to_datetime(tail['time'], unit='s')
        tail.set_index('time', inplace=True)
        if hasattr(tail.index, 'tz') and tail.index.tz is not None:
            tail.index = tail.index.tz_localize(None)
        newer = tail[tail.index > last_ts]
        if len(newer) == 0:
            continue
        # Normaliser les index pour éviter tz-naive vs tz-aware (sort_index échoue sinon)
        df_concat = df.copy()
        if hasattr(df_concat.index, 'tz') and df_concat.index.tz is not None:
            df_concat.index = df_concat.index.tz_localize(None)
        newer_idx = newer.index
        if hasattr(newer_idx, 'tz') and newer_idx.tz is not None:
            newer = newer.copy()
            newer.index = newer.index.tz_localize(None)
        common = df_concat.columns.intersection(newer.columns).tolist()
        if not common:
            common = ['open', 'high', 'low', 'close', 'tick_volume']
        combined = pd.concat([df_concat, newer[common]], axis=0)
        combined = combined[~combined.index.duplicated(keep='last')].sort_index()
        combined[f'EMA_{EMA_FAST}'] = combined['close'].ewm(span=EMA_FAST, adjust=False).mean()
        combined[f'SMA_{SMA_SLOW}'] = combined['close'].rolling(window=SMA_SLOW).mean()
        if USE_ATR_FILTER:
            high_low = combined['high'] - combined['low']
            high_close = np.abs(combined['high'] - combined['close'].shift())
            low_close = np.abs(combined['low'] - combined['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            combined['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()
        bot.historical_data[symbol] = combined
        added_any = True
    if added_any:
        print("   Rafraîchissement M5: dernières barres récupérées depuis MT5.")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest sur les N dernières heures (même logique que run_backtest.py)"
    )
    parser.add_argument(
        "--hours", "-H",
        type=int,
        default=24,
        metavar="N",
        help="Nombre d'heures maximum du backtest (défaut: 24)"
    )
    args = parser.parse_args()
    last_hours = args.hours
    if last_hours < 1:
        print("ERREUR: --hours doit être >= 1")
        sys.exit(1)

    print("=" * 70)
    print(f"BACKTEST DERNIÈRES {last_hours} H - Même logique que run_backtest.py")
    print("   Timeframe M5 | LONG et SHORT | Vérification alignement prod")
    print("=" * 70)

    config = load_config()

    bot = MT5BacktestBot(
        login=config['login'],
        password=config['password'],
        server=config['server'],
        symbols=config['symbols'],
        risk_percent=config['risk'],
        max_daily_loss=config['max_daily_loss'],
        initial_balance=config['initial_balance']
    )

    months_back = 1
    print(f"\nChargement des données M5 (dernier mois, puis fenêtre {last_hours} h)...")
    validated_symbols = []
    symbol_stats = {}

    for symbol in config['symbols']:
        df = bot.load_historical_data(
            symbol,
            years=config['years_back'],
            use_all_available=False,
            last_n_months=months_back
        )
        if df is not None and len(df) > 0:
            bot.historical_data[symbol] = df
            validated_symbols.append(symbol)
            symbol_stats[symbol] = {
                'bars_loaded': len(df),
                'period_start': df.index[0],
                'period_end': df.index[-1],
                'period_days': (df.index[-1] - df.index[0]).days if len(df) > 1 else 0,
                'signals_detected': 0,
                'trades_opened': 0,
                'signals_blocked': 0
            }
            print(f"   OK {symbol}: {len(df)} barres M5")
        else:
            print(f"   ERREUR: pas de données pour {symbol}")
            symbol_stats[symbol] = {'bars_loaded': 0, 'error': 'Données non chargées'}

    if not validated_symbols:
        print("Aucun symbole chargé. Arrêt.")
        return

    if USE_H1_TREND_FILTER:
        print("\nChargement des données H1...")
        for symbol in validated_symbols:
            df_h1 = bot.load_h1_data(
                symbol,
                years=config['years_back'],
                use_all_available=False,
                last_n_months=months_back
            )
            if df_h1 is not None:
                bot.h1_data[symbol] = df_h1
                print(f"   OK {symbol}: {len(df_h1)} barres H1")
            else:
                print(f"   ATTENTION: pas de H1 pour {symbol}")

    bot.symbols = validated_symbols

    print("\nRafraîchissement des dernières barres M5...")
    _refresh_m5_tail(bot, validated_symbols)

    end_ts = min(df.index[-1] for df in bot.historical_data.values())
    if hasattr(end_ts, 'tzinfo') and end_ts.tzinfo:
        end_ts = end_ts.replace(tzinfo=None)
    start_window = end_ts - timedelta(hours=last_hours)
    cutoff_warm = end_ts - timedelta(hours=last_hours + WARMUP_HOURS)

    def _index_naive(idx):
        if hasattr(idx, 'tz') and idx.tz is not None:
            return idx.tz_localize(None) if hasattr(idx, 'tz_localize') else idx
        return idx

    for sym in list(bot.historical_data.keys()):
        df = bot.historical_data[sym]
        idx_naive = _index_naive(df.index)
        mask = (idx_naive >= cutoff_warm) & (idx_naive <= end_ts)
        df_slice = df.loc[mask].copy()
        if len(df_slice) < 60:
            print(f"   ATTENTION {sym}: seulement {len(df_slice)} barres après fenêtre (warm-up 60 barres requis)")
        bot.historical_data[sym] = df_slice
        if sym in symbol_stats:
            symbol_stats[sym]['bars_loaded'] = len(df_slice)
            symbol_stats[sym]['period_start'] = df_slice.index[0] if len(df_slice) > 0 else None
            symbol_stats[sym]['period_end'] = df_slice.index[-1] if len(df_slice) > 0 else None

    for sym in list(bot.h1_data.keys()):
        df = bot.h1_data[sym]
        idx_naive = _index_naive(df.index)
        mask = (idx_naive >= cutoff_warm) & (idx_naive <= end_ts)
        bot.h1_data[sym] = df.loc[mask].copy()

    last_bar_str = end_ts.strftime('%Y-%m-%d %H:%M') if hasattr(end_ts, 'strftime') else str(end_ts)
    print(f"\nDernière barre M5 utilisée: {last_bar_str}")
    end_naive = _to_naive(end_ts)
    now = datetime.now()
    if (now - end_naive).total_seconds() > 600:
        print("   ⚠️  Données > 10 min en retard: relancez dans 1–2 min pour voir le trade le plus récent.")
    print(f"Fenêtre backtest: {cutoff_warm} → {end_ts}")
    print(f"Période « dernières {last_hours} h » (pour comparaison prod): {start_window} → {end_ts}")
    config['preferred_symbol_use_local_weekday'] = True
    print("   (Actif du jour calculé en heure locale, comme en prod)")
    print("\nDémarrage du moteur de backtest...\n")

    run_backtest_engine(bot, config, symbol_stats)

    closed_in_window = [t for t in bot.closed_trades if _to_naive(t.entry_time) >= start_window]
    open_in_window = []
    for sym, trades in bot.open_trades.items():
        for t in trades:
            if _to_naive(t.entry_time) >= start_window:
                open_in_window.append((sym, t))

    print("\n" + "=" * 70)
    print(f"TRADES DANS LES DERNIÈRES {last_hours} H (à comparer avec le bot prod)")
    print("=" * 70)
    print(f"Période: {start_window.strftime('%Y-%m-%d %H:%M')} → {end_ts.strftime('%Y-%m-%d %H:%M')}")
    print(f"Trades fermés: {len(closed_in_window)}")
    print(f"Trades encore ouverts (fin de période): {len(open_in_window)}")

    pnl_final = sum(t.profit for t in closed_in_window)
    print(f"P&L final (trades fermés dans la fenêtre): {pnl_final:+.2f} €")

    nb_sl = sum(1 for t in closed_in_window if getattr(t, 'exit_reason', None) == 'SL')
    nb_tp = sum(1 for t in closed_in_window if getattr(t, 'exit_reason', None) == 'TP')
    pertes_totales = sum(t.profit for t in closed_in_window if t.profit < 0)
    gains_totaux = sum(t.profit for t in closed_in_window if t.profit > 0)
    print(f"Sorties SL: {nb_sl}  |  Sorties TP: {nb_tp}")
    print(f"Pertes totales: {pertes_totales:.2f} €  |  Gains totaux: {gains_totaux:.2f} €")

    if closed_in_window:
        print(f"\n--- Trades fermés (dernières {last_hours} h) ---")
        for t in closed_in_window:
            entry_naive = _to_naive(t.entry_time)
            typ = "LONG" if t.type == TradeType.LONG else "SHORT"
            res = f"   {entry_naive.strftime('%Y-%m-%d %H:%M')} | {t.symbol} | {typ} | entrée={t.entry_price} | sortie={t.exit_price} | {t.exit_reason} | P&L={t.profit:.2f}"
            print(res)

    if open_in_window:
        print(f"\n--- Trades encore ouverts (entrée dans les {last_hours} h) ---")
        for sym, t in open_in_window:
            entry_naive = _to_naive(t.entry_time)
            typ = "LONG" if t.type == TradeType.LONG else "SHORT"
            print(f"   {entry_naive.strftime('%Y-%m-%d %H:%M')} | {sym} | {typ} | entrée={t.entry_price} | SL={t.stop_loss} | TP={t.take_profit}")

    if symbol_stats:
        print(f"\n--- Diagnostic (signaux / trades ouverts / bloqués) ---")
        for sym, st in symbol_stats.items():
            if isinstance(st, dict):
                det = st.get('signals_detected', 0)
                opened = st.get('trades_opened', 0)
                blocked = st.get('signals_blocked', 0)
                print(f"   {sym}: signaux={det}, trades ouverts={opened}, signaux bloqués={blocked}")

    print(f"\nFin backtest ({last_hours} h).")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
