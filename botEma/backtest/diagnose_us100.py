#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de diagnostic pour comprendre pourquoi US100 ne génère aucun trade dans le backtest
"""

import sys
import os
from datetime import datetime

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
except ImportError:
    print("❌ Erreur: MetaTrader5, pandas ou numpy n'est pas installé.")
    sys.exit(1)

from ema_mt5_bot_backtest import (
    MT5BacktestBot, TradeType, EMA_FAST, SMA_SLOW, USE_ATR_FILTER,
    USE_H1_TREND_FILTER, TIMEFRAME_MT5, TIMEFRAME_H1, ALLOW_LONG, ALLOW_SHORT,
    MIN_BARS_BETWEEN_SAME_SETUP, ATR_MULTIPLIER, ATR_LOOKBACK
)

def diagnose_symbol(symbol: str):
    """Diagnostique pourquoi un symbole ne génère pas de trades"""
    print(f"\n{'='*70}")
    print(f"🔍 DIAGNOSTIC POUR {symbol}")
    print(f"{'='*70}\n")
    
    # Initialiser MT5
    mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not mt5.initialize(path=mt5_path):
        print(f"❌ Erreur initialisation MT5: {mt5.last_error()}")
        return
    
    # Se connecter
    import config
    if not mt5.login(login=config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER):
        print(f"❌ Échec connexion MT5: {mt5.last_error()}")
        mt5.shutdown()
        return
    
    # Créer un bot de backtest
    bot = MT5BacktestBot(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
        symbols=[symbol],
        risk_percent=0.5,
        max_daily_loss=-250.0,
        initial_balance=10000.0
    )
    
    # Charger les données M5
    print(f"📊 Chargement des données M5 pour {symbol}...")
    df_m5 = bot.load_historical_data(symbol, years=3, use_all_available=True)
    
    if df_m5 is None or len(df_m5) == 0:
        print(f"❌ Aucune donnée M5 chargée pour {symbol}")
        mt5.shutdown()
        return
    
    print(f"✅ {len(df_m5)} bougies M5 chargées")
    print(f"   Période: {df_m5.index[0]} → {df_m5.index[-1]}")
    
    # Charger les données H1
    if USE_H1_TREND_FILTER:
        print(f"\n📊 Chargement des données H1 pour {symbol}...")
        df_h1 = bot.load_h1_data(symbol, years=3, use_all_available=True)
        if df_h1 is None or len(df_h1) == 0:
            print(f"⚠️  Aucune donnée H1 chargée pour {symbol}")
            print(f"   Le filtre H1 ne pourra pas fonctionner correctement")
        else:
            print(f"✅ {len(df_h1)} bougies H1 chargées")
            bot.h1_data[symbol] = df_h1
    
    bot.historical_data[symbol] = df_m5
    
    # Analyser les signaux
    print(f"\n🔍 Analyse des signaux...")
    
    stats = {
        'total_bars': 0,
        'valid_bars': 0,
        'ema_slope_blocked': 0,
        'atr_blocked': 0,
        'no_cross': 0,
        'h1_trend_blocked_long': 0,
        'h1_trend_blocked_short': 0,
        'long_signals': 0,
        'short_signals': 0,
        'already_open': 0,
        'recent_trade': 0,
        'cooldown': 0,
        'stop_loss_invalid': 0,
        'lot_size_invalid': 0
    }
    
    # Parcourir les bougies
    for bar_index in range(SMA_SLOW, len(df_m5)):
        stats['total_bars'] += 1
        
        market_data = bot.get_market_data_at_index(symbol, bar_index)
        if market_data is None or len(market_data) < SMA_SLOW + 10:
            continue
        
        stats['valid_bars'] += 1
        current = market_data.iloc[-1]
        prev = market_data.iloc[-2] if len(market_data) > 1 else current
        current_time = market_data.index[-1]
        
        # Vérifier EMA slope
        price_close = current['close']
        ema20 = current[f'EMA_{EMA_FAST}']
        if price_close == ema20:
            stats['ema_slope_blocked'] += 1
            continue
        
        # Vérifier ATR
        if USE_ATR_FILTER and 'ATR' in market_data.columns:
            current_atr = current['ATR']
            if not pd.isna(current_atr) and current_atr > 0:
                atr_values = market_data['ATR'].iloc[-ATR_LOOKBACK:-1]
                if len(atr_values) > 0:
                    atr_avg = atr_values.mean()
                    if current_atr < (atr_avg * ATR_MULTIPLIER):
                        stats['atr_blocked'] += 1
                        continue
        
        # Vérifier croisements
        ema20_current = current[f'EMA_{EMA_FAST}']
        sma50_current = current[f'SMA_{SMA_SLOW}']
        ema20_prev = prev[f'EMA_{EMA_FAST}']
        sma50_prev = prev[f'SMA_{SMA_SLOW}']
        
        long_cross = (ema20_prev < sma50_prev) and (ema20_current > sma50_current)
        short_cross = (ema20_prev > sma50_prev) and (ema20_current < sma50_current)
        
        if not long_cross and not short_cross:
            stats['no_cross'] += 1
            continue
        
        # Vérifier LONG
        if long_cross and ALLOW_LONG:
            # Vérifier H1 trend
            h1_ok = True
            if USE_H1_TREND_FILTER:
                if not bot.check_h1_trend(symbol, current_time, TradeType.LONG):
                    stats['h1_trend_blocked_long'] += 1
                    h1_ok = False
            
            if h1_ok:
                # Vérifier setup récent
                has_recent, _ = bot.has_recent_same_setup(symbol, TradeType.LONG, current_time)
                if has_recent:
                    stats['recent_trade'] += 1
                    continue
                
                # Vérifier position ouverte
                if bot.has_open_position(symbol):
                    stats['already_open'] += 1
                    continue
                
                # Vérifier cooldown
                if bot.is_in_cooldown(current_time):
                    stats['cooldown'] += 1
                    continue
                
                # Vérifier stop-loss et lot size
                entry_price = current['close']
                stop_loss = bot.find_last_low(symbol, market_data, 10)
                
                if stop_loss <= 0 or stop_loss >= entry_price:
                    stats['stop_loss_invalid'] += 1
                    continue
                
                lot_size = bot.calculate_lot_size(symbol, entry_price, stop_loss)
                if lot_size <= 0:
                    stats['lot_size_invalid'] += 1
                    continue
                
                stats['long_signals'] += 1
        
        # Vérifier SHORT
        if short_cross and ALLOW_SHORT:
            # Vérifier H1 trend
            h1_ok = True
            if USE_H1_TREND_FILTER:
                if not bot.check_h1_trend(symbol, current_time, TradeType.SHORT):
                    stats['h1_trend_blocked_short'] += 1
                    h1_ok = False
            
            if h1_ok:
                # Vérifier setup récent
                has_recent, _ = bot.has_recent_same_setup(symbol, TradeType.SHORT, current_time)
                if has_recent:
                    stats['recent_trade'] += 1
                    continue
                
                # Vérifier cooldown
                if bot.is_in_cooldown(current_time):
                    stats['cooldown'] += 1
                    continue
                
                # Vérifier stop-loss et lot size
                entry_price = current['close']
                stop_loss = bot.find_last_high(symbol, market_data, 10)
                
                if stop_loss <= 0 or stop_loss <= entry_price:
                    stats['stop_loss_invalid'] += 1
                    continue
                
                lot_size = bot.calculate_lot_size(symbol, entry_price, stop_loss)
                if lot_size <= 0:
                    stats['lot_size_invalid'] += 1
                    continue
                
                stats['short_signals'] += 1
    
    # Afficher les résultats
    print(f"\n📈 STATISTIQUES:")
    print(f"   Bougies totales: {stats['total_bars']}")
    print(f"   Bougies valides: {stats['valid_bars']}")
    
    print(f"\n🚫 SIGNaux BLOQUÉS:")
    print(f"   - Pas de croisement: {stats['no_cross']}")
    print(f"   - EMA Slope: {stats['ema_slope_blocked']}")
    print(f"   - ATR: {stats['atr_blocked']}")
    print(f"   - H1 trend (LONG): {stats['h1_trend_blocked_long']}")
    print(f"   - H1 trend (SHORT): {stats['h1_trend_blocked_short']}")
    print(f"   - Setup récent: {stats['recent_trade']}")
    print(f"   - Position ouverte: {stats['already_open']}")
    print(f"   - Cooldown: {stats['cooldown']}")
    print(f"   - Stop-loss invalide: {stats['stop_loss_invalid']}")
    print(f"   - Lot size invalide: {stats['lot_size_invalid']}")
    
    print(f"\n✅ SIGNaux VALIDES:")
    print(f"   LONG: {stats['long_signals']}")
    print(f"   SHORT: {stats['short_signals']}")
    print(f"   TOTAL: {stats['long_signals'] + stats['short_signals']}")
    
    if stats['long_signals'] + stats['short_signals'] == 0:
        print(f"\n❌ AUCUN SIGNAL VALIDE DÉTECTÉ")
        print(f"\n💡 RAISONS PROBABLES:")
        if stats['no_cross'] > stats['valid_bars'] * 0.8:
            print(f"   1. Très peu de croisements EMA20/SMA50 ({stats['no_cross']}/{stats['valid_bars']})")
        if stats['h1_trend_blocked_long'] + stats['h1_trend_blocked_short'] > 0:
            print(f"   2. Filtre H1 bloque {stats['h1_trend_blocked_long'] + stats['h1_trend_blocked_short']} signaux")
        if stats['atr_blocked'] > stats['valid_bars'] * 0.5:
            print(f"   3. Volatilité souvent insuffisante ({stats['atr_blocked']}/{stats['valid_bars']})")
        if stats['stop_loss_invalid'] > 0:
            print(f"   4. Stop-loss invalide sur {stats['stop_loss_invalid']} tentatives")
        if stats['lot_size_invalid'] > 0:
            print(f"   5. Lot size invalide sur {stats['lot_size_invalid']} tentatives")
    else:
        print(f"\n✅ {stats['long_signals'] + stats['short_signals']} SIGNAL(S) VALIDE(S) DÉTECTÉ(S)")
        print(f"   Mais aucun trade n'a été pris - vérifiez les logs du backtest")
    
    mt5.shutdown()
    print(f"\n{'='*70}\n")

def main():
    """Point d'entrée principal"""
    print("\n" + "="*70)
    print("🔧 DIAGNOSTIC BACKTEST - POURQUOI US100 NE GÉNÈRE AUCUN TRADE?")
    print("="*70)
    
    # Diagnostiquer US100
    diagnose_symbol("US100.cash")
    
    # Comparer avec US30
    print("\n" + "="*70)
    print("📊 COMPARAISON AVEC US30.cash")
    print("="*70)
    diagnose_symbol("US30.cash")

if __name__ == "__main__":
    main()
