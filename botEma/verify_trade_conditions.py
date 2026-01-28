#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de vérification COMPLÈTE de tous les critères d'entrée en trade
Vérifie chaque point qui fait qu'un trade est pris ou refusé
"""

import sys
import time
from datetime import datetime

# Forcer l'encodage UTF-8 pour Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
except ImportError:
    print("❌ Erreur: MetaTrader5, pandas ou numpy n'est pas installé.")
    sys.exit(1)

try:
    from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOLS, RISK_PERCENT
except ImportError:
    print("❌ Erreur: config.py non trouvé")
    sys.exit(1)

# Importer toutes les constantes et fonctions du bot
from ema_mt5_bot import (
    EMA_FAST, SMA_SLOW, RISK_REWARD_RATIO_FLAT, RISK_REWARD_RATIO_TRENDING,
    SMA_SLOPE_MIN, USE_ATR_SL, ATR_SL_MULTIPLIER, ATR_PERIOD,
    USE_H1_TREND_FILTER, TIMEFRAME_MT5, TIMEFRAME_H1,
    ALLOW_LONG, ALLOW_SHORT, MIN_BARS_BETWEEN_SAME_SETUP,
    USE_ATR_FILTER, ATR_MULTIPLIER, ATR_LOOKBACK,
    USE_DISTANCE_FILTER, MAX_DISTANCE_FROM_EMA200,
    USE_EMA_SPREAD_FILTER, MAX_EMA_SPREAD,
    USE_CONFIRMATION_FILTER, CONFIRMATION_BARS,
    USE_VOLATILITY_FILTER, MAX_VOLATILITY_MULTIPLIER,
    MAGIC_NUMBER
)

def connect_mt5():
    """Connexion à MT5"""
    mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not mt5.initialize(path=mt5_path):
        error = mt5.last_error()
        print(f"❌ Erreur initialisation MT5: {error}")
        return False
    
    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        error = mt5.last_error()
        print(f"❌ Erreur connexion MT5: {error}")
        return False
    
    account_info = mt5.account_info()
    if account_info:
        print(f"✅ Connecté au compte: {account_info.login}")
        print(f"   Balance: {account_info.balance:.2f} {account_info.currency}")
    
    return True

def load_m5_data(symbol, count=100):
    """Charge les données M5 avec indicateurs"""
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, count)
    if rates is None or len(rates) == 0:
        return None
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    
    # Calculer EMA 20 et SMA 50
    df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df[f'SMA_{SMA_SLOW}'] = df['close'].rolling(window=SMA_SLOW).mean()
    
    # Calculer ATR
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()
    
    return df

def load_h1_data(symbol, count=100):
    """Charge les données H1 pour le filtre de tendance"""
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, count)
    if rates is None or len(rates) == 0:
        return None
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    
    return df

def get_trading_session(trade_time: datetime):
    """Détermine la session de trading basée sur l'heure UTC"""
    from ema_mt5_bot import TradingSession
    hour = trade_time.hour
    
    if 0 <= hour < 8:
        return TradingSession.ASIA
    elif 8 <= hour < 14:
        return TradingSession.EUROPE
    elif 14 <= hour < 21:
        return TradingSession.US
    else:  # 21-24
        return TradingSession.OFF_HOURS

def is_valid_trading_session(trade_time: datetime):
    """Vérifie si on est dans une session de trading valide"""
    session = get_trading_session(trade_time)
    from ema_mt5_bot import TradingSession
    return session != TradingSession.OFF_HOURS

def check_h1_trend(symbol, trade_type):
    """Vérifie la tendance H1 (même logique que le bot)"""
    if not USE_H1_TREND_FILTER:
        return True, "Filtre H1 désactivé"
    
    df_h1 = load_h1_data(symbol, 10)
    if df_h1 is None or len(df_h1) < 3:
        return False, "Données H1 insuffisantes"
    
    # Prendre les 3 dernières bougies H1
    prices = df_h1['close'].iloc[-3:].tolist()
    
    if trade_type == "LONG":
        # Pour LONG: tendance haussière
        price_first = prices[0]
        price_last = prices[-1]
        
        if price_last <= price_first:
            return False, f"Tendance H1 non haussière (première: {price_first:.2f}, dernière: {price_last:.2f})"
        
        # Compter les hausses
        rises = 0
        for i in range(1, len(prices)):
            if prices[i] > prices[i-1]:
                rises += 1
        
        if rises < 2:
            return False, f"Pas assez de hausses H1 (seulement {rises} sur 3 bougies)"
        
        return True, f"Tendance H1 haussière ✅ ({rises} hausses sur 3 bougies)"
    
    else:  # SHORT
        # Pour SHORT: tendance baissière
        price_first = prices[0]
        price_last = prices[-1]
        
        if price_last >= price_first:
            return False, f"Tendance H1 non baissière (première: {price_first:.2f}, dernière: {price_last:.2f})"
        
        # Compter les baisses
        falls = 0
        for i in range(1, len(prices)):
            if prices[i] < prices[i-1]:
                falls += 1
        
        if falls < 2:
            return False, f"Pas assez de baisses H1 (seulement {falls} sur 3 bougies)"
        
        return True, f"Tendance H1 baissière ✅ ({falls} baisses sur 3 bougies)"

def check_cross_signal(df, trade_type):
    """Vérifie le croisement EMA20/SMA50"""
    if len(df) < 2:
        return False, "Données insuffisantes"
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    ema20_current = current[f'EMA_{EMA_FAST}']
    sma50_current = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']
    
    if trade_type == "LONG":
        # EMA 20 doit croiser au-dessus de SMA 50
        if ema20_prev >= sma50_prev:
            return False, f"Pas de croisement haussier (EMA20_prev={ema20_prev:.2f} >= SMA50_prev={sma50_prev:.2f})"
        
        if ema20_current <= sma50_current:
            return False, f"EMA20 pas encore au-dessus (EMA20={ema20_current:.2f} <= SMA50={sma50_current:.2f})"
        
        return True, f"Croisement haussier détecté ✅ (EMA20: {ema20_prev:.2f}→{ema20_current:.2f}, SMA50: {sma50_prev:.2f}→{sma50_current:.2f})"
    
    else:  # SHORT
        # EMA 20 doit croiser en-dessous de SMA 50
        if ema20_prev <= sma50_prev:
            return False, f"Pas de croisement baissier (EMA20_prev={ema20_prev:.2f} <= SMA50_prev={sma50_prev:.2f})"
        
        if ema20_current >= sma50_current:
            return False, f"EMA20 pas encore en-dessous (EMA20={ema20_current:.2f} >= SMA50={sma50_current:.2f})"
        
        return True, f"Croisement baissier détecté ✅ (EMA20: {ema20_prev:.2f}→{ema20_current:.2f}, SMA50: {sma50_prev:.2f}→{sma50_current:.2f})"

def check_atr_filter(df):
    """Vérifie le filtre ATR (volatilité)"""
    if not USE_ATR_FILTER:
        return True, "Filtre ATR désactivé"
    
    if 'ATR' not in df.columns:
        return True, "ATR non calculé (filtre ignoré)"
    
    if len(df) < ATR_LOOKBACK + 1:
        return True, "Données insuffisantes pour ATR (filtre ignoré)"
    
    current_atr = df['ATR'].iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return True, "ATR invalide (filtre ignoré)"
    
    # Moyenne ATR sur les dernières périodes
    atr_values = df['ATR'].iloc[-(ATR_LOOKBACK + 1):-1]
    atr_avg = atr_values.mean()
    
    if atr_avg <= 0:
        return True, "Moyenne ATR invalide (filtre ignoré)"
    
    # Vérifier que volatilité actuelle >= moyenne * multiplicateur
    min_volatility = atr_avg * ATR_MULTIPLIER
    
    if current_atr < min_volatility:
        return False, f"Volatilité insuffisante (ATR={current_atr:.2f} < {min_volatility:.2f})"
    
    return True, f"Volatilité OK ✅ (ATR={current_atr:.2f} >= {min_volatility:.2f})"

def check_existing_positions(symbol, trade_type):
    """Vérifie les positions existantes et protection sur-trading"""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return True, "Aucune position existante"
    
    our_positions = [pos for pos in positions if pos.magic == MAGIC_NUMBER]
    
    if not our_positions:
        return True, "Aucune position existante avec notre magic number"
    
    # Compter les positions du même type
    same_type_positions = [pos for pos in our_positions if 
                          (trade_type == "LONG" and pos.type == mt5.ORDER_TYPE_BUY) or
                          (trade_type == "SHORT" and pos.type == mt5.ORDER_TYPE_SELL)]
    
    if same_type_positions:
        return True, f"{len(same_type_positions)} position(s) {trade_type} déjà ouverte(s) (plusieurs positions autorisées)"
    
    return True, f"{len(our_positions)} position(s) ouverte(s) (type différent)"

def verify_trade_conditions(symbol):
    """Vérifie TOUS les critères d'entrée pour un symbole"""
    print(f"\n{'='*70}")
    print(f"🔍 VÉRIFICATION COMPLÈTE DES CONDITIONS DE TRADE")
    print(f"   Symbole: {symbol}")
    print(f"{'='*70}")
    
    # 1. Charger les données M5
    print(f"\n📊 1. CHARGEMENT DES DONNÉES M5")
    df = load_m5_data(symbol, 100)
    if df is None or len(df) < SMA_SLOW + 10:
        print(f"   ❌ Données M5 insuffisantes ({len(df) if df is not None else 0} bougies)")
        return
    
    print(f"   ✅ {len(df)} bougies M5 chargées")
    current = df.iloc[-1]
    current_time = df.index[-1]
    if hasattr(current_time, 'to_pydatetime'):
        current_time = current_time.to_pydatetime()
    print(f"   Prix actuel: {current['close']:.2f}")
    print(f"   EMA{EMA_FAST}: {current[f'EMA_{EMA_FAST}']:.2f}")
    print(f"   SMA{SMA_SLOW}: {current[f'SMA_{SMA_SLOW}']:.2f}")
    print(f"   Heure UTC: {current_time.strftime('%H:%M:%S')}")
    
    # 1.5. Vérifier la session de trading
    print(f"\n📊 1.5. SESSION DE TRADING")
    session = get_trading_session(current_time)
    session_emoji = "🌍" if session.value == "ASIA" else "🇪🇺" if session.value == "EUROPE" else "🇺🇸" if session.value == "US" else "🌙"
    session_valid = is_valid_trading_session(current_time)
    print(f"   Session: {session_emoji} {session.value}")
    if not session_valid:
        print(f"   ❌ Trading bloqué: Session OFF_HOURS (21:00-00:00 UTC)")
        print(f"   ⚠️  Le bot ne peut PAS prendre de trades en session OFF_HOURS")
        return
    else:
        print(f"   ✅ Session valide pour le trading")
    
    # 2. Vérifier LONG
    print(f"\n{'='*70}")
    print(f"🟢 VÉRIFICATION LONG")
    print(f"{'='*70}")
    
    if not ALLOW_LONG:
        print(f"   ❌ LONG désactivé dans la configuration")
    else:
        # 2.1 Croisement EMA20/SMA50
        print(f"\n   2.1 Croisement EMA{EMA_FAST}/SMA{SMA_SLOW}:")
        cross_ok, cross_msg = check_cross_signal(df, "LONG")
        print(f"      {'✅' if cross_ok else '❌'} {cross_msg}")
        
        # 2.2 Filtre H1
        print(f"\n   2.2 Filtre tendance H1:")
        h1_ok, h1_msg = check_h1_trend(symbol, "LONG")
        print(f"      {'✅' if h1_ok else '❌'} {h1_msg}")
        
        # 2.3 Filtre ATR
        print(f"\n   2.3 Filtre ATR (volatilité):")
        atr_ok, atr_msg = check_atr_filter(df)
        print(f"      {'✅' if atr_ok else '❌'} {atr_msg}")
        
        # 2.4 Positions existantes
        print(f"\n   2.4 Positions existantes:")
        pos_ok, pos_msg = check_existing_positions(symbol, "LONG")
        print(f"      ✅ {pos_msg}")
        
        # Résumé LONG
        long_all_ok = cross_ok and h1_ok and atr_ok
        print(f"\n   📊 RÉSUMÉ LONG:")
        print(f"      {'✅ SIGNAL LONG VALIDE' if long_all_ok else '❌ Signal LONG invalide'}")
        if not long_all_ok:
            print(f"      Raisons:")
            if not cross_ok:
                print(f"        - Croisement: {cross_msg}")
            if not h1_ok:
                print(f"        - H1: {h1_msg}")
            if not atr_ok:
                print(f"        - ATR: {atr_msg}")
    
    # 3. Vérifier SHORT
    print(f"\n{'='*70}")
    print(f"🔴 VÉRIFICATION SHORT")
    print(f"{'='*70}")
    
    if not ALLOW_SHORT:
        print(f"   ❌ SHORT désactivé dans la configuration")
    else:
        # 3.1 Croisement EMA20/SMA50
        print(f"\n   3.1 Croisement EMA{EMA_FAST}/SMA{SMA_SLOW}:")
        cross_ok, cross_msg = check_cross_signal(df, "SHORT")
        print(f"      {'✅' if cross_ok else '❌'} {cross_msg}")
        
        # 3.2 Filtre H1
        print(f"\n   3.2 Filtre tendance H1:")
        h1_ok, h1_msg = check_h1_trend(symbol, "SHORT")
        print(f"      {'✅' if h1_ok else '❌'} {h1_msg}")
        
        # 3.3 Filtre ATR
        print(f"\n   3.3 Filtre ATR (volatilité):")
        atr_ok, atr_msg = check_atr_filter(df)
        print(f"      {'✅' if atr_ok else '❌'} {atr_msg}")
        
        # 3.4 Positions existantes
        print(f"\n   3.4 Positions existantes:")
        pos_ok, pos_msg = check_existing_positions(symbol, "SHORT")
        print(f"      ✅ {pos_msg}")
        
        # Résumé SHORT
        short_all_ok = cross_ok and h1_ok and atr_ok
        print(f"\n   📊 RÉSUMÉ SHORT:")
        print(f"      {'✅ SIGNAL SHORT VALIDE' if short_all_ok else '❌ Signal SHORT invalide'}")
        if not short_all_ok:
            print(f"      Raisons:")
            if not cross_ok:
                print(f"        - Croisement: {cross_msg}")
            if not h1_ok:
                print(f"        - H1: {h1_msg}")
            if not atr_ok:
                print(f"        - ATR: {atr_msg}")
    
    # 4. Configuration générale
    print(f"\n{'='*70}")
    print(f"⚙️  CONFIGURATION GÉNÉRALE")
    print(f"{'='*70}")
    print(f"   EMA Fast: {EMA_FAST}")
    print(f"   SMA Slow: {SMA_SLOW}")
    print(f"   Risque par trade: {RISK_PERCENT}%")
    print(f"   R:R plate (SMA50 plate): 1:{RISK_REWARD_RATIO_FLAT}")
    print(f"   R:R trending (SMA50 penche): 1:{RISK_REWARD_RATIO_TRENDING}")
    print(f"   Filtre H1: {'✅ Activé' if USE_H1_TREND_FILTER else '❌ Désactivé'}")
    print(f"   Filtre ATR: {'✅ Activé' if USE_ATR_FILTER else '❌ Désactivé'}")
    print(f"   SL basé sur ATR: {'✅ Oui' if USE_ATR_SL else '❌ Non'}")
    print(f"   Multiplicateur ATR SL: {ATR_SL_MULTIPLIER}")
    print(f"   Protection sur-trading: {MIN_BARS_BETWEEN_SAME_SETUP} bougies ({(MIN_BARS_BETWEEN_SAME_SETUP * 5)} min)")
    print(f"   LONG autorisé: {'✅ Oui' if ALLOW_LONG else '❌ Non'}")
    print(f"   SHORT autorisé: {'✅ Oui' if ALLOW_SHORT else '❌ Non'}")

def main():
    """Fonction principale"""
    print("=" * 70)
    print("🔍 VÉRIFICATION COMPLÈTE DES CONDITIONS DE TRADE")
    print("=" * 70)
    print("Ce script vérifie TOUS les critères qui font qu'un trade est pris")
    print("=" * 70)
    
    # Connexion
    if not connect_mt5():
        print("❌ Échec de la connexion")
        return
    
    # Vérifier chaque symbole
    for symbol in SYMBOLS:
        verify_trade_conditions(symbol)
        print(f"\n{'='*70}\n")
    
    print("✅ Vérification terminée")
    print("\n💡 Pour lancer le bot:")
    print("   py run_bot.py")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
