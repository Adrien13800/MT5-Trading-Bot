#!/usr/bin/env python3
"""
Strategy Core - Logique partagee entre production et backtest.

Ce module contient TOUTES les fonctions pures de la strategie EMA 20 / SMA 50.
Aucune dependance a MT5, aucun side-effect. Uniquement des calculs sur DataFrames.

IMPORTANT: Toute modification ici impacte PROD et BACKTEST simultanement.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


# ============================================================================
# CONSTANTES DE STRATEGIE (source unique de verite)
# ============================================================================

# Indicateurs
EMA_FAST = 20
SMA_SLOW = 50

# Risk/Reward
RISK_REWARD_RATIO_FLAT = 3.5       # R:R 1:3.5 (V2_SAFE)
RISK_REWARD_RATIO_TRENDING = 3.5   # R:R 1:3.5 (V2_SAFE)

# Pente SMA 50
SMA_SLOPE_MIN = 0.00003

# ATR
USE_ATR_FILTER = True
ATR_PERIOD = 14
ATR_MULTIPLIER = 0.5
ATR_LOOKBACK = 20

# SL
USE_ATR_SL = True
ATR_SL_MULTIPLIER = 2.0  # V2_SAFE (etait 1.5)

# Directions
ALLOW_LONG = True
ALLOW_SHORT = True

# Filtres (tous desactives pour maximiser les trades)
USE_TREND_FILTER = True
USE_MOMENTUM_FILTER = False
USE_DISTANCE_FILTER = False
MAX_DISTANCE_FROM_EMA200 = 0.05
USE_EMA_SPREAD_FILTER = False
MAX_EMA_SPREAD = 0.10
USE_CONFIRMATION_FILTER = False
CONFIRMATION_BARS = 1
USE_VOLATILITY_FILTER = False
MAX_VOLATILITY_MULTIPLIER = 3.0
EMA_TOUCH_TOLERANCE = 0.01

# H1 trend filter
USE_H1_TREND_FILTER = True
H1_BARS_REQUIRED = 2  # Nombre de barres H1 fermees pour le filtre (2 = plus reactif, 3 = plus strict)

# Cooldown apres perte (en barres M5)
COOLDOWN_AFTER_LOSS = 2  # 0 = desactive, 2 = attendre 10 min apres un SL (optimise R5)

# Time exit: fermer un trade apres N minutes sans toucher SL ni TP
MAX_TRADE_DURATION_MINUTES = 360  # 360 min = 6h (V2_SAFE, etait 210)


# ============================================================================
# ENUMS
# ============================================================================

class TradeType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingSession(Enum):
    ASIA = "ASIA"          # 00:00 - 08:00 UTC
    EUROPE = "EUROPE"      # 08:00 - 14:00 UTC
    US = "US"              # 14:00 - 21:00 UTC
    OFF_HOURS = "OFF"      # 21:00 - 00:00 UTC


# Session blocking (V2_SAFE: EU only)
BLOCKED_SESSIONS = [TradingSession.US, TradingSession.ASIA]

# R:R par session (V2_SAFE: EU 3.5)
SESSION_RR = {
    TradingSession.EUROPE: 3.5,
}

# Heures autorisees (V2_SAFE: bloque 9h et 13h UTC)
ALLOWED_HOURS = [8, 10, 11, 12]  # None = toutes les heures

# Jours bloques (V2_SAFE: mercredi)
BLOCKED_DAYS = [2]  # 0=Lundi, 1=Mardi, 2=Mercredi, 3=Jeudi, 4=Vendredi


class MarketCondition(Enum):
    BULL = "BULL"
    BEAR = "BEAR"


class MarketTrend(Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"


# ============================================================================
# CALCUL DES INDICATEURS
# ============================================================================

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule EMA 20, SMA 50 et ATR sur un DataFrame OHLC.
    Modifie le DataFrame in-place et le retourne.

    Le DataFrame doit avoir les colonnes: open, high, low, close
    et etre trie par date (plus ancien -> plus recent).
    """
    df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df[f'SMA_{SMA_SLOW}'] = df['close'].rolling(window=SMA_SLOW).mean()

    if USE_ATR_FILTER:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()

    return df


# ============================================================================
# SESSIONS DE TRADING
# ============================================================================

def get_trading_session(trade_time: datetime) -> TradingSession:
    """Determine la session de trading basee sur l'heure UTC."""
    hour = trade_time.hour
    if 0 <= hour < 8:
        return TradingSession.ASIA
    elif 8 <= hour < 14:
        return TradingSession.EUROPE
    elif 14 <= hour < 21:
        return TradingSession.US
    else:
        return TradingSession.OFF_HOURS


def is_valid_trading_session(trade_time: datetime) -> bool:
    """True si on est dans une session valide (pas OFF_HOURS ni bloquee, heure/jour ok)."""
    session = get_trading_session(trade_time)
    if session == TradingSession.OFF_HOURS:
        return False
    if session in BLOCKED_SESSIONS:
        return False
    # Filtre jours bloques (V2_SAFE: mercredi)
    if BLOCKED_DAYS and trade_time.weekday() in BLOCKED_DAYS:
        return False
    # Filtre heures autorisees (V2_SAFE: 8, 10, 11, 12)
    if ALLOWED_HOURS is not None and trade_time.hour not in ALLOWED_HOURS:
        return False
    return True


# ============================================================================
# FILTRES
# ============================================================================

def is_sma50_flat(df: pd.DataFrame) -> bool:
    """True si la SMA 50 est plate (pente < seuil)."""
    if len(df) < 2:
        return True
    sma50_current = df[f'SMA_{SMA_SLOW}'].iloc[-1]
    sma50_prev = df[f'SMA_{SMA_SLOW}'].iloc[-2]
    slope = abs(sma50_current - sma50_prev)
    min_slope = sma50_current * SMA_SLOPE_MIN
    return slope < min_slope


def get_risk_reward_ratio(df: pd.DataFrame) -> float:
    """Retourne le R:R adapte par session (R6) ou par pente SMA50."""
    # R:R par session si configure
    if SESSION_RR and len(df) > 0:
        current_time = df.index[-1]
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime()
        session = get_trading_session(current_time)
        if session in SESSION_RR:
            return SESSION_RR[session]

    if is_sma50_flat(df):
        return RISK_REWARD_RATIO_FLAT
    return RISK_REWARD_RATIO_TRENDING


def check_atr_filter(df: pd.DataFrame) -> bool:
    """Filtre ATR: volatilite suffisante pour eviter les faux signaux."""
    if not USE_ATR_FILTER or 'ATR' not in df.columns:
        return True
    if len(df) < ATR_LOOKBACK + 1:
        return False
    current_atr = df['ATR'].iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return True
    atr_values = df['ATR'].iloc[-1 - ATR_LOOKBACK:-1]
    atr_avg = atr_values.mean()
    if current_atr < (atr_avg * ATR_MULTIPLIER):
        return False
    candle_range = df['high'].iloc[-1] - df['low'].iloc[-1]
    min_range = current_atr * ATR_MULTIPLIER
    return candle_range >= min_range


def check_trend_filter(df: pd.DataFrame, trade_type: TradeType) -> bool:
    """Verifie que la bougie cloture au-dessus/en-dessous de l'EMA 20."""
    if not USE_TREND_FILTER or len(df) < 1:
        return True
    current = df.iloc[-1]
    price_close = current['close']
    ema20 = current[f'EMA_{EMA_FAST}']
    if trade_type == TradeType.LONG:
        return price_close > ema20
    else:
        return price_close < ema20


def check_momentum_filter(df: pd.DataFrame, trade_type: TradeType) -> bool:
    """Verifie le momentum avant l'entree."""
    if not USE_MOMENTUM_FILTER or len(df) < 3:
        return True
    current = df.iloc[-1]
    prev = df.iloc[-2]
    price_momentum = current['close'] - prev['close']
    if trade_type == TradeType.LONG:
        return price_momentum > 0
    else:
        return price_momentum < 0


def check_distance_from_sma50(df: pd.DataFrame, trade_type: TradeType) -> bool:
    """Evite les entrees trop loin de la SMA 50."""
    if not USE_DISTANCE_FILTER or len(df) < 1:
        return True
    current = df.iloc[-1]
    price = current['close']
    sma50 = current[f'SMA_{SMA_SLOW}']
    if sma50 <= 0 or pd.isna(sma50):
        return True
    distance_pct = abs(price - sma50) / sma50
    return distance_pct <= MAX_DISTANCE_FROM_EMA200


def check_ema_spread(df: pd.DataFrame) -> bool:
    """Evite les spreads trop larges entre EMA20 et SMA50."""
    if not USE_EMA_SPREAD_FILTER or len(df) < 1:
        return True
    current = df.iloc[-1]
    ema20 = current[f'EMA_{EMA_FAST}']
    sma50 = current[f'SMA_{SMA_SLOW}']
    if sma50 <= 0 or pd.isna(sma50):
        return True
    spread_pct = abs(ema20 - sma50) / sma50
    return spread_pct <= MAX_EMA_SPREAD


def check_confirmation_filter(df: pd.DataFrame, trade_type: TradeType) -> bool:
    """Confirmation sur plusieurs bougies."""
    if not USE_CONFIRMATION_FILTER or len(df) < CONFIRMATION_BARS + 1:
        return True
    recent_closes = df['close'].iloc[-(CONFIRMATION_BARS + 1):]
    if trade_type == TradeType.LONG:
        return recent_closes.iloc[-1] > recent_closes.iloc[0]
    else:
        return recent_closes.iloc[-1] < recent_closes.iloc[0]


def check_volatility_filter(df: pd.DataFrame) -> bool:
    """Evite les entrees en volatilite excessive."""
    if not USE_VOLATILITY_FILTER or 'ATR' not in df.columns or len(df) < ATR_LOOKBACK + 1:
        return True
    current_atr = df['ATR'].iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return True
    atr_values = df['ATR'].iloc[-1 - ATR_LOOKBACK:-1]
    atr_avg = atr_values.mean()
    if atr_avg <= 0:
        return True
    return current_atr <= (atr_avg * MAX_VOLATILITY_MULTIPLIER)


# ============================================================================
# TENDANCE H1
# ============================================================================

def check_h1_trend(df_h1: Optional[pd.DataFrame], trade_type: TradeType) -> bool:
    """
    Analyse les dernieres bougies H1 fermees pour determiner la tendance.

    Mode H1_BARS_REQUIRED=2 : la derniere H1 fermee doit aller dans le sens du trade.
    Mode H1_BARS_REQUIRED=3 : 3 barres, au moins 2 dans le sens + direction globale.

    Args:
        df_h1: DataFrame H1 filtre (barres fermees uniquement)
        trade_type: LONG ou SHORT

    Returns:
        True si la tendance H1 est alignee avec le trade propose
    """
    if not USE_H1_TREND_FILTER:
        return True

    n = H1_BARS_REQUIRED

    if df_h1 is None or len(df_h1) < n:
        return False

    prices = df_h1.iloc[-n:]['close'].values

    if n == 2:
        # Mode 2 barres: la derniere fermee doit aller dans le sens du trade
        if trade_type == TradeType.LONG:
            return prices[1] > prices[0]
        else:
            return prices[1] < prices[0]
    else:
        # Mode 3 barres (original strict)
        if trade_type == TradeType.LONG:
            if prices[-1] < prices[0]:
                return False
            rises = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i - 1])
            return rises >= 2
        else:
            if prices[-1] > prices[0]:
                return False
            falls = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i - 1])
            return falls >= 2


# ============================================================================
# DETECTION DE SIGNAL (CROSSOVER EMA20 / SMA50)
# ============================================================================

def check_long_signal(df_m5: pd.DataFrame, df_h1: Optional[pd.DataFrame] = None,
                      symbol: str = "") -> bool:
    """
    Verifie les conditions d'entree LONG sur M5.
    df_m5 ne contient que des barres fermees. iloc[-1] = derniere fermee.

    Args:
        df_m5: DataFrame M5 avec indicateurs calcules
        df_h1: DataFrame H1 filtre jusqu'au moment actuel (pour filtre tendance)
        symbol: nom du symbole (pour logging)

    Returns:
        True si signal LONG detecte
    """
    if not ALLOW_LONG:
        return False

    if len(df_m5) < 5:
        return False

    current_time = df_m5.index[-1]
    if hasattr(current_time, 'to_pydatetime'):
        current_time = current_time.to_pydatetime()

    # Filtre session
    if not is_valid_trading_session(current_time):
        return False

    # Filtre H1
    if USE_H1_TREND_FILTER:
        if not check_h1_trend(df_h1, TradeType.LONG):
            return False

    # Crossover EMA20 > SMA50
    current = df_m5.iloc[-1]
    prev = df_m5.iloc[-2]

    ema20_current = current[f'EMA_{EMA_FAST}']
    sma50_current = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']

    if ema20_prev >= sma50_prev:
        return False  # Pas de croisement haussier
    if ema20_current <= sma50_current:
        return False  # Pas encore au-dessus

    return True


def check_short_signal(df_m5: pd.DataFrame, df_h1: Optional[pd.DataFrame] = None,
                       symbol: str = "") -> bool:
    """
    Verifie les conditions d'entree SHORT sur M5.
    df_m5 ne contient que des barres fermees. iloc[-1] = derniere fermee.
    """
    if not ALLOW_SHORT:
        return False

    if len(df_m5) < 5:
        return False

    current_time = df_m5.index[-1]
    if hasattr(current_time, 'to_pydatetime'):
        current_time = current_time.to_pydatetime()

    # Filtre session
    if not is_valid_trading_session(current_time):
        return False

    # Filtre H1
    if USE_H1_TREND_FILTER:
        if not check_h1_trend(df_h1, TradeType.SHORT):
            return False

    # Crossover EMA20 < SMA50
    current = df_m5.iloc[-1]
    prev = df_m5.iloc[-2]

    ema20_current = current[f'EMA_{EMA_FAST}']
    sma50_current = current[f'SMA_{SMA_SLOW}']
    ema20_prev = prev[f'EMA_{EMA_FAST}']
    sma50_prev = prev[f'SMA_{SMA_SLOW}']

    if ema20_prev <= sma50_prev:
        return False  # Pas de croisement baissier
    if ema20_current >= sma50_current:
        return False  # Pas encore en-dessous

    return True


# ============================================================================
# CALCUL SL / TP
# ============================================================================

def calculate_sl_long(df: pd.DataFrame, lookback: int = 10) -> float:
    """
    Calcule le Stop Loss pour un LONG.
    Priorite ATR, fallback dernier swing low.
    """
    current_price = df['close'].iloc[-1]

    if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
        current_atr = df['ATR'].iloc[-1]
        if not pd.isna(current_atr) and current_atr > 0:
            return current_price - (current_atr * ATR_SL_MULTIPLIER)

    if len(df) < lookback:
        lookback = len(df)
    lows = df['low'].iloc[-lookback:]
    min_low = lows.min()
    return min_low * 0.999


def calculate_sl_short(df: pd.DataFrame, lookback: int = 10) -> float:
    """
    Calcule le Stop Loss pour un SHORT.
    Priorite ATR, fallback dernier swing high.
    """
    current_price = df['close'].iloc[-1]

    if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
        current_atr = df['ATR'].iloc[-1]
        if not pd.isna(current_atr) and current_atr > 0:
            return current_price + (current_atr * ATR_SL_MULTIPLIER)

    if len(df) < lookback:
        lookback = len(df)
    highs = df['high'].iloc[-lookback:]
    max_high = highs.max()
    return max_high * 1.001


def calculate_tp(entry_price: float, stop_loss: float, rr_ratio: float,
                 trade_type: TradeType) -> float:
    """Calcule le Take Profit a partir de l'entree, du SL et du R:R."""
    risk = abs(entry_price - stop_loss)
    if trade_type == TradeType.LONG:
        return entry_price + (risk * rr_ratio)
    else:
        return entry_price - (risk * rr_ratio)


# ============================================================================
# HELPERS
# ============================================================================

def get_market_condition(df: pd.DataFrame) -> MarketCondition:
    """Bull si prix > SMA50, Bear sinon."""
    if len(df) < 1:
        return MarketCondition.BULL
    current = df.iloc[-1]
    price = current['close']
    sma50 = current[f'SMA_{SMA_SLOW}']
    if pd.isna(sma50):
        return MarketCondition.BULL
    return MarketCondition.BULL if price >= sma50 else MarketCondition.BEAR


def get_market_trend(df: pd.DataFrame) -> MarketTrend:
    """Trending si SMA50 penche, Ranging sinon."""
    if is_sma50_flat(df):
        return MarketTrend.RANGING
    return MarketTrend.TRENDING


def get_h1_data_at_time(df_h1: pd.DataFrame, current_time: datetime) -> Optional[pd.DataFrame]:
    """
    Filtre le DataFrame H1 pour ne garder que les bougies FERMEES a current_time.

    Une barre H1 avec timestamp T couvre la periode [T, T+1h).
    Elle n'est fermee que lorsque current_time >= T + 1h.
    On filtre donc: bar.index < floor(current_time, 1h)
    ce qui exclut la barre H1 en cours (ouverte).

    Cela evite:
      - En PROD: utiliser une barre H1 dont le 'close' est le prix live (pas le close final)
      - En BACKTEST: un look-ahead bias (utiliser le close final d'une barre pas encore fermee)

    Retourne None si moins de 3 bougies fermees disponibles.
    """
    if df_h1 is None or len(df_h1) == 0:
        return None

    ts = pd.Timestamp(current_time)
    if hasattr(df_h1.index, 'tz') and df_h1.index.tz is not None and getattr(ts, 'tzinfo', None) is None:
        ts = ts.tz_localize(df_h1.index.tz)

    # Seules les barres dont l'heure de debut < heure courante arrondie sont fermees.
    # Ex: a 15:05 → cutoff = 15:00 → on garde les barres < 15:00 (derniere: 14:00, fermee a 15:00)
    cutoff = ts.floor('h')
    h1_closed = df_h1[df_h1.index < cutoff]

    if len(h1_closed) < 3:
        return None

    return h1_closed
