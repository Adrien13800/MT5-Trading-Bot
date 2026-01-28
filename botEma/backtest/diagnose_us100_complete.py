#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic complet pour comprendre pourquoi US100.cash ne génère pas de trades
Teste TOUTES les raisons possibles
"""

import sys
import os
from datetime import datetime

# Fix encoding pour Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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
    USE_ATR_SL, ATR_SL_MULTIPLIER, ATR_MULTIPLIER, ATR_PERIOD
)

def diagnose_us100():
    """Diagnostic complet pour US100.cash"""
    
    print("\n" + "="*70)
    print("🔍 DIAGNOSTIC COMPLET - POURQUOI US100.CASH NE GÉNÈRE PAS DE TRADES?")
    print("="*70)
    
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
    
    symbol = "US100.cash"
    
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
    print(f"\n📊 1. CHARGEMENT DES DONNÉES M5 POUR {symbol}")
    print("-" * 70)
    df_m5 = bot.load_historical_data(symbol, years=3, use_all_available=True)
    
    if df_m5 is None or len(df_m5) < SMA_SLOW + 10:
        print(f"❌ PROBLÈME: Pas assez de données pour {symbol}")
        print(f"   Données chargées: {len(df_m5) if df_m5 is not None else 0} bougies")
        mt5.shutdown()
        return
    
    bot.historical_data[symbol] = df_m5
    print(f"✅ {len(df_m5)} bougies M5 chargées")
    print(f"   Période: {df_m5.index[0]} → {df_m5.index[-1]}")
    
    # Charger les données H1
    print(f"\n📊 2. CHARGEMENT DES DONNÉES H1 POUR {symbol}")
    print("-" * 70)
    df_h1 = bot.load_h1_data(symbol)
    if df_h1 is not None:
        bot.h1_data[symbol] = df_h1
        print(f"✅ {len(df_h1)} bougies H1 chargées")
    else:
        print(f"⚠️  Aucune donnée H1 chargée (peut bloquer les trades si USE_H1_TREND_FILTER=True)")
    
    # Vérifier les informations du symbole
    print(f"\n📊 3. INFORMATIONS DU SYMBOLE")
    print("-" * 70)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info:
        print(f"✅ Symbole trouvé: {symbol}")
        print(f"   Point: {symbol_info.point}")
        print(f"   Tick size: {symbol_info.trade_tick_size}")
        print(f"   Tick value: {symbol_info.trade_tick_value}")
        print(f"   Contract size: {symbol_info.trade_contract_size}")
        print(f"   Digits: {symbol_info.digits}")
    else:
        print(f"❌ PROBLÈME: Symbole {symbol} non trouvé dans MT5")
        mt5.shutdown()
        return
    
    # Analyser les signaux sur toutes les bougies
    print(f"\n📊 4. ANALYSE DES SIGNAUX SUR {len(df_m5) - SMA_SLOW} BOUGIES")
    print("-" * 70)
    
    stats = {
        'total_bars': 0,
        'signals_long': 0,
        'signals_short': 0,
        'blocked_h1_trend_long': 0,
        'blocked_h1_trend_short': 0,
        'blocked_no_cross_long': 0,
        'blocked_no_cross_short': 0,
        'blocked_ema_slope': 0,
        'blocked_atr': 0,
        'blocked_invalid_sl_long': 0,
        'blocked_invalid_sl_short': 0,
        'blocked_sl_too_far_long': 0,
        'blocked_sl_too_far_short': 0,
        'blocked_invalid_lot_long': 0,
        'blocked_invalid_lot_short': 0,
        'valid_trades_long': 0,
        'valid_trades_short': 0
    }
    
    # Parcourir toutes les bougies (sauf les 50 premières)
    sample_size = min(1000, len(df_m5) - SMA_SLOW)  # Analyser les 1000 premières bougies pour diagnostic rapide
    print(f"   Analyse de {sample_size} bougies (pour diagnostic rapide)...")
    
    for bar_index in range(SMA_SLOW, SMA_SLOW + sample_size):
        stats['total_bars'] += 1
        
        # Récupérer les données jusqu'à cet index
        market_data = bot.get_market_data_at_index(symbol, bar_index)
        if market_data is None:
            continue
        
        current_bar = df_m5.iloc[bar_index]
        current_bar_time = df_m5.index[bar_index]
        
        # TEST LONG
        if ALLOW_LONG:
            # Vérifier le signal LONG
            long_signal = bot.check_long_entry(market_data, symbol, current_bar_time)
            
            if long_signal:
                stats['signals_long'] += 1
                
                # Calculer le stop-loss
                entry_price = current_bar['close']
                stop_loss = bot.find_last_low(symbol, market_data, 10)
                sl_distance_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0
                
                # Calculer le lot size
                lot_size = bot.calculate_lot_size(symbol, entry_price, stop_loss)
                
                # Vérifier les raisons de blocage
                if lot_size <= 0:
                    stats['blocked_invalid_lot_long'] += 1
                elif stop_loss <= 0 or stop_loss >= entry_price:
                    stats['blocked_invalid_sl_long'] += 1
                elif sl_distance_pct > 0.05:
                    stats['blocked_sl_too_far_long'] += 1
                else:
                    stats['valid_trades_long'] += 1
            else:
                # Vérifier pourquoi le signal n'est pas détecté
                # Vérifier H1 trend
                if USE_H1_TREND_FILTER:
                    if not bot.check_h1_trend(symbol, current_bar_time, TradeType.LONG):
                        stats['blocked_h1_trend_long'] += 1
                
                # Vérifier le croisement
                if len(market_data) >= 2:
                    current = market_data.iloc[-1]
                    prev = market_data.iloc[-2]
                    ema20_current = current.get(f'EMA_{EMA_FAST}', None)
                    sma50_current = current.get(f'SMA_{SMA_SLOW}', None)
                    ema20_prev = prev.get(f'EMA_{EMA_FAST}', None)
                    sma50_prev = prev.get(f'SMA_{SMA_SLOW}', None)
                    
                    if ema20_current is not None and sma50_current is not None:
                        if ema20_prev >= sma50_prev or ema20_current <= sma50_current:
                            stats['blocked_no_cross_long'] += 1
        
        # TEST SHORT
        if ALLOW_SHORT:
            # Vérifier le signal SHORT
            short_signal = bot.check_short_entry(market_data, symbol, current_bar_time)
            
            if short_signal:
                stats['signals_short'] += 1
                
                # Calculer le stop-loss
                entry_price = current_bar['close']
                stop_loss = bot.find_last_high(symbol, market_data, 10)
                sl_distance_pct = abs(stop_loss - entry_price) / entry_price if entry_price > 0 else 0
                
                # Calculer le lot size
                lot_size = bot.calculate_lot_size(symbol, entry_price, stop_loss)
                
                # Vérifier les raisons de blocage
                if lot_size <= 0:
                    stats['blocked_invalid_lot_short'] += 1
                elif stop_loss <= 0 or stop_loss <= entry_price:
                    stats['blocked_invalid_sl_short'] += 1
                elif sl_distance_pct > 0.05:
                    stats['blocked_sl_too_far_short'] += 1
                else:
                    stats['valid_trades_short'] += 1
            else:
                # Vérifier pourquoi le signal n'est pas détecté
                # Vérifier H1 trend
                if USE_H1_TREND_FILTER:
                    if not bot.check_h1_trend(symbol, current_bar_time, TradeType.SHORT):
                        stats['blocked_h1_trend_short'] += 1
                
                # Vérifier le croisement
                if len(market_data) >= 2:
                    current = market_data.iloc[-1]
                    prev = market_data.iloc[-2]
                    ema20_current = current.get(f'EMA_{EMA_FAST}', None)
                    sma50_current = current.get(f'SMA_{SMA_SLOW}', None)
                    ema20_prev = prev.get(f'EMA_{EMA_FAST}', None)
                    sma50_prev = prev.get(f'SMA_{SMA_SLOW}', None)
                    
                    if ema20_current is not None and sma50_current is not None:
                        if ema20_prev <= sma50_prev or ema20_current >= sma50_current:
                            stats['blocked_no_cross_short'] += 1
    
    # Afficher les résultats
    print(f"\n📊 5. RÉSULTATS DU DIAGNOSTIC")
    print("=" * 70)
    print(f"\n📈 LONG:")
    print(f"   Signaux détectés: {stats['signals_long']}")
    print(f"   Trades valides: {stats['valid_trades_long']}")
    print(f"   Bloqués par H1 trend: {stats['blocked_h1_trend_long']}")
    print(f"   Bloqués par pas de croisement: {stats['blocked_no_cross_long']}")
    print(f"   Bloqués par SL invalide: {stats['blocked_invalid_sl_long']}")
    print(f"   Bloqués par SL trop éloigné (>5%): {stats['blocked_sl_too_far_long']}")
    print(f"   Bloqués par lot size invalide: {stats['blocked_invalid_lot_long']}")
    
    print(f"\n📉 SHORT:")
    print(f"   Signaux détectés: {stats['signals_short']}")
    print(f"   Trades valides: {stats['valid_trades_short']}")
    print(f"   Bloqués par H1 trend: {stats['blocked_h1_trend_short']}")
    print(f"   Bloqués par pas de croisement: {stats['blocked_no_cross_short']}")
    print(f"   Bloqués par SL invalide: {stats['blocked_invalid_sl_short']}")
    print(f"   Bloqués par SL trop éloigné (>5%): {stats['blocked_sl_too_far_short']}")
    print(f"   Bloqués par lot size invalide: {stats['blocked_invalid_lot_short']}")
    
    # Diagnostic final
    print(f"\n🔍 6. DIAGNOSTIC FINAL")
    print("=" * 70)
    
    total_signals = stats['signals_long'] + stats['signals_short']
    total_valid = stats['valid_trades_long'] + stats['valid_trades_short']
    
    if total_signals == 0:
        print("❌ PROBLÈME PRINCIPAL: Aucun signal détecté!")
        print("\n   Raisons possibles:")
        if stats['blocked_h1_trend_long'] + stats['blocked_h1_trend_short'] > 0:
            print(f"   - Filtre H1 trend bloque tous les signaux ({stats['blocked_h1_trend_long'] + stats['blocked_h1_trend_short']} fois)")
        if stats['blocked_no_cross_long'] + stats['blocked_no_cross_short'] > 0:
            print(f"   - Pas de croisement EMA20/SMA50 détecté ({stats['blocked_no_cross_long'] + stats['blocked_no_cross_short']} fois)")
        print("   - Les données peuvent être insuffisantes ou les indicateurs mal calculés")
    elif total_signals > 0 and total_valid == 0:
        print("⚠️  PROBLÈME: Des signaux sont détectés mais tous sont bloqués!")
        print("\n   Raisons de blocage:")
        if stats['blocked_invalid_sl_long'] + stats['blocked_invalid_sl_short'] > 0:
            print(f"   - Stop-loss invalide: {stats['blocked_invalid_sl_long'] + stats['blocked_invalid_sl_short']} fois")
        if stats['blocked_sl_too_far_long'] + stats['blocked_sl_too_far_short'] > 0:
            print(f"   - Stop-loss trop éloigné (>5%): {stats['blocked_sl_too_far_long'] + stats['blocked_sl_too_far_short']} fois")
        if stats['blocked_invalid_lot_long'] + stats['blocked_invalid_lot_short'] > 0:
            print(f"   - Lot size invalide: {stats['blocked_invalid_lot_long'] + stats['blocked_invalid_lot_short']} fois")
    elif total_valid > 0:
        print(f"✅ {total_valid} trades valides détectés sur {sample_size} bougies analysées")
        print(f"   Si le backtest complet ne génère pas de trades, vérifiez:")
        print(f"   - Les filtres supplémentaires dans run_backtest.py")
        print(f"   - Les cooldowns et restrictions de positions simultanées")
    
    # Test spécifique du calcul du stop-loss
    print(f"\n📊 7. TEST DU CALCUL STOP-LOSS (10 dernières bougies)")
    print("-" * 70)
    
    for i in range(max(SMA_SLOW, len(df_m5) - 10), len(df_m5)):
        market_data = bot.get_market_data_at_index(symbol, i)
        if market_data is None:
            continue
        
        current = market_data.iloc[-1]
        entry_price = current['close']
        
        # Test LONG
        stop_loss_long = bot.find_last_low(symbol, market_data, 10)
        sl_distance_pct_long = abs(entry_price - stop_loss_long) / entry_price if entry_price > 0 else 0
        lot_size_long = bot.calculate_lot_size(symbol, entry_price, stop_loss_long)
        
        # Test SHORT
        stop_loss_short = bot.find_last_high(symbol, market_data, 10)
        sl_distance_pct_short = abs(stop_loss_short - entry_price) / entry_price if entry_price > 0 else 0
        lot_size_short = bot.calculate_lot_size(symbol, entry_price, stop_loss_short)
        
        print(f"\n   Bougie {i} (Prix: {entry_price:.2f}):")
        print(f"      LONG:  SL={stop_loss_long:.2f}, Distance={sl_distance_pct_long*100:.2f}%, Lot={lot_size_long:.2f}")
        if stop_loss_long <= 0 or stop_loss_long >= entry_price:
            print(f"            ❌ SL invalide")
        elif sl_distance_pct_long > 0.05:
            print(f"            ⚠️  SL trop éloigné (>5%)")
        elif lot_size_long <= 0:
            print(f"            ❌ Lot size invalide")
        else:
            print(f"            ✅ Valide")
        
        print(f"      SHORT: SL={stop_loss_short:.2f}, Distance={sl_distance_pct_short*100:.2f}%, Lot={lot_size_short:.2f}")
        if stop_loss_short <= 0 or stop_loss_short <= entry_price:
            print(f"            ❌ SL invalide")
        elif sl_distance_pct_short > 0.05:
            print(f"            ⚠️  SL trop éloigné (>5%)")
        elif lot_size_short <= 0:
            print(f"            ❌ Lot size invalide")
        else:
            print(f"            ✅ Valide")
    
    mt5.shutdown()
    
    print(f"\n" + "="*70)
    print("✅ DIAGNOSTIC TERMINÉ")
    print("="*70)

if __name__ == "__main__":
    diagnose_us100()
