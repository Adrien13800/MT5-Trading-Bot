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

# Ajouter le repertoire parent au path pour importer strategy_core
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
except ImportError:
    print("ERREUR: MetaTrader5, pandas ou numpy n'est pas installe.")
    print("   Installez-les avec: pip install MetaTrader5 pandas numpy")
    sys.exit(1)

# ============================================================================
# IMPORT STRATEGY CORE (source unique de verite pour la logique de trading)
# ============================================================================
from strategy_core import (
    # Constantes
    EMA_FAST, SMA_SLOW, RISK_REWARD_RATIO_FLAT, RISK_REWARD_RATIO_TRENDING,
    SMA_SLOPE_MIN, USE_ATR_FILTER, ATR_PERIOD, ATR_MULTIPLIER, ATR_LOOKBACK,
    USE_ATR_SL, ATR_SL_MULTIPLIER, ALLOW_LONG, ALLOW_SHORT,
    USE_TREND_FILTER, USE_MOMENTUM_FILTER, USE_DISTANCE_FILTER,
    MAX_DISTANCE_FROM_EMA200, USE_EMA_SPREAD_FILTER, MAX_EMA_SPREAD,
    USE_CONFIRMATION_FILTER, CONFIRMATION_BARS, USE_VOLATILITY_FILTER,
    MAX_VOLATILITY_MULTIPLIER, EMA_TOUCH_TOLERANCE, USE_H1_TREND_FILTER,
    # Enums
    TradeType, TradingSession, MarketCondition, MarketTrend,
    # Fonctions pures
    compute_indicators,
    get_trading_session as core_get_trading_session,
    is_valid_trading_session as core_is_valid_trading_session,
    is_sma50_flat, get_risk_reward_ratio as core_get_risk_reward_ratio,
    check_atr_filter as core_check_atr_filter,
    check_h1_trend as core_check_h1_trend,
    check_long_signal, check_short_signal,
    calculate_sl_long, calculate_sl_short, calculate_tp,
    get_h1_data_at_time as core_get_h1_data_at_time,
    check_trend_filter as core_check_trend_filter,
    check_momentum_filter as core_check_momentum_filter,
    check_distance_from_sma50, check_ema_spread as core_check_ema_spread,
    check_confirmation_filter as core_check_confirmation_filter,
    check_volatility_filter as core_check_volatility_filter,
    get_market_condition as core_get_market_condition,
    get_market_trend as core_get_market_trend,
)

# Constantes specifiques au backtest (pas dans strategy_core)
RISK_REWARD_RATIO = 1.5
MAGIC_NUMBER = 123456
TRADE_COMMENT = "EMA20_SMA50_Cross"
MIN_BARS_BETWEEN_SAME_SETUP = 0
COOLDOWN_AFTER_LOSS = 0
REQUIRE_IMPULSE_BREAK = False
REQUIRE_REJECTION = False
USE_ACTIVE_SESSIONS = False

# Timeframe MT5
TIMEFRAME_MT5 = mt5.TIMEFRAME_M5
TIMEFRAME_H1 = mt5.TIMEFRAME_H1


# TradeType, TradingSession, MarketCondition, MarketTrend importes depuis strategy_core


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
            
            # Calculer les indicateurs via strategy_core
            compute_indicators(df)

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
            years: Nombre d'années demandées (utilisé si use_all_available=False et last_n_months non utilisé)
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
        
        # Si use_all_available (et pas last_n_months), on récupère directement toutes les données
        if use_all_available and not (last_n_months is not None and last_n_months > 0):
            print(f"   Mode: Récupération de TOUTES les données disponibles (pas de limite)")
            rates = None
        else:
            # Calculer la date de début (années ou derniers N mois)
            end_date = datetime.now()
            if last_n_months is not None and last_n_months > 0:
                start_date = end_date - timedelta(days=last_n_months * 30)  # ~30 jours par mois
            else:
                start_date = end_date - timedelta(days=years * 365)
            
            print(f"   Periode demandee: {start_date.strftime('%Y-%m-%d')} a {end_date.strftime('%Y-%m-%d')}")
            
            # Récupérer les données (M5 = 5 minutes)
            # Essayer d'abord avec copy_rates_range
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_MT5, start_date, end_date)
            
            # Si ça ne fonctionne pas, essayer avec copy_rates_from_pos (récupère depuis le début)
            if rates is None or len(rates) == 0:
                print(f"   ATTENTION: copy_rates_range n'a pas fonctionne, tentative avec copy_rates_from_pos...")
                # Calculer approximativement le nombre de bougies nécessaires (288 bougies M5 par jour)
                days_requested = (last_n_months * 30) if (last_n_months is not None and last_n_months > 0) else (years * 365)
                bars_needed = int(days_requested * 288)  # 288 bougies M5 par jour (24h * 60min / 5min)
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, bars_needed)
                
                if rates is not None and len(rates) > 0:
                    # Filtrer les données pour ne garder que celles dans la période demandée
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
                        print(f"   OK {len(rates)} bougies recuperees avec copy_rates_from_pos")
                    else:
                        rates = None
        
        # Si toujours pas de données OU si use_all_available, récupérer TOUTES les données disponibles
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
        
        # Calculer les indicateurs via strategy_core
        compute_indicators(df)

        print(f"   OK {len(df)} bougies chargees ({df.index[0]} a {df.index[-1]})")
        return df
    
    def load_h1_data(self, symbol: str, years: int = 3, use_all_available: bool = True, last_n_months: Optional[int] = None) -> Optional[pd.DataFrame]:
        """Charge les données H1 pour l'analyse de tendance supérieure.
        last_n_months: si > 0, charge uniquement les N derniers mois (prioritaire sur years)."""
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
        
        if use_all_available and not (last_n_months is not None and last_n_months > 0):
            # Récupérer TOUTES les données H1 disponibles
            print(f"   Mode: Récupération de TOUTES les données H1 disponibles")
            rates = None
        else:
            # Calculer la date de début (années ou derniers N mois)
            end_date = datetime.now()
            if last_n_months is not None and last_n_months > 0:
                start_date = end_date - timedelta(days=last_n_months * 30)
            else:
                start_date = end_date - timedelta(days=years * 365)
            
            # Récupérer les données H1
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_H1, start_date, end_date)
            
            if rates is None or len(rates) == 0:
                # Essayer avec copy_rates_from_pos
                days_requested = (last_n_months * 30) if (last_n_months is not None and last_n_months > 0) else (years * 365)
                bars_needed = int(days_requested * 24)  # 24 bougies H1 par jour
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, bars_needed)
                
                if rates is not None and len(rates) > 0:
                    df_temp = pd.DataFrame(rates)
                    df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                    df_temp = df_temp[df_temp['time'] >= start_date]
                    df_temp = df_temp[df_temp['time'] <= end_date]
                    
                    if len(df_temp) > 0:
                        rates = df_temp.to_records(index=False)
        
        # Si toujours pas de données OU si use_all_available, récupérer TOUTES les données disponibles
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
        
        # Les EMA ne sont utilisées QUE sur M5, pas sur H1
        # On détermine la tendance H1 uniquement avec le prix (pas d'EMA)
        
        return df
    
    def get_h1_data_at_time(self, symbol: str, current_time: datetime) -> Optional[pd.DataFrame]:
        """Recupere les donnees H1 filtrees via strategy_core.get_h1_data_at_time."""
        if symbol not in self.h1_data:
            return None
        return core_get_h1_data_at_time(self.h1_data[symbol], current_time)

    def check_h1_trend(self, symbol: str, current_time: datetime, trade_type: TradeType) -> bool:
        """Delegue a strategy_core.check_h1_trend."""
        if not USE_H1_TREND_FILTER:
            return True
        df_h1 = self.get_h1_data_at_time(symbol, current_time)
        return core_check_h1_trend(df_h1, trade_type)
    
    def get_market_data_at_index(self, symbol: str, current_index: int) -> Optional[pd.DataFrame]:
        """Récupère les données jusqu'à l'index actuel (pour le backtest)"""
        if symbol not in self.historical_data:
            return None
        
        df = self.historical_data[symbol]
        
        # Retourner les données jusqu'à l'index actuel (minimum 50 bougies pour SMA 50)
        if current_index < SMA_SLOW + 10:
            return None
        
        return df.iloc[:current_index + 1]
    
    # ========== FONCTIONS DE LOGIQUE (DELEGUEES A STRATEGY_CORE) ==========

    def check_ema_slope(self, df: pd.DataFrame) -> bool:
        if len(df) < 1:
            return False
        current = df.iloc[-1]
        return current['close'] != current[f'EMA_{EMA_FAST}']

    def is_ema200_flat(self, df: pd.DataFrame) -> bool:
        return is_sma50_flat(df)

    def get_risk_reward_ratio(self, df: pd.DataFrame) -> float:
        return core_get_risk_reward_ratio(df)

    def check_atr_filter(self, df: pd.DataFrame) -> bool:
        return core_check_atr_filter(df)

    def check_trend_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        return core_check_trend_filter(df, trade_type)

    def check_momentum_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        return core_check_momentum_filter(df, trade_type)

    def check_distance_from_ema200(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        return check_distance_from_sma50(df, trade_type)

    def check_ema_spread(self, df: pd.DataFrame) -> bool:
        return core_check_ema_spread(df)

    def check_confirmation_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        return core_check_confirmation_filter(df, trade_type)

    def check_volatility_filter(self, df: pd.DataFrame) -> bool:
        return core_check_volatility_filter(df)

    def find_last_low(self, symbol: str, df: pd.DataFrame, lookback: int = 10) -> float:
        return calculate_sl_long(df, lookback)

    def find_last_high(self, symbol: str, df: pd.DataFrame, lookback: int = 10) -> float:
        return calculate_sl_short(df, lookback)
    
    def check_long_entry(self, df: pd.DataFrame, symbol: str = "", current_time: datetime = None) -> bool:
        """Delegue a strategy_core.check_long_signal."""
        df_h1 = None
        if USE_H1_TREND_FILTER and symbol:
            if current_time is None:
                current_time = df.index[-1]
                if hasattr(current_time, 'to_pydatetime'):
                    current_time = current_time.to_pydatetime()
            df_h1 = self.get_h1_data_at_time(symbol, current_time)
        return check_long_signal(df, df_h1, symbol)

    def check_short_entry(self, df: pd.DataFrame, symbol: str = "", current_time: datetime = None) -> bool:
        """Delegue a strategy_core.check_short_signal."""
        df_h1 = None
        if USE_H1_TREND_FILTER and symbol:
            if current_time is None:
                current_time = df.index[-1]
                if hasattr(current_time, 'to_pydatetime'):
                    current_time = current_time.to_pydatetime()
            df_h1 = self.get_h1_data_at_time(symbol, current_time)
        return check_short_signal(df, df_h1, symbol)
    
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
        return core_get_trading_session(trade_time)

    def is_valid_trading_session(self, trade_time: datetime) -> bool:
        return core_is_valid_trading_session(trade_time)

    def get_market_condition(self, df: pd.DataFrame) -> MarketCondition:
        return core_get_market_condition(df)

    def get_market_trend(self, df: pd.DataFrame) -> Tuple[MarketTrend, float]:
        """Retourne (MarketTrend, slope_value) - slope calcul specifique au backtest."""
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
