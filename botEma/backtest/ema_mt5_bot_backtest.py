#!/usr/bin/env python3
"""
Bot de Backtest MT5 - Stratégie EMA 20 / SMA 50 (Croisement)
Entrée en position au croisement EMA 20 / SMA 50
Timeframe: 5 minutes
Teste sur 3 ans de données historiques
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
except ImportError:
    print("ERREUR: MetaTrader5, pandas ou numpy n'est pas installe.")
    print("   Installez-les avec: pip install MetaTrader5 pandas numpy")
    sys.exit(1)

# Configuration EMA/SMA
EMA_FAST = 20   # EMA rapide (20)
SMA_SLOW = 50   # SMA lente (50) - remplace EMA 200
RISK_REWARD_RATIO = 1.5  # R:R par défaut (sera adapté selon pente SMA 50)
RISK_REWARD_RATIO_FLAT = 1.0  # R:R 1:1 quand SMA 50 est plate
RISK_REWARD_RATIO_TRENDING = 1.5  # R:R 1:1.5 quand SMA 50 penche

# Filtres OPTIMISÉS pour plus de trades ET meilleur WR (PARAMÈTRES ORIGINAUX - 830 trades)
SMA_SLOPE_MIN = 0.00003  # Pente minimale SMA 50 pour considérer qu'elle "penche" (sinon plate)
USE_ATR_FILTER = True
ATR_PERIOD = 14
ATR_MULTIPLIER = 0.5  # Volatilité minimale réduite (0.5 pour plus de trades)
ATR_LOOKBACK = 20  # Périodes pour moyenne ATR

# Trading (LONG et SHORT activés)
ALLOW_LONG = True  # Activé
ALLOW_SHORT = True  # Activé
MAGIC_NUMBER = 123456
TRADE_COMMENT = "EMA20_SMA50_Cross"

# Protection contre le sur-trading
MIN_BARS_BETWEEN_SAME_SETUP = 0  # Pas de restriction en backtest
COOLDOWN_AFTER_LOSS = 0  # Pas de cooldown en backtest

# Filtres pour plus de trades
USE_TREND_FILTER = True  # Filtre de tendance (désactivé pour stratégie croisement)
USE_MOMENTUM_FILTER = False  # DÉSACTIVÉ pour plus de trades
EMA_TOUCH_TOLERANCE = 0.01  # Augmenté à 1% pour plus de détections (pullback plus permissif)
USE_ATR_SL = True  # Utiliser ATR pour le SL (plus intelligent)
ATR_SL_MULTIPLIER = 1.5  # Multiplicateur ATR pour le SL
REQUIRE_IMPULSE_BREAK = False  # DÉSACTIVÉ
REQUIRE_REJECTION = False  # DÉSACTIVÉ
USE_ACTIVE_SESSIONS = False  # Optionnel: trader uniquement sessions actives

# NOUVEAUX FILTRES INTELLIGENTS pour améliorer le WR (ciblent les mauvais trades)
# ASSOUPLIS pour augmenter le nombre de trades
USE_DISTANCE_FILTER = False  # DÉSACTIVÉ - Trop restrictif (max 2% était trop strict)
MAX_DISTANCE_FROM_EMA200 = 0.05  # Augmenté à 5% si réactivé
USE_EMA_SPREAD_FILTER = False  # DÉSACTIVÉ - Trop restrictif pour augmenter les trades
MAX_EMA_SPREAD = 0.10  # Augmenté à 10% si réactivé
USE_CONFIRMATION_FILTER = False  # DÉSACTIVÉ - Réduit trop les trades
CONFIRMATION_BARS = 1  # Réduit à 1 bougie si réactivé
USE_VOLATILITY_FILTER = False  # DÉSACTIVÉ - Trop restrictif
MAX_VOLATILITY_MULTIPLIER = 3.0  # Augmenté à 3.0x si réactivé

# Timeframe MT5 (M5 = 5 minutes pour les trades, H1 = 1 heure pour la tendance)
TIMEFRAME_MT5 = mt5.TIMEFRAME_M5
TIMEFRAME_H1 = mt5.TIMEFRAME_H1  # Pour l'analyse de tendance supérieure

# Filtre de tendance H1 (ne trader que dans le sens de la tendance H1)
USE_H1_TREND_FILTER = True  # ACTIVÉ - Tendance déterminée par les 3 dernières bougies H1


class TradeType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingSession(Enum):
    """Sessions de trading"""
    ASIA = "ASIA"        # 00:00 - 08:00 UTC
    EUROPE = "EUROPE"    # 08:00 - 14:00 UTC
    US = "US"            # 14:00 - 21:00 UTC
    OFF_HOURS = "OFF"    # 21:00 - 00:00 UTC


class MarketCondition(Enum):
    """Condition de marché"""
    BULL = "BULL"        # Prix au-dessus de SMA 50
    BEAR = "BEAR"        # Prix en-dessous de SMA 50


class MarketTrend(Enum):
    """Tendance du marché"""
    TRENDING = "TRENDING"  # SMA 50 avec pente significative
    RANGING = "RANGING"    # SMA 50 plate


@dataclass
class SimulatedTrade:
    """Position simulée pour le backtest"""
    symbol: str
    type: TradeType
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    entry_time: datetime
    entry_bar_index: int
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_bar_index: Optional[int] = None
    exit_reason: str = "OPEN"  # OPEN, SL, TP, MANUAL
    profit: float = 0.0
    profit_pct: float = 0.0
    # Analytics avancées
    session: Optional[TradingSession] = None       # Session de trading à l'entrée
    market_condition: Optional[MarketCondition] = None  # Bull ou Bear à l'entrée
    market_trend: Optional[MarketTrend] = None     # Trending ou Ranging à l'entrée
    sma_slope: float = 0.0                         # Pente de la SMA 50 à l'entrée
    atr_value: float = 0.0                         # ATR à l'entrée
    day_of_week: int = 0                           # Jour de la semaine (0=Lundi, 6=Dimanche)
    risk_reward_ratio: float = 0.0                 # R:R utilisé pour ce trade (1.0 ou 1.5)


@dataclass
class BacktestStats:
    """Statistiques du backtest"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_profit: float = 0.0
    total_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    return_pct: float = 0.0
    # Statistiques positions simultanées
    max_concurrent_positions: int = 0
    times_multiple_positions: int = 0
    times_long_short_simultaneous: int = 0
    # Statistiques par R:R
    rr_1_0_count: int = 0
    rr_1_0_pct: float = 0.0
    rr_1_0_win_rate: float = 0.0
    rr_1_0_pnl: float = 0.0
    rr_1_5_count: int = 0
    rr_1_5_pct: float = 0.0
    rr_1_5_win_rate: float = 0.0
    rr_1_5_pnl: float = 0.0
    rr_3_0_count: int = 0
    rr_3_0_pct: float = 0.0
    rr_3_0_win_rate: float = 0.0
    rr_3_0_pnl: float = 0.0


class MT5BacktestBot:
    """Bot de backtest avec la MÊME logique que la production"""
    
    def __init__(self, login: int, password: str, server: str, 
                 symbols: List[str], risk_percent: float = 0.5, 
                 max_daily_loss: float = -250.0, initial_balance: float = 10000.0):
        self.login = login
        self.password = password
        self.server = server
        self.symbols = symbols
        self.risk_percent = risk_percent
        self.max_daily_loss = max_daily_loss
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.equity = initial_balance
        
        # Suivi des trades (plusieurs trades par symbole possible)
        self.open_trades: Dict[str, List[SimulatedTrade]] = {}  # Liste de trades par symbole
        self.closed_trades: List[SimulatedTrade] = []
        
        # Trades simulés avec 3.0R pour comparaison (uniquement pour les trades faits avec 1.5R)
        self.open_trades_3r: Dict[str, List[SimulatedTrade]] = {}  # Trades 3.0R simulés
        self.closed_trades_3r: List[SimulatedTrade] = []  # Trades 3.0R fermés
        
        self.last_bar_time: Dict[str, datetime] = {}
        self.daily_start_balance: Optional[float] = None
        self.trading_stopped_daily: bool = False
        self.last_trading_date: Optional[datetime.date] = None
        self.last_trade_by_symbol_type: Dict[Tuple[str, TradeType], datetime] = {}
        self.last_loss_time: Optional[datetime] = None  # Pour cooldown après perte
        
        # Données historiques par symbole (M5 pour les trades)
        self.historical_data: Dict[str, pd.DataFrame] = {}
        
        # Données H1 pour l'analyse de tendance supérieure
        self.h1_data: Dict[str, pd.DataFrame] = {}
        
        # Suivi pour statistiques
        self.equity_curve: List[float] = [initial_balance]
        self.daily_balances: List[Tuple[datetime.date, float]] = []
        
        print("=" * 70)
        print("BACKTEST BOT MT5 - Initialisation")
        print("=" * 70)
        
        # Initialiser MT5
        if not mt5.initialize():
            print(f"ERREUR initialisation MT5: {mt5.last_error()}")
            sys.exit(1)
        
        print("OK MT5 initialise")
        
        # Se connecter au compte (pour récupérer les données)
        print(f"Connexion au compte {login} sur {server}...")
        if not self.connect():
            print("ERREUR: Echec de la connexion MT5")
            sys.exit(1)
        
        # Vérifier les infos du compte pour confirmer la connexion
        account_info = mt5.account_info()
        if account_info:
            print(f"OK Connecte au compte: {account_info.login}")
            print(f"   Serveur: {account_info.server}")
            print(f"   Balance: {account_info.balance:.2f} {account_info.currency}")
            print(f"   Nom du compte: {account_info.name if hasattr(account_info, 'name') else 'N/A'}")
        else:
            print(f"ATTENTION: Connecte mais impossible de recuperer les infos du compte")
            print(f"   Login utilisé: {login}")
        print(f"Balance initiale: {initial_balance:.2f}")
        print(f"Symboles: {', '.join(symbols)}")
        print(f"EMA Fast: {EMA_FAST}, SMA Slow: {SMA_SLOW}")
        print(f"Risque par trade: {risk_percent}%")
        print(f"R:R adaptatif: 1:{RISK_REWARD_RATIO_FLAT} (SMA50 plate) / 1:{RISK_REWARD_RATIO_TRENDING} (SMA50 penche)")
        print(f"Protection quotidienne: {max_daily_loss:.2f}")
        print("=" * 70)
    
    def connect(self) -> bool:
        """Se connecte au compte MT5"""
        print(f"   Tentative de connexion avec login={self.login}, server={self.server}...")
        authorized = mt5.login(
            login=self.login,
            password=self.password,
            server=self.server
        )
        
        if not authorized:
            error = mt5.last_error()
            print(f"   ERREUR: Echec connexion MT5:")
            print(f"      Code erreur: {error}")
            if hasattr(error, 'description'):
                print(f"      Description: {error.description}")
            return False
        
        print(f"   OK Connexion reussie!")
        return True
    
    def find_symbol_variant(self, symbol_base: str) -> Optional[str]:
        """Trouve la variante exacte d'un symbole dans MT5"""
        variants = [
            symbol_base,
            symbol_base.upper(),
            symbol_base.lower(),
            symbol_base.capitalize(),
            symbol_base.replace('.', ''),
            symbol_base.replace('.', '_'),
            symbol_base.replace('Cash', 'cash'),
            symbol_base.replace('cash', 'Cash'),
            symbol_base.replace('.Cash', '.cash'),
            symbol_base.replace('.cash', '.Cash'),
        ]
        
        for variant in variants:
            symbol_info = mt5.symbol_info(variant)
            if symbol_info is not None:
                return variant
        
        all_symbols = mt5.symbols_get()
        if all_symbols:
            matching = []
            symbol_upper = symbol_base.upper()
            for sym in all_symbols:
                sym_name = sym.name
                if symbol_upper in sym_name.upper() or sym_name.upper() in symbol_upper:
                    matching.append(sym_name)
            
            if matching:
                return matching[0]
        
        return None
    
    def load_from_csv(self, csv_path: str, symbol: str = "") -> Optional[pd.DataFrame]:
        """
        Charge les données historiques depuis un fichier CSV (Yahoo Finance ou autre source)
        
        Args:
            csv_path: Chemin vers le fichier CSV
            symbol: Nom du symbole (optionnel, pour logging)
        
        Returns:
            DataFrame avec les données au format attendu
        """
        try:
            print(f"Chargement depuis CSV: {csv_path}")
            
            # Lire le CSV - essayer d'abord avec index_col=0, sinon lire normalement
            try:
                df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                # Si l'index n'est pas datetime, essayer de le convertir
                if not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index)
            except:
                # Si ça échoue, lire sans index_col et utiliser 'time' comme colonne
                df = pd.read_csv(csv_path, parse_dates=False)
                
                # Si 'time' est une colonne, l'utiliser comme index
                if 'time' in df.columns:
                    # Convertir en datetime en gérant les timezones (convertir en UTC puis supprimer le timezone)
                    df['time'] = pd.to_datetime(df['time'], utc=True)
                    # Supprimer le timezone pour avoir un index datetime simple
                    df['time'] = df['time'].dt.tz_localize(None)
                    df.set_index('time', inplace=True)
                elif df.index.name == 'time' or (hasattr(df.index, 'name') and df.index.name is None):
                    # Essayer de convertir l'index directement
                    df.index = pd.to_datetime(df.index)
                else:
                    print(f"   ERREUR: Colonne 'time' non trouvee dans le CSV")
                    return None
            
            # Vérifier les colonnes nécessaires
            required_cols = ['open', 'high', 'low', 'close']
            if not all(col in df.columns for col in required_cols):
                # Essayer avec des noms différents (Yahoo Finance style)
                if 'Open' in df.columns:
                    df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'}, inplace=True)
                else:
                    print(f"   ERREUR: Colonnes manquantes dans le CSV. Attendu: {required_cols}")
                    print(f"   Colonnes trouvees: {df.columns.tolist()}")
                    return None
            
            # Ajouter tick_volume si absent
            if 'tick_volume' not in df.columns:
                if 'Volume' in df.columns:
                    df['tick_volume'] = df['Volume']
                else:
                    df['tick_volume'] = 0
            
            # S'assurer que l'index est datetime
            if not isinstance(df.index, pd.DatetimeIndex):
                try:
                    # Gérer les timezones si présentes
                    df.index = pd.to_datetime(df.index, utc=True)
                    # Supprimer le timezone pour avoir un index datetime simple
                    if df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                except Exception as e:
                    print(f"   ERREUR: Impossible de convertir l'index en datetime: {e}")
                    return None
            elif df.index.tz is not None:
                # Si l'index est déjà datetime mais avec timezone, la supprimer
                df.index = df.index.tz_localize(None)
            
            # Trier par date
            df.sort_index(inplace=True)
            
            # Calculer EMA 20 et SMA 50
            df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
            df[f'SMA_{SMA_SLOW}'] = df['close'].rolling(window=SMA_SLOW).mean()
            
            # Calculer l'ATR si nécessaire
            if USE_ATR_FILTER:
                high_low = df['high'] - df['low']
                high_close = np.abs(df['high'] - df['close'].shift())
                low_close = np.abs(df['low'] - df['close'].shift())
                ranges = pd.concat([high_low, high_close, low_close], axis=1)
                true_range = ranges.max(axis=1)
                df['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()
            
            oldest_date = df.index.min()
            newest_date = df.index.max()
            days_available = (newest_date - oldest_date).days
            
            print(f"   OK {len(df)} bougies chargees")
            print(f"   Periode: {oldest_date.strftime('%Y-%m-%d')} a {newest_date.strftime('%Y-%m-%d')}")
            print(f"   Duree: {days_available} jours ({days_available/30:.1f} mois, {days_available/365:.1f} ans)")
            
            return df
            
        except Exception as e:
            print(f"   ERREUR lors du chargement du CSV: {e}")
            return None
    
    def load_historical_data(self, symbol: str, years: int = 3, use_all_available: bool = True, last_n_months: Optional[int] = None) -> Optional[pd.DataFrame]:
        """
        Charge les données historiques pour un symbole
        
        Args:
            symbol: Symbole à charger
            years: Nombre d'années demandées (utilisé si use_all_available=False et last_n_months=None)
            use_all_available: Si True, charge TOUTES les données disponibles (ignore years)
            last_n_months: Si > 0, charge uniquement les N derniers mois (prioritaire sur years)
        """
        if last_n_months is not None and last_n_months > 0:
            print(f"Chargement des donnees historiques pour {symbol} (derniers {last_n_months} mois)...")
        elif use_all_available:
            print(f"Chargement de TOUTES les donnees historiques disponibles pour {symbol}...")
        else:
            print(f"Chargement des donnees historiques pour {symbol} ({years} ans)...")
        
        # Trouver le symbole exact
        original_symbol = symbol
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            found = self.find_symbol_variant(symbol)
            if found:
                print(f"   Symbole trouve: '{original_symbol}' -> '{found}'")
                symbol = found
                symbol_info = mt5.symbol_info(symbol)
            else:
                print(f"   ERREUR: Symbole {original_symbol} non trouve dans MT5")
                print(f"   Astuce: Utilisez list_symbols.py pour voir les symboles disponibles")
                return None
        else:
            print(f"   OK Symbole '{symbol}' trouve")
        
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                print(f"   ERREUR: Impossible d'activer le symbole {symbol}")
                return None
            print(f"   OK Symbole '{symbol}' active")
        
        end_date = datetime.now()
        
        # Mode "derniers N mois" (prioritaire)
        if last_n_months is not None and last_n_months > 0:
            start_date = end_date - timedelta(days=last_n_months * 30)
            print(f"   Periode demandee: {start_date.strftime('%Y-%m-%d')} a {end_date.strftime('%Y-%m-%d')}")
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_MT5, start_date, end_date)
            if rates is None or len(rates) == 0:
                bars_needed = last_n_months * 30 * 288  # ~288 bougies M5/jour
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, bars_needed)
                if rates is not None and len(rates) > 0:
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
                    else:
                        rates = None
        # Si use_all_available, on récupère directement toutes les données
        elif use_all_available:
            print(f"   Mode: Récupération de TOUTES les données disponibles (pas de limite)")
            rates = None
        else:
            # Calculer la date de début (3 ans en arrière)
            start_date = end_date - timedelta(days=years * 365)
            
            print(f"   Periode demandee: {start_date.strftime('%Y-%m-%d')} a {end_date.strftime('%Y-%m-%d')}")
            
            # Récupérer les données (M5 = 5 minutes)
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_MT5, start_date, end_date)
            
            # Si ça ne fonctionne pas, essayer avec copy_rates_from_pos
            if rates is None or len(rates) == 0:
                print(f"   ATTENTION: copy_rates_range n'a pas fonctionne, tentative avec copy_rates_from_pos...")
                bars_needed = years * 365 * 288
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, bars_needed)
                
                if rates is not None and len(rates) > 0:
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
                        print(f"   OK {len(rates)} bougies recuperees avec copy_rates_from_pos")
                    else:
                        rates = None
        
        # Si toujours pas de données OU si use_all_available (sans last_n_months), récupérer TOUTES les données disponibles
        if rates is None or len(rates) == 0:
            print(f"   ATTENTION: Pas de donnees pour la periode demandee, recuperation de TOUTES les donnees disponibles...")
            
            # Essayer de récupérer progressivement plus de données
            # Augmenté pour récupérer le maximum possible
            max_attempts = [50000, 100000, 200000, 500000, 1000000, 2000000]  # Essayer avec de plus en plus de bougies
            
            rates = None
            best_rates = None
            best_count = 0
            
            for max_bars in max_attempts:
                print(f"      Tentative avec {max_bars} bougies...", end=" ")
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, max_bars)
                
                if rates is not None and len(rates) > 0:
                    print(f"OK {len(rates)} bougies")
                    # Garder le meilleur résultat
                    if len(rates) > best_count:
                        best_rates = rates
                        best_count = len(rates)
                    
                    # Vérifier si on a récupéré toutes les données disponibles
                    if len(rates) < max_bars:
                        # On a récupéré toutes les données disponibles
                        print(f"      OK Toutes les donnees disponibles recuperees")
                        break
                    # Sinon, on continue avec plus de bougies
                else:
                    print(f"ERREUR: Echec")
            
            # Utiliser le meilleur résultat obtenu
            rates = best_rates
            
            if rates is not None and len(rates) > 0:
                df_temp = pd.DataFrame(rates)
                df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                oldest_date = df_temp['time'].min()
                newest_date = df_temp['time'].max()
                days_available = (newest_date - oldest_date).days
                
                print(f"   OK {len(rates)} bougies recuperees")
                print(f"   Periode disponible: {oldest_date.strftime('%Y-%m-%d')} a {newest_date.strftime('%Y-%m-%d')}")
                print(f"   Duree: {days_available} jours ({days_available/30:.1f} mois)")
                
                if days_available < (years * 365):
                    print(f"   ATTENTION: Seulement {days_available} jours disponibles au lieu de {years * 365} jours demandes")
                    print(f"   Le backtest utilisera toutes les donnees disponibles ({len(rates)} bougies)")
                
                # Utiliser toutes les données disponibles
            else:
                print(f"   ERREUR: Aucune donnee historique disponible pour {symbol}")
                print(f"   Verifiez que:")
                print(f"      - Le symbole est disponible sur votre broker")
                print(f"      - Les donnees historiques sont telechargees dans MT5")
                print(f"      - Vous etes connecte au bon serveur")
                print(f"   Astuce: Lancez d'abord 'py force_download.py' pour telecharger les donnees")
                return None
        
        # Convertir en DataFrame
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        
        # Calculer EMA 20 et SMA 50
        df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df[f'SMA_{SMA_SLOW}'] = df['close'].rolling(window=SMA_SLOW).mean()
        
        # Calculer l'ATR si nécessaire (MÊME logique que la prod)
        if USE_ATR_FILTER:
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            df['ATR'] = true_range.rolling(window=ATR_PERIOD).mean()
        
        # Fenêtre "derniers N mois" si demandée (ex. après fallback tout-charger)
        if last_n_months is not None and last_n_months > 0 and len(df) > 0:
            cutoff = df.index[-1] - pd.Timedelta(days=last_n_months * 30)
            df = df[df.index >= cutoff].copy()
        
        print(f"   OK {len(df)} bougies chargees ({df.index[0]} a {df.index[-1]})")
        return df
    
    def load_h1_data(self, symbol: str, years: int = 3, use_all_available: bool = True, last_n_months: Optional[int] = None) -> Optional[pd.DataFrame]:
        """Charge les données H1 pour l'analyse de tendance supérieure"""
        if not USE_H1_TREND_FILTER:
            return None
        
        # Trouver le symbole exact
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            found = self.find_symbol_variant(symbol)
            if found:
                symbol = found
                symbol_info = mt5.symbol_info(symbol)
            else:
                return None
        
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                return None
        
        end_date = datetime.now()
        
        # Mode "derniers N mois"
        if last_n_months is not None and last_n_months > 0:
            start_date = end_date - timedelta(days=last_n_months * 30)
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_H1, start_date, end_date)
            if rates is None or len(rates) == 0:
                bars_needed = last_n_months * 30 * 24
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, bars_needed)
                if rates is not None and len(rates) > 0:
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
                    else:
                        rates = None
        elif use_all_available:
            print(f"   Mode: Récupération de TOUTES les données H1 disponibles")
            rates = None
        else:
            start_date = end_date - timedelta(days=years * 365)
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_H1, start_date, end_date)
            if rates is None or len(rates) == 0:
                bars_needed = years * 365 * 24
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, bars_needed)
                if rates is not None and len(rates) > 0:
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
        
        # Si toujours pas de données OU si use_all_available (sans last_n_months), récupérer tout
        if rates is None or len(rates) == 0:
            if not use_all_available:
                print(f"   ATTENTION: Pas de donnees H1 pour la periode demandee, recuperation de TOUTES les donnees disponibles...")
            
            # Essayer de récupérer progressivement plus de données H1
            max_attempts = [10000, 50000, 100000, 200000, 500000]  # Essayer avec de plus en plus de bougies H1
            
            best_rates = None
            best_count = 0
            
            for max_bars in max_attempts:
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, max_bars)
                
                if rates is not None and len(rates) > 0:
                    count = len(rates)
                    if count > best_count:
                        best_rates = rates
                        best_count = count
                    
                    # Si on a récupéré moins que demandé, c'est qu'on a tout récupéré
                    if count < max_bars:
                        break
            
            rates = best_rates
        
        if rates is None or len(rates) == 0:
            return None
        
        # Convertir en DataFrame
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        
        # Fenêtre "derniers N mois" si demandée
        if last_n_months is not None and last_n_months > 0 and len(df) > 0:
            cutoff = df.index[-1] - pd.Timedelta(days=last_n_months * 30)
            df = df[df.index >= cutoff].copy()
        
        return df
    
    def get_h1_data_at_time(self, symbol: str, current_time: datetime) -> Optional[pd.DataFrame]:
        """
        Récupère les données H1 jusqu'à un moment donné (pour analyse de tendance)
        
        Utilisé pour déterminer la tendance H1 au moment où on analyse un signal M5
        """
        if symbol not in self.h1_data:
            return None
        
        df_h1 = self.h1_data[symbol]
        
        # Retourner les données jusqu'à la bougie H1 qui contient le moment actuel M5
        # Trouver la dernière bougie H1 qui est <= current_time (moment du signal M5)
        h1_data_until_now = df_h1[df_h1.index <= current_time]

        # Besoin d'au moins 3 bougies H1 pour analyser la tendance
        if len(h1_data_until_now) < 3:
            return None

        return h1_data_until_now

    def check_h1_trend(self, symbol: str, current_time: datetime, trade_type: TradeType) -> bool:
        """
        Détermine la tendance sur H1 en analysant les 3 dernières bougies H1
        et vérifie si elle est alignée avec le trade M5
        
        STRATÉGIE: 
        - Analyse les 3 dernières bougies H1 pour déterminer la tendance
        - Pour LONG: les 3 dernières bougies H1 doivent être en hausse (tendance haussière)
        - Pour SHORT: les 3 dernières bougies H1 doivent être en baisse (tendance baissière)
        - M5 prend les trades seulement si alignés avec la tendance H1
        
        Retourne True si la tendance H1 est dans le même sens que le trade proposé
        """
        if not USE_H1_TREND_FILTER:
            return True
        
        df_h1 = self.get_h1_data_at_time(symbol, current_time)
        if df_h1 is None or len(df_h1) < 3:
            return False  # Besoin d'au moins 3 bougies H1 pour déterminer la tendance
        
        # Analyser les 3 dernières bougies H1
        last_3_bars = df_h1.iloc[-3:]
        prices = last_3_bars['close'].values
        
        if trade_type == TradeType.LONG:
            # Pour LONG M5: la tendance H1 doit être HAUSSIÈRE
            # Analyser les 3 dernières bougies H1
            # Tendance haussière si :
            # - La dernière bougie est >= à la première (tendance générale haussière)
            # - ET au moins 2 bougies sur 3 sont en hausse par rapport à la précédente
            price_first = prices[0]
            price_last = prices[-1]
            
            # Vérifier que la tendance générale est haussière
            if price_last < price_first:
                return False  # Tendance générale baissière
            
            # Compter les hausses entre les bougies consécutives
            rises = 0
            for i in range(1, len(prices)):
                if prices[i] > prices[i-1]:
                    rises += 1
            
            # Tendance haussière si au moins 2 hausses sur 3 bougies
            return rises >= 2
        else:  # SHORT
            # Pour SHORT M5: la tendance H1 doit être BAISSIÈRE
            # Analyser les 3 dernières bougies H1
            # Tendance baissière si :
            # - La dernière bougie est <= à la première (tendance générale baissière)
            # - ET au moins 2 bougies sur 3 sont en baisse par rapport à la précédente
            price_first = prices[0]
            price_last = prices[-1]
            
            # Vérifier que la tendance générale est baissière
            if price_last > price_first:
                return False  # Tendance générale haussière
            
            # Compter les baisses entre les bougies consécutives
            falls = 0
            for i in range(1, len(prices)):
                if prices[i] < prices[i-1]:
                    falls += 1
            
            # Tendance baissière si au moins 2 baisses sur 3 bougies
            return falls >= 2
    
    def get_market_data_at_index(self, symbol: str, current_index: int) -> Optional[pd.DataFrame]:
        """Récupère les données jusqu'à l'index actuel (pour le backtest)"""
        if symbol not in self.historical_data:
            return None
        
        df = self.historical_data[symbol]
        
        # Retourner les données jusqu'à l'index actuel (minimum 50 bougies pour SMA 50)
        if current_index < SMA_SLOW + 10:
            return None
        
        return df.iloc[:current_index + 1]
    
    # ========== FONCTIONS DE LOGIQUE (IDENTIQUES À LA PROD) ==========
    
    def check_ema_slope(self, df: pd.DataFrame) -> bool:
        """
        Vérifie si la bougie clôture au-dessus ou en-dessous de l'EMA 20
        """
        if len(df) < 1:
            return False
        
        current = df.iloc[-1]
        price_close = current['close']
        ema20 = current[f'EMA_{EMA_FAST}']
        
        # La bougie doit clôturer au-dessus OU en-dessous de l'EMA 20
        # (pas exactement sur l'EMA 20)
        return price_close != ema20
    
    def is_ema200_flat(self, df: pd.DataFrame) -> bool:
        """
        Détermine si la SMA 50 est plate (sans pente significative)
        Retourne True si plate, False si penche dans un sens
        """
        if len(df) < 2:
            return True  # Par défaut considéré comme plate si pas assez de données
        
        sma50_current = df[f'SMA_{SMA_SLOW}'].iloc[-1]
        sma50_prev = df[f'SMA_{SMA_SLOW}'].iloc[-2]
        
        slope = abs(sma50_current - sma50_prev)
        min_slope = sma50_current * SMA_SLOPE_MIN
        
        # Si la pente est inférieure au minimum, la SMA 50 est considérée comme plate
        return slope < min_slope
    
    def get_risk_reward_ratio(self, df: pd.DataFrame) -> float:
        """
        Retourne le R:R adapté selon la pente de la SMA 50
        - 1.0 (1:1) si SMA 50 est plate
        - 1.5 (1:1.5) si SMA 50 penche dans un sens
        """
        if self.is_ema200_flat(df):
            return RISK_REWARD_RATIO_FLAT
        else:
            return RISK_REWARD_RATIO_TRENDING
    
    def check_atr_filter(self, df: pd.DataFrame) -> bool:
        """Vérifie la volatilité avec ATR (anti faux signaux - éviter marchés compressés)"""
        if not USE_ATR_FILTER or 'ATR' not in df.columns:
            return True
        
        if len(df) < ATR_LOOKBACK + 1:
            return False
        
        current_atr = df['ATR'].iloc[-1]
        if pd.isna(current_atr) or current_atr <= 0:
            return True
        
        # Calculer la moyenne ATR sur les X dernières périodes
        atr_values = df['ATR'].iloc[-(ATR_LOOKBACK + 1):-1]  # Exclure la dernière
        atr_avg = atr_values.mean()
        
        # Ne pas trader si ATR < moyenne ATR (marché compressé)
        if current_atr < (atr_avg * ATR_MULTIPLIER):
            return False
        
        # Vérifier aussi que la bougie actuelle a une range suffisante
        candle_range = df['high'].iloc[-1] - df['low'].iloc[-1]
        min_range = current_atr * ATR_MULTIPLIER
        
        return candle_range >= min_range
    
    def check_trend_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Vérifie que la bougie clôture au-dessus (LONG) ou en-dessous (SHORT) de l'EMA 20"""
        if not USE_TREND_FILTER or len(df) < 1:
            return True
        
        current = df.iloc[-1]
        price_close = current['close']
        ema20 = current[f'EMA_{EMA_FAST}']
        
        if trade_type == TradeType.LONG:
            # Pour LONG: la bougie doit clôturer au-dessus de l'EMA 20
            return price_close > ema20
        else:  # SHORT
            # Pour SHORT: la bougie doit clôturer en-dessous de l'EMA 20
            return price_close < ema20
    
    def check_momentum_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Vérifie le momentum avant l'entrée (améliore le WR)"""
        if not USE_MOMENTUM_FILTER or len(df) < 3:
            return True
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        if trade_type == TradeType.LONG:
            # Pour LONG: vérifier que le prix monte avec force
            price_momentum = current['close'] - prev['close']
            return price_momentum > 0  # Prix en hausse
        else:  # SHORT
            # Pour SHORT: vérifier que le prix baisse avec force
            price_momentum = current['close'] - prev['close']
            return price_momentum < 0  # Prix en baisse
    
    def check_distance_from_ema200(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Évite les entrées trop loin de la SMA 50 (améliore WR)"""
        if not USE_DISTANCE_FILTER or len(df) < 1:
            return True
        
        current = df.iloc[-1]
        price = current['close']
        sma50 = current[f'SMA_{SMA_SLOW}']
        
        if sma50 <= 0 or pd.isna(sma50):
            return True
        
        distance_pct = abs(price - sma50) / sma50
        
        return distance_pct <= MAX_DISTANCE_FROM_EMA200
    
    def check_ema_spread(self, df: pd.DataFrame) -> bool:
        """Évite les spreads trop larges entre EMA20 et SMA50 (améliore WR)"""
        if not USE_EMA_SPREAD_FILTER or len(df) < 1:
            return True
        
        current = df.iloc[-1]
        ema20 = current[f'EMA_{EMA_FAST}']
        sma50 = current[f'SMA_{SMA_SLOW}']
        
        if sma50 <= 0 or pd.isna(sma50):
            return True
        
        spread_pct = abs(ema20 - sma50) / sma50
        
        return spread_pct <= MAX_EMA_SPREAD
    
    def check_confirmation_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Confirmation sur plusieurs bougies (améliore WR)"""
        if not USE_CONFIRMATION_FILTER or len(df) < CONFIRMATION_BARS + 1:
            return True
        
        if trade_type == TradeType.LONG:
            # Pour LONG: vérifier que les dernières bougies sont haussières
            recent_closes = df['close'].iloc[-(CONFIRMATION_BARS + 1):]
            return recent_closes.iloc[-1] > recent_closes.iloc[0]
        else:  # SHORT
            # Pour SHORT: vérifier que les dernières bougies sont baissières
            recent_closes = df['close'].iloc[-(CONFIRMATION_BARS + 1):]
            return recent_closes.iloc[-1] < recent_closes.iloc[0]
    
    def check_volatility_filter(self, df: pd.DataFrame) -> bool:
        """Évite les entrées dans volatilité excessive (améliore WR)"""
        if not USE_VOLATILITY_FILTER or 'ATR' not in df.columns or len(df) < ATR_LOOKBACK + 1:
            return True
        
        current_atr = df['ATR'].iloc[-1]
        if pd.isna(current_atr) or current_atr <= 0:
            return True
        
        # Calculer moyenne ATR
        atr_values = df['ATR'].iloc[-(ATR_LOOKBACK + 1):-1]
        atr_avg = atr_values.mean()
        
        if atr_avg <= 0:
            return True
        
        # Éviter si volatilité trop élevée (marché agité)
        return current_atr <= (atr_avg * MAX_VOLATILITY_MULTIPLIER)
    
    def find_last_low(self, symbol: str, df: pd.DataFrame, lookback: int = 10) -> float:
        """Calcule le SL pour LONG (basé sur ATR ou dernier swing valide)"""
        current_price = df['close'].iloc[-1]
        
        # PRIORITÉ: Utiliser ATR pour SL intelligent
        if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
            current_atr = df['ATR'].iloc[-1]
            if not pd.isna(current_atr) and current_atr > 0:
                # SL basé sur ATR (plus adapté à la volatilité)
                return current_price - (current_atr * ATR_SL_MULTIPLIER)
        
        # Fallback: Dernier swing bas valide
        if len(df) < lookback:
            lookback = len(df)
        
        lows = df['low'].iloc[-lookback:]
        min_low = lows.min()
        
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info:
            point = symbol_info.point
            buffer = point * 5
            return min_low - buffer
        
        return min_low * 0.999
    
    def find_last_high(self, symbol: str, df: pd.DataFrame, lookback: int = 10) -> float:
        """Calcule le SL pour SHORT (basé sur ATR ou dernier swing valide)"""
        current_price = df['close'].iloc[-1]
        
        # PRIORITÉ: Utiliser ATR pour SL intelligent
        if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
            current_atr = df['ATR'].iloc[-1]
            if not pd.isna(current_atr) and current_atr > 0:
                # SL basé sur ATR (plus adapté à la volatilité)
                return current_price + (current_atr * ATR_SL_MULTIPLIER)
        
        # Fallback: Dernier swing haut valide
        if len(df) < lookback:
            lookback = len(df)
        
        highs = df['high'].iloc[-lookback:]
        max_high = highs.max()
        
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info:
            point = symbol_info.point
            buffer = point * 5
            return max_high + buffer
        
        return max_high * 1.001
    
    def check_long_entry(self, df: pd.DataFrame, symbol: str = "", current_time: datetime = None) -> bool:
        """
        Vérifie les conditions d'entrée LONG sur M5
        
        STRATÉGIE CROISEMENT:
        1. H1 détermine la tendance principale (haussière/baissière)
        2. M5 prend les trades seulement si alignés avec la tendance H1
        3. Condition M5: EMA 20 croise au-dessus de SMA 50
        
        Retourne True si toutes les conditions sont remplies (H1 + M5)
        """
        if len(df) < 5:
            return False
        
        # Récupérer le temps actuel si non fourni
        if current_time is None:
            current_time = df.index[-1]
            if hasattr(current_time, 'to_pydatetime'):
                current_time = current_time.to_pydatetime()
        
        # FILTRE SESSION: Vérifier qu'on est dans une session de trading valide (ASIA, EUROPE, US)
        if not self.is_valid_trading_session(current_time):
            return False  # Session OFF_HOURS -> pas de trade
        
        # FILTRE 0: Tendance H1 (PRIORITÉ ABSOLUE)
        # Analyse les 3 dernières bougies H1 pour déterminer la tendance
        if USE_H1_TREND_FILTER and symbol and current_time is not None:
            if not self.check_h1_trend(symbol, current_time, TradeType.LONG):
                return False  # Tendance H1 non haussière -> pas de LONG M5
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema20_current = current[f'EMA_{EMA_FAST}']
        sma50_current = current[f'SMA_{SMA_SLOW}']
        ema20_prev = prev[f'EMA_{EMA_FAST}']
        sma50_prev = prev[f'SMA_{SMA_SLOW}']
        
        # Condition M5: EMA 20 doit croiser au-dessus de SMA 50
        # EMA 20 était en dessous de SMA 50 à la bougie précédente
        # EMA 20 est maintenant au-dessus de SMA 50
        if ema20_prev >= sma50_prev:
            return False  # Pas de croisement haussier
        
        if ema20_current <= sma50_current:
            return False  # Pas encore au-dessus après croisement
        
        return True
    
    def check_short_entry(self, df: pd.DataFrame, symbol: str = "", current_time: datetime = None) -> bool:
        """
        Vérifie les conditions d'entrée SHORT sur M5
        
        STRATÉGIE CROISEMENT:
        1. H1 détermine la tendance principale (haussière/baissière)
        2. M5 prend les trades seulement si alignés avec la tendance H1
        3. Condition M5: EMA 20 croise en-dessous de SMA 50
        
        Retourne True si toutes les conditions sont remplies (H1 + M5)
        """
        if len(df) < 5:
            return False
        
        # Récupérer le temps actuel si non fourni
        if current_time is None:
            current_time = df.index[-1]
            if hasattr(current_time, 'to_pydatetime'):
                current_time = current_time.to_pydatetime()
        
        # FILTRE SESSION: Vérifier qu'on est dans une session de trading valide (ASIA, EUROPE, US)
        if not self.is_valid_trading_session(current_time):
            return False  # Session OFF_HOURS -> pas de trade
        
        # FILTRE 0: Tendance H1 (PRIORITÉ ABSOLUE)
        # Analyse les 3 dernières bougies H1 pour déterminer la tendance
        if USE_H1_TREND_FILTER and symbol and current_time is not None:
            if not self.check_h1_trend(symbol, current_time, TradeType.SHORT):
                return False  # Tendance H1 non baissière -> pas de SHORT M5
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema20_current = current[f'EMA_{EMA_FAST}']
        sma50_current = current[f'SMA_{SMA_SLOW}']
        ema20_prev = prev[f'EMA_{EMA_FAST}']
        sma50_prev = prev[f'SMA_{SMA_SLOW}']
        
        # Condition M5: EMA 20 doit croiser en-dessous de SMA 50
        # EMA 20 était au-dessus de SMA 50 à la bougie précédente
        # EMA 20 est maintenant en-dessous de SMA 50
        if ema20_prev <= sma50_prev:
            return False  # Pas de croisement baissier
        
        if ema20_current >= sma50_current:
            return False  # Pas encore en-dessous après croisement
        
        return True
    
    def calculate_profit(self, symbol: str, entry_price: float, exit_price: float, lot_size: float, trade_type: TradeType) -> float:
        """Calcule le profit/perte d'un trade (utilise la même logique que calculate_lot_size)"""
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return 0.0
        
        # Calculer la distance en prix
        if trade_type == TradeType.LONG:
            price_diff = exit_price - entry_price
        else:  # SHORT
            price_diff = entry_price - exit_price
        
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size
        point = symbol_info.point
        
        if tick_size > 0 and tick_value > 0:
            # Méthode avec tick_value et tick_size
            ticks = price_diff / tick_size
            profit = ticks * tick_value * lot_size
        else:
            # Méthode avec contract_size
            contract_size = symbol_info.trade_contract_size
            if contract_size > 0:
                profit = (price_diff * contract_size * lot_size) / entry_price
            else:
                # Fallback avec point
                profit = price_diff * point * lot_size
        
        return profit
    
    def calculate_lot_size(self, symbol: str, entry_price: float, stop_loss: float) -> float:
        """Calcule la taille du lot selon le risque (utilise balance simulée)"""
        risk_amount = self.current_balance * (self.risk_percent / 100.0)
        
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return 0
        
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return 0
        
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size
        point = symbol_info.point
        
        if tick_size > 0 and tick_value > 0:
            ticks_in_stop = stop_distance / tick_size
            risk_per_lot = ticks_in_stop * tick_value
        else:
            contract_size = symbol_info.trade_contract_size
            if contract_size > 0:
                risk_per_lot = (stop_distance * contract_size) / entry_price
            else:
                risk_per_lot = stop_distance * point
        
        if risk_per_lot <= 0:
            return 0
        
        lot_size = risk_amount / risk_per_lot
        
        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        lot_step = symbol_info.volume_step
        
        if lot_size < min_lot:
            lot_size = min_lot
        if lot_size > max_lot:
            lot_size = max_lot
        
        if lot_step > 0:
            lot_size = (lot_size // lot_step) * lot_step
        
        return round(lot_size, 2)
    
    def has_open_position(self, symbol: str) -> bool:
        """Vérifie si une position est déjà ouverte pour ce symbole (simulée)"""
        return symbol in self.open_trades and len(self.open_trades[symbol]) > 0
    
    def get_daily_loss(self, current_date: datetime.date) -> float:
        """Calcule la perte quotidienne (adapté pour backtest)"""
        if self.last_trading_date is None or current_date > self.last_trading_date:
            self.daily_start_balance = self.current_balance
            self.trading_stopped_daily = False
            self.last_trading_date = current_date
        elif self.daily_start_balance is None:
            self.daily_start_balance = self.current_balance
            self.trading_stopped_daily = False
            self.last_trading_date = current_date
        
        daily_loss = self.current_balance - self.daily_start_balance
        return daily_loss
    
    def can_trade_today(self, current_date: datetime.date) -> Tuple[bool, str]:
        """Vérifie si on peut trader aujourd'hui"""
        if self.trading_stopped_daily:
            return False, "Trading arrêté pour la journée (limite de perte atteinte)"
        
        daily_loss = self.get_daily_loss(current_date)
        
        if daily_loss <= self.max_daily_loss:
            self.trading_stopped_daily = True
            return False, f"Limite de perte quotidienne atteinte: {daily_loss:.2f} (limite: {self.max_daily_loss:.2f})"
        
        return True, ""
    
    def has_recent_same_setup(self, symbol: str, trade_type: TradeType, current_bar_time: datetime) -> Tuple[bool, Optional[str]]:
        """Vérifie si une position du même type a été ouverte récemment"""
        key = (symbol, trade_type)
        
        if key not in self.last_trade_by_symbol_type:
            return False, None
        
        last_trade_time = self.last_trade_by_symbol_type[key]
        time_diff = current_bar_time - last_trade_time
        bars_elapsed = time_diff.total_seconds() / 300
        
        if bars_elapsed < MIN_BARS_BETWEEN_SAME_SETUP:
            remaining_bars = int(MIN_BARS_BETWEEN_SAME_SETUP - bars_elapsed)
            return True, f"Setup {trade_type.value} déjà traité il y a {int(bars_elapsed)} bougie(s). Attendre encore {remaining_bars} bougie(s)."
        
        return False, None
    
    def is_in_cooldown(self, current_bar_time: datetime) -> bool:
        """Vérifie si on est en cooldown après une perte (pas de re-entrée immédiate)"""
        if self.last_loss_time is None:
            return False
        
        time_diff = current_bar_time - self.last_loss_time
        bars_elapsed = time_diff.total_seconds() / 300
        
        return bars_elapsed < COOLDOWN_AFTER_LOSS
    
    def record_trade(self, symbol: str, trade_type: TradeType, trade_time: datetime):
        """Enregistre qu'une position a été ouverte"""
        key = (symbol, trade_type)
        self.last_trade_by_symbol_type[key] = trade_time
    
    # ========== ANALYTICS AVANCÉES ==========
    
    def get_trading_session(self, trade_time: datetime) -> TradingSession:
        """
        Détermine la session de trading basée sur l'heure UTC
        
        Sessions:
        - ASIA: 00:00 - 08:00 UTC (Tokyo, Sydney)
        - EUROPE: 08:00 - 14:00 UTC (Londres, Francfort)
        - US: 14:00 - 21:00 UTC (New York)
        - OFF_HOURS: 21:00 - 00:00 UTC (faible activité)
        """
        hour = trade_time.hour
        
        if 0 <= hour < 8:
            return TradingSession.ASIA
        elif 8 <= hour < 14:
            return TradingSession.EUROPE
        elif 14 <= hour < 21:
            return TradingSession.US
        else:  # 21-24
            return TradingSession.OFF_HOURS
    
    def is_valid_trading_session(self, trade_time: datetime) -> bool:
        """
        Vérifie si on est dans une session de trading valide (ASIA, EUROPE ou US)
        Retourne False si on est en session OFF_HOURS
        """
        session = self.get_trading_session(trade_time)
        return session != TradingSession.OFF_HOURS
    
    def get_market_condition(self, df: pd.DataFrame) -> MarketCondition:
        """
        Détermine si le marché est en Bull ou Bear
        Basé sur la position du prix par rapport à la SMA 50
        """
        if len(df) < 1:
            return MarketCondition.BULL  # Par défaut
        
        current = df.iloc[-1]
        price = current['close']
        sma50 = current[f'SMA_{SMA_SLOW}']
        
        if pd.isna(sma50):
            return MarketCondition.BULL
        
        if price >= sma50:
            return MarketCondition.BULL
        else:
            return MarketCondition.BEAR
    
    def get_market_trend(self, df: pd.DataFrame) -> Tuple[MarketTrend, float]:
        """
        Détermine si le marché est en Tendance ou en Range
        Basé sur la pente de la SMA 50
        
        Retourne: (MarketTrend, slope_value)
        """
        if len(df) < 10:
            return MarketTrend.RANGING, 0.0
        
        # Calculer la pente sur les 10 dernières bougies
        sma_values = df[f'SMA_{SMA_SLOW}'].iloc[-10:]
        
        if sma_values.isna().any():
            return MarketTrend.RANGING, 0.0
        
        sma_start = sma_values.iloc[0]
        sma_end = sma_values.iloc[-1]
        
        if sma_start <= 0:
            return MarketTrend.RANGING, 0.0
        
        # Pente en pourcentage
        slope_pct = (sma_end - sma_start) / sma_start
        
        # Seuil pour considérer comme "trending" (0.1% de mouvement sur 10 bougies)
        TRENDING_THRESHOLD = 0.001
        
        if abs(slope_pct) >= TRENDING_THRESHOLD:
            return MarketTrend.TRENDING, slope_pct
        else:
            return MarketTrend.RANGING, slope_pct
    
    def get_atr_at_entry(self, df: pd.DataFrame) -> float:
        """Récupère la valeur ATR à l'entrée"""
        if 'ATR' not in df.columns or len(df) < 1:
            return 0.0
        
        atr = df['ATR'].iloc[-1]
        return atr if not pd.isna(atr) else 0.0
    
    def classify_trade(self, trade: SimulatedTrade, df: pd.DataFrame):
        """
        Classifie un trade avec toutes les informations analytiques
        Appelé lors de l'ouverture d'un trade
        """
        trade.session = self.get_trading_session(trade.entry_time)
        trade.market_condition = self.get_market_condition(df)
        trade.market_trend, trade.sma_slope = self.get_market_trend(df)
        trade.atr_value = self.get_atr_at_entry(df)
        trade.day_of_week = trade.entry_time.weekday()  # 0=Lundi, 6=Dimanche
