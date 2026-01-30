#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de diagnostic complet des conditions de trading
Vérifie point par point toutes les conditions jusqu'à l'envoi de l'ordre MT5
"""

import sys
import io
from datetime import datetime

# Forcer l'encodage UTF-8 pour Windows
if sys.platform == 'win32':
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
    from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOLS
except ImportError:
    print("❌ Erreur: config.py non trouvé")
    sys.exit(1)

# Importer les constantes et classes du bot
from ema_mt5_bot import (
    EMA_FAST, SMA_SLOW, RISK_REWARD_RATIO_FLAT, RISK_REWARD_RATIO_TRENDING,
    USE_ATR_FILTER, USE_H1_TREND_FILTER, USE_ATR_SL, ATR_SL_MULTIPLIER,
    ALLOW_LONG, ALLOW_SHORT, MAGIC_NUMBER, TRADE_COMMENT,
    MT5TradingBot, TradingSession, TradeType
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

def diagnose_symbol(symbol: str):
    """Diagnostic complet pour un symbole"""
    print("\n" + "=" * 70)
    print(f"🔍 DIAGNOSTIC COMPLET POUR {symbol}")
    print("=" * 70)
    
    # Créer une instance du bot pour utiliser ses méthodes
    bot = MT5TradingBot(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
        symbols=[symbol],
        risk_percent=0.5,
        max_daily_loss=-250.0
    )
    
    # ========== ÉTAPE 1: PROTECTION QUOTIDIENNE ==========
    print("\n📋 ÉTAPE 1: PROTECTION QUOTIDIENNE")
    print("-" * 70)
    can_trade, reason = bot.can_trade_today()
    if not can_trade:
        print(f"   ❌ BLOQUÉ: {reason}")
        print("   ⚠️  Le bot ne peut PAS prendre de trades (protection quotidienne)")
        return
    else:
        daily_loss = bot.get_daily_loss()
        account_info = mt5.account_info()
        currency = account_info.currency if account_info else "USD"
        print(f"   ✅ OK: Perte quotidienne: {daily_loss:.2f} {currency}")
        print(f"   ✅ Marge restante: {abs(bot.max_daily_loss) - abs(daily_loss):.2f} {currency}")
    
    # ========== ÉTAPE 2: DONNÉES DE MARCHÉ ==========
    print("\n📋 ÉTAPE 2: DONNÉES DE MARCHÉ")
    print("-" * 70)
    df = bot.get_market_data(symbol)
    if df is None or len(df) < SMA_SLOW + 10:
        print(f"   ❌ BLOQUÉ: Données insuffisantes ({len(df) if df is not None else 0} bougies)")
        print("   ⚠️  Le bot ne peut PAS prendre de trades (données insuffisantes)")
        return
    else:
        print(f"   ✅ OK: {len(df)} bougies M5 chargées")
        # Après sort_index (get_market_data): iloc[-2]=dernière barre fermée
        current = df.iloc[-2]
        print(f"   ✅ Prix actuel: {current['close']:.2f}")
        print(f"   ✅ EMA{EMA_FAST}: {current[f'EMA_{EMA_FAST}']:.2f}")
        print(f"   ✅ SMA{SMA_SLOW}: {current[f'SMA_{SMA_SLOW}']:.2f}")
    
    # ========== ÉTAPE 3: NOUVELLE BOUGIE ==========
    print("\n📋 ÉTAPE 3: NOUVELLE BOUGIE")
    print("-" * 70)
    # Après sort_index: index[-2]=dernière barre fermée
    current_time = df.index[-2].to_pydatetime()
    if symbol in bot.last_bar_time and current_time <= bot.last_bar_time[symbol]:
        print(f"   ❌ BLOQUÉ: Pas de nouvelle bougie")
        print(f"   ⚠️  Dernière bougie traitée: {bot.last_bar_time[symbol].strftime('%H:%M:%S')}")
        print("   ⚠️  Le bot ne peut PAS prendre de trades (pas de nouvelle bougie)")
        return
    else:
        print(f"   ✅ OK: Nouvelle bougie détectée: {current_time.strftime('%H:%M:%S')} UTC")
    
    # ========== ÉTAPE 4: SESSION DE TRADING ==========
    print("\n📋 ÉTAPE 4: SESSION DE TRADING")
    print("-" * 70)
    session = bot.get_trading_session(current_time)
    is_valid_session = bot.is_valid_trading_session(current_time)
    session_emoji = "🌍" if session == TradingSession.ASIA else "🇪🇺" if session == TradingSession.EUROPE else "🇺🇸" if session == TradingSession.US else "🌙"
    print(f"   Session actuelle: {session_emoji} {session.value}")
    print(f"   Heure UTC: {current_time.strftime('%H:%M:%S')}")
    if not is_valid_session:
        print(f"   ❌ BLOQUÉ: Session OFF_HOURS (21:00-00:00 UTC)")
        print("   ⚠️  Le bot ne peut PAS prendre de trades (session OFF_HOURS)")
        return
    else:
        print(f"   ✅ OK: Session valide pour le trading")
    
    # ========== ÉTAPE 5: FILTRE H1 (TENDANCE) ==========
    print("\n📋 ÉTAPE 5: FILTRE H1 (TENDANCE)")
    print("-" * 70)
    if USE_H1_TREND_FILTER:
        print(f"   Filtre H1: ✅ ACTIVÉ")
        # Vérifier pour LONG
        h1_long_ok = bot.check_h1_trend(symbol, current_time, TradeType.LONG)
        print(f"   LONG - Tendance H1: {'✅ OK' if h1_long_ok else '❌ BLOQUÉ'}")
        if not h1_long_ok:
            df_h1 = bot.get_h1_data_at_time(symbol, current_time)
            if df_h1 is not None and len(df_h1) >= 3:
                last_3 = df_h1.iloc[-3:]
                prices = last_3['close'].values
                print(f"      Raison: Tendance H1 non haussière ({prices[0]:.2f} -> {prices[-1]:.2f})")
        
        # Vérifier pour SHORT
        h1_short_ok = bot.check_h1_trend(symbol, current_time, TradeType.SHORT)
        print(f"   SHORT - Tendance H1: {'✅ OK' if h1_short_ok else '❌ BLOQUÉ'}")
        if not h1_short_ok:
            df_h1 = bot.get_h1_data_at_time(symbol, current_time)
            if df_h1 is not None and len(df_h1) >= 3:
                last_3 = df_h1.iloc[-3:]
                prices = last_3['close'].values
                print(f"      Raison: Tendance H1 non baissière ({prices[0]:.2f} -> {prices[-1]:.2f})")
    else:
        print(f"   Filtre H1: ❌ DÉSACTIVÉ (toujours OK)")
        h1_long_ok = True
        h1_short_ok = True
    
    # ========== ÉTAPE 6: CROISEMENT EMA20/SMA50 ==========
    print("\n📋 ÉTAPE 6: CROISEMENT EMA20/SMA50")
    print("-" * 70)
    # Après sort_index: iloc[-2]=dernière barre fermée, iloc[-3]=avant-dernière
    current = df.iloc[-2]
    prev = df.iloc[-3]
    ema20_curr = current[f'EMA_{EMA_FAST}']
    sma50_curr = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']
    
    # LONG: EMA20 doit croiser au-dessus de SMA50
    cross_long = (ema20_prev < sma50_prev) and (ema20_curr > sma50_curr)
    print(f"   LONG - Croisement: {'✅ DÉTECTÉ' if cross_long else '❌ PAS DE CROISEMENT'}")
    if not cross_long:
        print(f"      EMA20_prev: {ema20_prev:.2f} {'<' if ema20_prev < sma50_prev else '>='} SMA50_prev: {sma50_prev:.2f}")
        print(f"      EMA20_curr: {ema20_curr:.2f} {'>' if ema20_curr > sma50_curr else '<='} SMA50_curr: {sma50_curr:.2f}")
    
    # SHORT: EMA20 doit croiser en-dessous de SMA50
    cross_short = (ema20_prev > sma50_prev) and (ema20_curr < sma50_curr)
    print(f"   SHORT - Croisement: {'✅ DÉTECTÉ' if cross_short else '❌ PAS DE CROISEMENT'}")
    if not cross_short:
        print(f"      EMA20_prev: {ema20_prev:.2f} {'>' if ema20_prev > sma50_prev else '<='} SMA50_prev: {sma50_prev:.2f}")
        print(f"      EMA20_curr: {ema20_curr:.2f} {'<' if ema20_curr < sma50_curr else '>='} SMA50_curr: {sma50_curr:.2f}")
    
    # ========== ÉTAPE 7: SIGNAL FINAL ==========
    print("\n📋 ÉTAPE 7: SIGNAL FINAL (TOUTES CONDITIONS)")
    print("-" * 70)
    long_signal = bot.check_long_entry(df, symbol)
    short_signal = bot.check_short_entry(df, symbol)
    
    print(f"   LONG signal: {'✅ VALIDE' if long_signal else '❌ INVALIDE'}")
    print(f"   SHORT signal: {'✅ VALIDE' if short_signal else '❌ INVALIDE'}")
    
    if not long_signal and not short_signal:
        print("\n   ⚠️  AUCUN SIGNAL VALIDE - Le bot ne peut PAS prendre de trades")
        print("   Raisons possibles:")
        if not is_valid_session:
            print("      - Session OFF_HOURS")
        if not cross_long and not cross_short:
            print("      - Pas de croisement EMA20/SMA50")
        if USE_H1_TREND_FILTER:
            if not h1_long_ok and not h1_short_ok:
                print("      - Tendance H1 non alignée")
        return
    
    # ========== ÉTAPE 8: CALCUL STOP-LOSS ==========
    print("\n📋 ÉTAPE 8: CALCUL STOP-LOSS")
    print("-" * 70)
    trade_type = TradeType.LONG if long_signal else TradeType.SHORT
    entry_price = current['close']
    
    if trade_type == TradeType.LONG:
        stop_loss = bot.find_last_low(symbol, df, 10)
        print(f"   Type: LONG")
        print(f"   Entry: {entry_price:.2f}")
        print(f"   SL calculé: {stop_loss:.2f}")
        print(f"   Distance SL: {entry_price - stop_loss:.2f} ({((entry_price - stop_loss) / entry_price * 100):.2f}%)")
        
        if stop_loss <= 0 or stop_loss >= entry_price:
            print(f"   ❌ BLOQUÉ: Stop-loss invalide")
            return
        sl_distance_pct = abs(entry_price - stop_loss) / entry_price
        if sl_distance_pct > 0.05:
            print(f"   ❌ BLOQUÉ: Stop-loss trop éloigné ({sl_distance_pct*100:.2f}% > 5%)")
            return
        print(f"   ✅ OK: Stop-loss valide")
    else:
        stop_loss = bot.find_last_high(symbol, df, 10)
        print(f"   Type: SHORT")
        print(f"   Entry: {entry_price:.2f}")
        print(f"   SL calculé: {stop_loss:.2f}")
        print(f"   Distance SL: {stop_loss - entry_price:.2f} ({((stop_loss - entry_price) / entry_price * 100):.2f}%)")
        
        if stop_loss <= 0 or stop_loss <= entry_price:
            print(f"   ❌ BLOQUÉ: Stop-loss invalide")
            return
        sl_distance_pct = abs(stop_loss - entry_price) / entry_price
        if sl_distance_pct > 0.05:
            print(f"   ❌ BLOQUÉ: Stop-loss trop éloigné ({sl_distance_pct*100:.2f}% > 5%)")
            return
        print(f"   ✅ OK: Stop-loss valide")
    
    # ========== ÉTAPE 9: CALCUL LOT SIZE ==========
    print("\n📋 ÉTAPE 9: CALCUL LOT SIZE")
    print("-" * 70)
    lot_size = bot.calculate_lot_size(symbol, entry_price, stop_loss)
    print(f"   Lot size calculé: {lot_size}")
    
    if lot_size <= 0:
        print(f"   ❌ BLOQUÉ: Lot size invalide")
        return
    print(f"   ✅ OK: Lot size valide")
    
    # ========== ÉTAPE 10: CALCUL TAKE-PROFIT ==========
    print("\n📋 ÉTAPE 10: CALCUL TAKE-PROFIT")
    print("-" * 70)
    rr_ratio = bot.get_risk_reward_ratio(df)
    is_flat = bot.is_ema200_flat(df)
    
    if trade_type == TradeType.LONG:
        stop_distance = entry_price - stop_loss
        take_profit = entry_price + (stop_distance * rr_ratio)
    else:
        stop_distance = stop_loss - entry_price
        take_profit = entry_price - (stop_distance * rr_ratio)
    
    print(f"   R:R utilisé: 1:{rr_ratio:.1f} ({'SMA50 plate' if is_flat else 'SMA50 penche'})")
    print(f"   TP calculé: {take_profit:.2f}")
    print(f"   ✅ OK: Take-profit valide")
    
    # ========== ÉTAPE 11: VÉRIFICATIONS FINALES ==========
    print("\n📋 ÉTAPE 11: VÉRIFICATIONS FINALES")
    print("-" * 70)
    
    # Vérifier les positions existantes
    positions = mt5.positions_get(symbol=symbol)
    our_positions = [pos for pos in positions if pos.magic == MAGIC_NUMBER] if positions else []
    print(f"   Positions ouvertes: {len(our_positions)}")
    if our_positions:
        print(f"   ✅ OK: Plusieurs positions autorisées")
    
    # Vérifier la connexion
    if not bot.check_connection():
        print(f"   ❌ BLOQUÉ: Connexion MT5 perdue")
        return
    print(f"   ✅ OK: Connexion MT5 active")
    
    # Vérifier les prix du marché
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"   ❌ BLOQUÉ: Impossible de récupérer le tick")
        return
    print(f"   ✅ OK: Tick disponible (BID: {tick.bid:.2f}, ASK: {tick.ask:.2f})")
    
    # ========== ÉTAPE 12: PRÊT POUR ENVOI ==========
    print("\n📋 ÉTAPE 12: PRÊT POUR ENVOI VERS MT5")
    print("-" * 70)
    print(f"   ✅ TOUTES LES CONDITIONS SONT REMPLIES!")
    print(f"   📊 Résumé:")
    print(f"      Type: {trade_type.value}")
    print(f"      Entry: {entry_price:.2f}")
    print(f"      SL: {stop_loss:.2f}")
    print(f"      TP: {take_profit:.2f}")
    print(f"      Lot: {lot_size}")
    print(f"      R:R: 1:{rr_ratio:.1f}")
    print(f"\n   ⚠️  NOTE: Ce script ne passe PAS l'ordre réellement")
    print(f"   ⚠️  Pour passer l'ordre réel, le bot doit détecter un signal valide")

def main():
    """Fonction principale"""
    print("=" * 70)
    print("🔍 DIAGNOSTIC COMPLET DES CONDITIONS DE TRADING")
    print("=" * 70)
    print("Ce script vérifie point par point toutes les conditions")
    print("nécessaires pour qu'un trade soit pris jusqu'à l'envoi vers MT5")
    print("=" * 70)
    
    if not connect_mt5():
        print("❌ Échec de la connexion")
        return
    
    for symbol in SYMBOLS:
        diagnose_symbol(symbol)
        print("\n" + "=" * 70 + "\n")
    
    print("✅ Diagnostic terminé")
    mt5.shutdown()

if __name__ == "__main__":
    main()
