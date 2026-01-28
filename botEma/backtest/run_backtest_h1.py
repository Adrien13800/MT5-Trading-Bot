#!/usr/bin/env python3
"""
Backtest en H1 avec données Yahoo Finance (jusqu'à 2 ans)
Permet de valider la stratégie sur une période plus longue

Ce backtest utilise:
- Données H1 de Yahoo Finance (2 ans disponibles)
- Même logique de croisement EMA20/SMA50
- Analytics avancées (session, bull/bear, trending/ranging)
"""

import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError as e:
    print(f"ERREUR: Module manquant - {e}")
    print("   Installez avec: pip install yfinance pandas numpy openpyxl")
    sys.exit(1)

# Desactiver le cache yfinance pour eviter les erreurs sqlite
import os
os.environ['YF_CACHE_DIR'] = os.path.join(os.getcwd(), '.yf_cache')
# Creer le dossier cache si necessaire
if not os.path.exists(os.environ['YF_CACHE_DIR']):
    os.makedirs(os.environ['YF_CACHE_DIR'], exist_ok=True)


# ========== CONFIGURATION ==========
EMA_FAST = 20
SMA_SLOW = 50
RISK_REWARD_RATIO_FLAT = 1.0
RISK_REWARD_RATIO_TRENDING = 1.5
SMA_SLOPE_MIN = 0.0001  # Ajusté pour H1
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5
INITIAL_BALANCE = 10000.0
RISK_PERCENT = 0.5


class TradeType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingSession(Enum):
    ASIA = "ASIA"
    EUROPE = "EUROPE"
    US = "US"
    OFF_HOURS = "OFF"


class MarketCondition(Enum):
    BULL = "BULL"
    BEAR = "BEAR"


class MarketTrend(Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"


@dataclass
class SimulatedTrade:
    symbol: str
    type: TradeType
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = "OPEN"
    profit: float = 0.0
    session: Optional[TradingSession] = None
    market_condition: Optional[MarketCondition] = None
    market_trend: Optional[MarketTrend] = None
    day_of_week: int = 0


def download_yahoo_data(yahoo_symbol: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """Télécharge les données H1 depuis Yahoo Finance"""
    print(f"   Téléchargement {yahoo_symbol} depuis Yahoo Finance...")
    
    try:
        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(period=period, interval="1h")
        
        if df is None or len(df) == 0:
            return None
        
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'tick_volume'
        })
        
        df = df[['open', 'high', 'low', 'close', 'tick_volume']]
        
        # Calculer EMA et SMA
        df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df[f'SMA_{SMA_SLOW}'] = df['close'].rolling(window=SMA_SLOW).mean()
        
        # Calculer ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()
        
        print(f"   OK {len(df)} bougies H1 telechargees")
        print(f"   Periode: {df.index.min().strftime('%Y-%m-%d')} a {df.index.max().strftime('%Y-%m-%d')}")
        
        return df
        
    except Exception as e:
        print(f"   ERREUR: {e}")
        return None


def get_trading_session(trade_time: datetime) -> TradingSession:
    hour = trade_time.hour
    if 0 <= hour < 8:
        return TradingSession.ASIA
    elif 8 <= hour < 14:
        return TradingSession.EUROPE
    elif 14 <= hour < 21:
        return TradingSession.US
    else:
        return TradingSession.OFF_HOURS


def get_market_condition(df: pd.DataFrame) -> MarketCondition:
    current = df.iloc[-1]
    price = current['close']
    sma50 = current[f'SMA_{SMA_SLOW}']
    return MarketCondition.BULL if price >= sma50 else MarketCondition.BEAR


def get_market_trend(df: pd.DataFrame) -> Tuple[MarketTrend, float]:
    if len(df) < 10:
        return MarketTrend.RANGING, 0.0
    
    sma_values = df[f'SMA_{SMA_SLOW}'].iloc[-10:]
    if sma_values.isna().any():
        return MarketTrend.RANGING, 0.0
    
    sma_start = sma_values.iloc[0]
    sma_end = sma_values.iloc[-1]
    
    if sma_start <= 0:
        return MarketTrend.RANGING, 0.0
    
    slope_pct = (sma_end - sma_start) / sma_start
    
    if abs(slope_pct) >= 0.005:  # 0.5% sur 10 bougies H1
        return MarketTrend.TRENDING, slope_pct
    else:
        return MarketTrend.RANGING, slope_pct


def is_sma_flat(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return True
    sma_current = df[f'SMA_{SMA_SLOW}'].iloc[-1]
    sma_prev = df[f'SMA_{SMA_SLOW}'].iloc[-2]
    slope = abs(sma_current - sma_prev)
    min_slope = sma_current * SMA_SLOPE_MIN
    return slope < min_slope


def check_long_entry(df: pd.DataFrame) -> bool:
    if len(df) < 5:
        return False
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    ema20_current = current[f'EMA_{EMA_FAST}']
    sma50_current = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']
    
    if pd.isna(ema20_current) or pd.isna(sma50_current):
        return False
    
    # Croisement haussier
    if ema20_prev >= sma50_prev:
        return False
    if ema20_current <= sma50_current:
        return False
    
    return True


def check_short_entry(df: pd.DataFrame) -> bool:
    if len(df) < 5:
        return False
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    ema20_current = current[f'EMA_{EMA_FAST}']
    sma50_current = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']
    
    if pd.isna(ema20_current) or pd.isna(sma50_current):
        return False
    
    # Croisement baissier
    if ema20_prev <= sma50_prev:
        return False
    if ema20_current >= sma50_current:
        return False
    
    return True


def run_backtest(symbol: str, df: pd.DataFrame) -> List[SimulatedTrade]:
    """Exécute le backtest sur les données"""
    closed_trades = []
    open_trade = None
    
    print(f"\n   Exécution du backtest sur {len(df)} bougies...")
    
    for i in range(SMA_SLOW + 10, len(df)):
        market_data = df.iloc[:i+1]
        current_bar = df.iloc[i]
        current_time = df.index[i]
        
        # Gérer le trade ouvert
        if open_trade:
            if open_trade.type == TradeType.LONG:
                if current_bar['low'] <= open_trade.stop_loss:
                    open_trade.exit_price = open_trade.stop_loss
                    open_trade.exit_time = current_time
                    open_trade.exit_reason = "SL"
                    open_trade.profit = open_trade.exit_price - open_trade.entry_price
                    closed_trades.append(open_trade)
                    open_trade = None
                elif current_bar['high'] >= open_trade.take_profit:
                    open_trade.exit_price = open_trade.take_profit
                    open_trade.exit_time = current_time
                    open_trade.exit_reason = "TP"
                    open_trade.profit = open_trade.exit_price - open_trade.entry_price
                    closed_trades.append(open_trade)
                    open_trade = None
            else:  # SHORT
                if current_bar['high'] >= open_trade.stop_loss:
                    open_trade.exit_price = open_trade.stop_loss
                    open_trade.exit_time = current_time
                    open_trade.exit_reason = "SL"
                    open_trade.profit = open_trade.entry_price - open_trade.exit_price
                    closed_trades.append(open_trade)
                    open_trade = None
                elif current_bar['low'] <= open_trade.take_profit:
                    open_trade.exit_price = open_trade.take_profit
                    open_trade.exit_time = current_time
                    open_trade.exit_reason = "TP"
                    open_trade.profit = open_trade.entry_price - open_trade.exit_price
                    closed_trades.append(open_trade)
                    open_trade = None
        
        # Chercher de nouveaux signaux si pas de trade ouvert
        if open_trade is None:
            if check_long_entry(market_data):
                entry_price = current_bar['close']
                atr = market_data['ATR'].iloc[-1] if 'ATR' in market_data.columns else entry_price * 0.01
                stop_loss = entry_price - (atr * ATR_SL_MULTIPLIER)
                rr = RISK_REWARD_RATIO_TRENDING if not is_sma_flat(market_data) else RISK_REWARD_RATIO_FLAT
                take_profit = entry_price + ((entry_price - stop_loss) * rr)
                
                trade = SimulatedTrade(
                    symbol=symbol,
                    type=TradeType.LONG,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_time=current_time
                )
                trade.session = get_trading_session(current_time)
                trade.market_condition = get_market_condition(market_data)
                trade.market_trend, _ = get_market_trend(market_data)
                trade.day_of_week = current_time.weekday()
                open_trade = trade
                
            elif check_short_entry(market_data):
                entry_price = current_bar['close']
                atr = market_data['ATR'].iloc[-1] if 'ATR' in market_data.columns else entry_price * 0.01
                stop_loss = entry_price + (atr * ATR_SL_MULTIPLIER)
                rr = RISK_REWARD_RATIO_TRENDING if not is_sma_flat(market_data) else RISK_REWARD_RATIO_FLAT
                take_profit = entry_price - ((stop_loss - entry_price) * rr)
                
                trade = SimulatedTrade(
                    symbol=symbol,
                    type=TradeType.SHORT,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_time=current_time
                )
                trade.session = get_trading_session(current_time)
                trade.market_condition = get_market_condition(market_data)
                trade.market_trend, _ = get_market_trend(market_data)
                trade.day_of_week = current_time.weekday()
                open_trade = trade
    
    # Fermer le trade restant
    if open_trade:
        open_trade.exit_price = df.iloc[-1]['close']
        open_trade.exit_time = df.index[-1]
        open_trade.exit_reason = "END"
        if open_trade.type == TradeType.LONG:
            open_trade.profit = open_trade.exit_price - open_trade.entry_price
        else:
            open_trade.profit = open_trade.entry_price - open_trade.exit_price
        closed_trades.append(open_trade)
    
    return closed_trades


def print_analytics(trades: List[SimulatedTrade], symbol: str):
    """Affiche les analytics avancées"""
    if not trades:
        print("   Aucun trade")
        return
    
    total = len(trades)
    winning = len([t for t in trades if t.profit > 0])
    wr = (winning / total * 100) if total > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"RÉSULTATS {symbol} (Backtest H1 - Yahoo Finance)")
    print(f"{'='*60}")
    print(f"   Total trades: {total}")
    print(f"   Gagnants: {winning} ({wr:.1f}%)")
    print(f"   Perdants: {total - winning} ({100-wr:.1f}%)")
    
    # Par session
    print(f"\n[PAR SESSION]")
    for session in TradingSession:
        sess_trades = [t for t in trades if t.session == session]
        if sess_trades:
            sess_win = len([t for t in sess_trades if t.profit > 0])
            sess_wr = (sess_win / len(sess_trades) * 100)
            print(f"   {session.value:10} | {len(sess_trades):3} trades | WR: {sess_wr:5.1f}%")
    
    # Bull vs Bear
    print(f"\n[BULL vs BEAR]")
    for condition in MarketCondition:
        cond_trades = [t for t in trades if t.market_condition == condition]
        if cond_trades:
            cond_win = len([t for t in cond_trades if t.profit > 0])
            cond_wr = (cond_win / len(cond_trades) * 100)
            print(f"   {condition.value:10} | {len(cond_trades):3} trades | WR: {cond_wr:5.1f}%")
    
    # Trending vs Ranging
    print(f"\n[TRENDING vs RANGING]")
    for trend in MarketTrend:
        trend_trades = [t for t in trades if t.market_trend == trend]
        if trend_trades:
            trend_win = len([t for t in trend_trades if t.profit > 0])
            trend_wr = (trend_win / len(trend_trades) * 100)
            print(f"   {trend.value:10} | {len(trend_trades):3} trades | WR: {trend_wr:5.1f}%")
    
    # Par jour
    print(f"\n[PAR JOUR]")
    day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    for day in range(7):
        day_trades = [t for t in trades if t.day_of_week == day]
        if day_trades:
            day_win = len([t for t in day_trades if t.profit > 0])
            day_wr = (day_win / len(day_trades) * 100)
            print(f"   {day_names[day]:10} | {len(day_trades):3} trades | WR: {day_wr:5.1f}%")
    
    print(f"\n{'='*60}")


def main():
    print("="*60)
    print("BACKTEST H1 - Données Yahoo Finance (2 ans)")
    print("Stratégie: Croisement EMA 20 / SMA 50")
    print("="*60)
    
    # Symboles Yahoo Finance
    symbols = {
        "US30": "^DJI",      # Dow Jones
        "US100": "^NDX",     # Nasdaq 100
    }
    
    all_trades = []
    
    for name, yahoo_symbol in symbols.items():
        print(f"\n{'='*60}")
        print(f"Traitement de {name} ({yahoo_symbol})")
        print(f"{'='*60}")
        
        # Télécharger les données
        df = download_yahoo_data(yahoo_symbol, period="2y")
        if df is None:
            continue
        
        # Exécuter le backtest
        trades = run_backtest(name, df)
        all_trades.extend(trades)
        
        # Afficher les résultats
        print_analytics(trades, name)
    
    # Résumé global
    if all_trades:
        print(f"\n{'='*60}")
        print("RÉSUMÉ GLOBAL (tous symboles)")
        print(f"{'='*60}")
        print_analytics(all_trades, "TOUS")
    
    print("\nOK Backtest H1 termine!")
    print("   Compare ces résultats avec ton backtest M5 pour valider les patterns")


if __name__ == "__main__":
    main()
