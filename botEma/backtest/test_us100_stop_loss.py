#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test spécifique pour comprendre pourquoi US100 ne génère pas de trades
Teste le calcul du stop-loss pour US100
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
    USE_ATR_SL, ATR_SL_MULTIPLIER
)

def test_stop_loss_calculation(symbol: str):
    """Teste le calcul du stop-loss pour un symbole"""
    print(f"\n{'='*70}")
    print(f"🔍 TEST STOP-LOSS POUR {symbol}")
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
    
    if df_m5 is None or len(df_m5) < SMA_SLOW + 10:
        print(f"❌ Pas assez de données pour {symbol}")
        mt5.shutdown()
        return
    
    bot.historical_data[symbol] = df_m5
    
    # Tester le calcul du stop-loss sur plusieurs bougies
    print(f"\n🔍 Test du calcul du stop-loss sur 20 bougies récentes...")
    
    invalid_count = 0
    valid_count = 0
    too_far_count = 0
    
    for i in range(SMA_SLOW, min(SMA_SLOW + 20, len(df_m5))):
        market_data = bot.get_market_data_at_index(symbol, i)
        if market_data is None:
            continue
        
        current = market_data.iloc[-1]
        entry_price = current['close']
        
        # Test LONG
        stop_loss_long = bot.find_last_low(symbol, market_data, 10)
        sl_distance_pct_long = abs(entry_price - stop_loss_long) / entry_price if entry_price > 0 else 0
        
        # Test SHORT
        stop_loss_short = bot.find_last_high(symbol, market_data, 10)
        sl_distance_pct_short = abs(stop_loss_short - entry_price) / entry_price if entry_price > 0 else 0
        
        # Vérifier LONG
        if stop_loss_long <= 0 or stop_loss_long >= entry_price:
            invalid_count += 1
            print(f"   Bougie {i}: LONG - Stop-loss invalide (SL={stop_loss_long:.2f}, Entry={entry_price:.2f})")
        elif sl_distance_pct_long > 0.05:
            too_far_count += 1
            print(f"   Bougie {i}: LONG - Stop-loss trop éloigné ({sl_distance_pct_long*100:.2f}% > 5%)")
        else:
            valid_count += 1
        
        # Vérifier SHORT
        if stop_loss_short <= 0 or stop_loss_short <= entry_price:
            invalid_count += 1
            print(f"   Bougie {i}: SHORT - Stop-loss invalide (SL={stop_loss_short:.2f}, Entry={entry_price:.2f})")
        elif sl_distance_pct_short > 0.05:
            too_far_count += 1
            print(f"   Bougie {i}: SHORT - Stop-loss trop éloigné ({sl_distance_pct_short*100:.2f}% > 5%)")
        else:
            valid_count += 1
    
    print(f"\n📊 RÉSULTATS:")
    print(f"   Stop-loss valides: {valid_count}")
    print(f"   Stop-loss invalides: {invalid_count}")
    print(f"   Stop-loss trop éloignés (>5%): {too_far_count}")
    
    if too_far_count > 0:
        print(f"\n⚠️  PROBLÈME DÉTECTÉ: Le stop-loss calculé avec ATR est souvent trop éloigné (>5%)")
        print(f"   Solution: Réduire ATR_SL_MULTIPLIER ou utiliser une méthode alternative")
    
    # Tester le calcul du lot size
    print(f"\n🔍 Test du calcul du lot size...")
    test_entry = df_m5.iloc[-1]['close']
    test_sl_long = bot.find_last_low(symbol, market_data, 10)
    test_sl_short = bot.find_last_high(symbol, market_data, 10)
    
    lot_size_long = bot.calculate_lot_size(symbol, test_entry, test_sl_long)
    lot_size_short = bot.calculate_lot_size(symbol, test_entry, test_sl_short)
    
    print(f"   Entry: {test_entry:.2f}")
    print(f"   SL LONG: {test_sl_long:.2f} → Lot size: {lot_size_long}")
    print(f"   SL SHORT: {test_sl_short:.2f} → Lot size: {lot_size_short}")
    
    if lot_size_long <= 0:
        print(f"   ⚠️  Lot size LONG invalide!")
    if lot_size_short <= 0:
        print(f"   ⚠️  Lot size SHORT invalide!")
    
    mt5.shutdown()

def main():
    """Point d'entrée principal"""
    print("\n" + "="*70)
    print("🔧 TEST STOP-LOSS - POURQUOI US100 NE GÉNÈRE AUCUN TRADE?")
    print("="*70)
    
    # Tester US100
    test_stop_loss_calculation("US100.cash")
    
    # Comparer avec US30
    print("\n" + "="*70)
    print("📊 COMPARAISON AVEC US30.cash")
    print("="*70)
    test_stop_loss_calculation("US30.cash")

if __name__ == "__main__":
    main()
