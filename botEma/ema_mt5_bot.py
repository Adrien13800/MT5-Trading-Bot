#!/usr/bin/env python3
"""
Bot de Trading Automatique MT5 - Stratégie EMA 20 / SMA 50 (Croisement)
Entrée en position au croisement EMA 20 / SMA 50
Se connecte à MetaTrader 5 et prend des positions réelles
Timeframe: 5 minutes (M5 pour trades, H1 pour tendance)
Actifs: US30 (Dow Jones) et NAS100 (Nasdaq)
"""

import sys
import time
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
except ImportError:
    print("❌ Erreur: MetaTrader5, pandas ou numpy n'est pas installé.")
    print("   Installez-les avec: pip install MetaTrader5 pandas numpy")
    sys.exit(1)

# Configuration EMA/SMA (IDENTIQUE AU BACKTEST)
EMA_FAST = 20   # EMA rapide (20)
SMA_SLOW = 50   # SMA lente (50) - remplace EMA 200
RISK_REWARD_RATIO = 1.5  # R:R par défaut (sera adapté selon pente SMA 50)
RISK_REWARD_RATIO_FLAT = 1.0  # R:R 1:1 quand SMA 50 est plate
RISK_REWARD_RATIO_TRENDING = 1.5  # R:R 1:1.5 quand SMA 50 penche

# Filtres OPTIMISÉS pour plus de trades ET meilleur WR (IDENTIQUE AU BACKTEST)
SMA_SLOPE_MIN = 0.00003  # Pente minimale SMA 50 pour considérer qu'elle "penche" (sinon plate)
USE_ATR_FILTER = True
ATR_PERIOD = 14
ATR_MULTIPLIER = 0.5  # Volatilité minimale réduite (0.5 pour plus de trades) - ALIGNÉ AVEC BACKTEST
ATR_LOOKBACK = 20  # Périodes pour moyenne ATR

# Trading
ALLOW_LONG = True
ALLOW_SHORT = True
MAGIC_NUMBER_DEFAULT = 123456
TRADE_COMMENT_DEFAULT = "EMA20_SMA50_Cross"

# Protection contre le sur-trading (aligné backtest : 0 = pas de restriction)
MIN_BARS_BETWEEN_SAME_SETUP = 0
COOLDOWN_AFTER_LOSS = 0

# Filtres pour plus de trades
USE_TREND_FILTER = True  # Filtre de tendance (désactivé pour stratégie croisement)
USE_MOMENTUM_FILTER = False  # DÉSACTIVÉ pour plus de trades
EMA_TOUCH_TOLERANCE = 0.01  # Augmenté à 1% pour plus de détections (pullback plus permissif) - ALIGNÉ AVEC BACKTEST
USE_ATR_SL = True  # Utiliser ATR pour le SL (plus intelligent)
ATR_SL_MULTIPLIER = 1.5  # Multiplicateur ATR pour le SL
REQUIRE_IMPULSE_BREAK = False  # DÉSACTIVÉ
REQUIRE_REJECTION = False  # DÉSACTIVÉ
USE_ACTIVE_SESSIONS = False  # Optionnel: trader uniquement sessions actives

# NOUVEAUX FILTRES INTELLIGENTS pour améliorer le WR (ciblent les mauvais trades)
# ASSOUPLIS pour augmenter le nombre de trades (IDENTIQUE AU BACKTEST)
USE_DISTANCE_FILTER = False  # DÉSACTIVÉ - Trop restrictif (max 2% était trop strict)
MAX_DISTANCE_FROM_EMA200 = 0.05  # Augmenté à 5% si réactivé
USE_EMA_SPREAD_FILTER = False  # DÉSACTIVÉ - Trop restrictif pour augmenter les trades
MAX_EMA_SPREAD = 0.10  # Augmenté à 10% si réactivé
USE_CONFIRMATION_FILTER = False  # DÉSACTIVÉ - Réduit trop les trades - ALIGNÉ AVEC BACKTEST
CONFIRMATION_BARS = 1  # Réduit à 1 bougie si réactivé - ALIGNÉ AVEC BACKTEST
USE_VOLATILITY_FILTER = False  # DÉSACTIVÉ - Trop restrictif - ALIGNÉ AVEC BACKTEST
MAX_VOLATILITY_MULTIPLIER = 3.0  # Augmenté à 3.0x si réactivé - ALIGNÉ AVEC BACKTEST

# Timeframe MT5 (M5 = 5 minutes pour les trades, H1 = 1 heure pour la tendance)
TIMEFRAME_MT5 = mt5.TIMEFRAME_M5
TIMEFRAME_H1 = mt5.TIMEFRAME_H1  # Pour l'analyse de tendance supérieure

# Filtre de tendance H1 (ne trader que dans le sens de la tendance H1)
USE_H1_TREND_FILTER = True  # ACTIVÉ - Tendance déterminée par les 3 dernières bougies H1

# Stratégie "un actif par jour" (WR du jour) + un seul actif à la fois
USE_DAILY_PREFERRED_SYMBOL = True   # Trader uniquement l'actif avec le meilleur WR ce jour-là
ONE_SYMBOL_AT_A_TIME = True        # Ne jamais avoir 2 actifs en position simultanément
# Parité backtest: prix d'entrée de référence = open de la bougie suivante
USE_NEXT_BAR_OPEN_FOR_ENTRY = True

# Symboles MT5 (à adapter selon votre broker)
SYMBOLS_MT5 = {
    "US30": "US30",  # Dow Jones - peut être US30, US30Cash, etc.
    "NAS100": "NAS100"  # Nasdaq - peut être NAS100, NAS100Cash, etc.
}


class TradeType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingSession(Enum):
    """Sessions de trading"""
    ASIA = "ASIA"        # 00:00 - 08:00 UTC
    EUROPE = "EUROPE"    # 08:00 - 14:00 UTC
    US = "US"            # 14:00 - 21:00 UTC
    OFF_HOURS = "OFF"    # 21:00 - 00:00 UTC


@dataclass
class Trade:
    symbol: str
    type: TradeType
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    ticket: int = 0
    timestamp: datetime = None
    status: str = "OPEN"
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class TradingSessionLogger:
    """Gère le logging de la session de trading dans un fichier .txt"""
    
    def __init__(self, file_prefix: str = "trading_session_"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = f"{file_prefix}{timestamp}.txt"
        self.log_file_handle = None
        self.original_print = print
        
    def start(self):
        """Démarre le logging"""
        try:
            self.log_file_handle = open(self.log_file, 'w', encoding='utf-8')
            # Écrire directement dans le fichier (sans passer par log() pour éviter la récursion)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            header = f"\n{'='*70}\n[SESSION DE TRADING DEMARREE]\n   Date: {timestamp}\n   Fichier log: {self.log_file}\n{'='*70}\n"
            self.log_file_handle.write(header)
            self.log_file_handle.flush()
            # Afficher dans la console (sans emojis pour éviter erreur encodage)
            try:
                self.original_print(header.replace('[SESSION DE TRADING DEMARREE]', 'SESSION DE TRADING DEMARREE'))
            except:
                self.original_print(header)
        except Exception as e:
            try:
                self.original_print(f"Erreur creation fichier log: {e}")
            except:
                pass
    
    def log(self, message: str):
        """Écrit dans le fichier ET dans la console"""
        # Afficher dans la console
        try:
            self.original_print(message)
        except UnicodeEncodeError:
            # Si erreur d'encodage, essayer sans emojis
            try:
                safe_message = message.encode('ascii', 'ignore').decode('ascii')
                self.original_print(safe_message)
            except:
                pass
        
        # Écrire dans le fichier (toujours en UTF-8)
        if self.log_file_handle:
            try:
                # Ajouter timestamp pour chaque ligne
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.log_file_handle.write(f"[{timestamp}] {message}\n")
                self.log_file_handle.flush()  # Forcer l'écriture immédiate
            except Exception as e:
                try:
                    self.original_print(f"Erreur ecriture log: {e}")
                except:
                    pass
    
    def close(self):
        """Ferme le fichier de log"""
        if self.log_file_handle:
            try:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                footer = f"\n{'='*70}\n[SESSION DE TRADING TERMINEE]\n   Date: {timestamp}\n   Fichier log: {self.log_file}\n{'='*70}\n"
                self.log_file_handle.write(footer)
                self.log_file_handle.flush()
                self.log_file_handle.close()
                # Afficher dans la console
                try:
                    self.original_print(footer.replace('[SESSION DE TRADING TERMINEE]', 'SESSION DE TRADING TERMINEE'))
                except:
                    self.original_print(footer)
            except Exception as e:
                try:
                    self.original_print(f"Erreur fermeture log: {e}")
                except:
                    pass


class MT5TradingBot:
    """Bot de trading automatique MT5 basé sur EMA 20 / SMA 50 (Croisement)"""
    
    def __init__(self, login: int, password: str, server: str, 
                 symbols: List[str], risk_percent: float = 1.0, max_daily_loss: float = -250.0,
                 magic_number: int = MAGIC_NUMBER_DEFAULT,
                 trade_comment: str = TRADE_COMMENT_DEFAULT,
                 mt5_terminal_path: str = None,
                 account_name: str = None,
                 preferred_symbol_by_day: Dict[int, str] = None,
                 use_daily_preferred_symbol: bool = None,
                 one_symbol_at_a_time: bool = None,
                 use_next_bar_open_for_entry: bool = None):
        self.login = login
        self.password = password
        self.server = server
        self.symbols = symbols
        self.risk_percent = risk_percent
        self.max_daily_loss = max_daily_loss
        self.magic_number = magic_number
        self.trade_comment = trade_comment
        self.mt5_terminal_path = mt5_terminal_path
        self.account_name = account_name or server
        self.open_trades: Dict[str, List[Trade]] = {}
        self.trade_history: List[Trade] = []
        self.last_bar_time: Dict[str, datetime] = {}
        self.daily_start_equity: Optional[float] = None
        self.daily_start_balance: Optional[float] = None
        self.trading_stopped_daily: bool = False
        self.last_trading_date: Optional[datetime.date] = None
        self.last_trade_by_symbol_type: Dict[Tuple[str, TradeType], datetime] = {}
        self.last_loss_time: Optional[datetime] = None
        self.h1_data: Dict[str, pd.DataFrame] = {}
        self.last_h1_reload_hour: Dict[str, int] = {}
        self.last_run_weekday: Optional[int] = None
        
        # Config options passées directement (prioritaires sur config.py / globales)
        self._init_preferred_symbol_by_day = preferred_symbol_by_day
        self._init_use_daily_preferred_symbol = use_daily_preferred_symbol
        self._init_one_symbol_at_a_time = one_symbol_at_a_time
        self._init_use_next_bar_open_for_entry = use_next_bar_open_for_entry
        
        # ===== LOGGING DE SESSION =====
        log_prefix = f"trading_session_{self.account_name}_" if self.account_name else "trading_session_"
        self.session_logger = TradingSessionLogger(file_prefix=log_prefix)
        self.session_logger.start()
        
        # ===== TRACKING DES ÉCHECS DE TRADES =====
        self.failed_trade_attempts: int = 0
        suffix = f"_{self.account_name}" if self.account_name else ""
        self.failed_trade_log_file = f"failed_trades_log{suffix}.json"
        self._init_failed_trade_logger()
        
        self.log("=" * 70)
        self.log(f"🤖 EMA TRADING BOT MT5 - Initialisation [{self.account_name}]")
        self.log("=" * 70)
        
        mt5_path = self.mt5_terminal_path or r"C:\Program Files\MetaTrader 5\terminal64.exe"
        if not self.mt5_terminal_path:
            try:
                import config as _cfg
                if getattr(_cfg, "MT5_TERMINAL_PATH", None):
                    mt5_path = _cfg.MT5_TERMINAL_PATH
            except ImportError:
                pass
        if not mt5.initialize(path=mt5_path):
            error = mt5.last_error()
            self.log(f"❌ Erreur initialisation MT5: {error}")
            
            # Messages d'aide selon le type d'erreur
            if error[0] == -6:
                self.log("\n⚠️  ERREUR D'AUTORISATION MT5")
                self.log("   Solutions possibles:")
                self.log("   1. Ouvrez MetaTrader 5 manuellement")
                self.log("   2. Allez dans Outils > Options > Expert Advisors")
                self.log("   3. Cochez 'Autoriser le trading algorithmique'")
                self.log("   4. Cochez 'Autoriser l'importation de DLL'")
                self.log("   5. Redémarrez MetaTrader 5")
                self.log("   6. Relancez le bot")
            elif error[0] == -1:
                self.log("\n⚠️  MT5 N'EST PAS INSTALLÉ OU NON TROUVÉ")
                self.log("   Installez MetaTrader 5 depuis: https://www.metatrader5.com/")
            
            sys.exit(1)
        
        self.log("✅ MT5 initialisé")
        
        # Se connecter au compte
        if not self.connect():
            self.log("❌ Échec de la connexion MT5")
            sys.exit(1)
        
        # Afficher les infos du compte
        account_info = mt5.account_info()
        if account_info:
            self.log(f"✅ Connecté au compte: {account_info.login}")
            self.log(f"   Serveur: {account_info.server}")
            self.log(f"   Balance: {account_info.balance:.2f} {account_info.currency}")
            self.log(f"   Equity: {account_info.equity:.2f} {account_info.currency}")
        
        # Vérifier et corriger les noms de symboles
        self.log(f"📊 Symboles configurés: {', '.join(symbols)}")
        self.log("🔍 Vérification des symboles dans MT5...")
        validated_symbols = []
        for sym in symbols:
            symbol_info = mt5.symbol_info(sym)
            if symbol_info is None:
                found = self.find_symbol_variant(sym)
                if found:
                    self.log(f"   ✅ '{sym}' → '{found}'")
                    validated_symbols.append(found)
                else:
                    self.log(f"   ❌ '{sym}' non trouvé - sera ignoré")
            else:
                self.log(f"   ✅ '{sym}' trouvé")
                validated_symbols.append(sym)
        
        if not validated_symbols:
            self.log("❌ Aucun symbole valide trouvé!")
            sys.exit(1)
        
        self.symbols = validated_symbols
        self.log(f"✅ Symboles validés: {', '.join(self.symbols)}")
        self.log(f"⏱️  Timeframe: M5 (5 minutes)")
        self.log(f"📈 EMA Fast: {EMA_FAST}, SMA Slow: {SMA_SLOW}")
        self.log(f"💰 Risque par trade: {risk_percent}%")
        self.log(f"📊 R:R adaptatif: 1:{RISK_REWARD_RATIO_FLAT} (SMA50 plate) / 1:{RISK_REWARD_RATIO_TRENDING} (SMA50 penche)")
        self.log(f"🛡️  Protection quotidienne: {max_daily_loss:.2f} {account_info.currency if account_info else 'USD'}")
        self.log(f"🔧 Filtres: ATR: {'✅' if USE_ATR_FILTER else '❌'}, H1 Trend: {'✅' if USE_H1_TREND_FILTER else '❌'}")
        self.log(f"📈 Trading: LONG: {'✅' if ALLOW_LONG else '❌'}, SHORT: {'✅' if ALLOW_SHORT else '❌'}")
        
        # Charger les données H1 pour l'analyse de tendance supérieure
        if USE_H1_TREND_FILTER:
            self.log("\n📊 Chargement des données H1 (analyse de tendance)...")
            for symbol in self.symbols:
                df_h1 = self.load_h1_data(symbol)
                if df_h1 is not None:
                    self.h1_data[symbol] = df_h1
                    self.log(f"   ✅ {symbol}: {len(df_h1)} bougies H1 chargées")
                else:
                    self.log(f"   ❌ ERREUR: Impossible de charger les données H1 pour {symbol}")
                    self.log(f"   ❌ Les trades pour {symbol} seront BLOQUÉS tant que les données H1 ne sont pas disponibles")
                    self.log(f"   ❌ Le filtre H1 est OBLIGATOIRE pour la stratégie")
        
        # Charger l'actif préféré par jour : priorité aux valeurs passées au constructeur, sinon config.py
        self.use_daily_preferred_symbol = self._init_use_daily_preferred_symbol if self._init_use_daily_preferred_symbol is not None else USE_DAILY_PREFERRED_SYMBOL
        self.one_symbol_at_a_time = self._init_one_symbol_at_a_time if self._init_one_symbol_at_a_time is not None else ONE_SYMBOL_AT_A_TIME
        self.use_next_bar_open_for_entry = self._init_use_next_bar_open_for_entry if self._init_use_next_bar_open_for_entry is not None else USE_NEXT_BAR_OPEN_FOR_ENTRY
        self.preferred_by_weekday: Dict[int, str] = {}
        if self._init_preferred_symbol_by_day is not None:
            self.preferred_by_weekday = {int(k): v for k, v in self._init_preferred_symbol_by_day.items()}
            self.log(f"   📅 Actif du jour: chargé depuis config ({len(self.preferred_by_weekday)} jours) [{self.account_name}]")
        else:
            try:
                import config as bot_config
                self.use_daily_preferred_symbol = getattr(bot_config, 'USE_DAILY_PREFERRED_SYMBOL', self.use_daily_preferred_symbol)
                self.one_symbol_at_a_time = getattr(bot_config, 'ONE_SYMBOL_AT_A_TIME', self.one_symbol_at_a_time)
                self.use_next_bar_open_for_entry = getattr(bot_config, 'USE_NEXT_BAR_OPEN_FOR_ENTRY', self.use_next_bar_open_for_entry)
                pref = getattr(bot_config, 'PREFERRED_SYMBOL_BY_DAY', None)
                if pref:
                    self.preferred_by_weekday = {int(k): v for k, v in pref.items()}
                    self.log(f"   📅 Actif du jour: chargé depuis config.py ({len(self.preferred_by_weekday)} jours)")
            except ImportError:
                self.log(f"   ⚠️  config.py non trouvé - actif du jour désactivé ou valeurs par défaut")
        
        self.log("=" * 70)
        
        # Initialiser l'equity de début de journée (FTMO utilise equity, pas balance)
        if account_info:
            self.daily_start_equity = account_info.equity
    
    def get_preferred_symbol_for_today(self) -> Optional[str]:
        """Retourne l'actif à privilégier aujourd'hui (config PREFERRED_SYMBOL_BY_DAY). None = pas de préférence."""
        if not self.use_daily_preferred_symbol or not self.preferred_by_weekday:
            return None
        weekday = datetime.now().weekday()  # 0=Lundi, 4=Vendredi, 5=Sam, 6=Dim
        preferred = self.preferred_by_weekday.get(weekday)
        if preferred and preferred in self.symbols:
            return preferred
        return None
    
    def has_open_position_on_other_symbol(self, current_symbol: str) -> bool:
        """True si une position est ouverte sur un autre actif que current_symbol (un seul actif à la fois)."""
        if not self.one_symbol_at_a_time:
            return False
        all_pos = mt5.positions_get()
        if not all_pos:
            return False
        our_pos = [p for p in all_pos if getattr(p, 'magic', None) == self.magic_number]
        symbols_with_pos = {p.symbol for p in our_pos}
        return bool(symbols_with_pos and (symbols_with_pos - {current_symbol}))
    
    def log(self, message: str):
        """Méthode de logging qui écrit dans la console ET dans le fichier"""
        self.session_logger.log(message)
    
    def connect(self) -> bool:
        """Se connecte au compte MT5"""
        authorized = mt5.login(
            login=self.login,
            password=self.password,
            server=self.server
        )
        
        if not authorized:
            self.log(f"❌ Échec connexion MT5: {mt5.last_error()}")
            return False
        
        return True
    
    # ===== MÉTHODES DE TRACKING DES ÉCHECS DE TRADES =====
    
    def _init_failed_trade_logger(self):
        """Initialise le système de logging des échecs de trades"""
        # Charger le compteur existant depuis le fichier si disponible
        if os.path.exists(self.failed_trade_log_file):
            try:
                with open(self.failed_trade_log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.failed_trade_attempts = data.get('total_failed_attempts', 0)
                    print(f"📋 Échecs de trades précédents chargés: {self.failed_trade_attempts}")
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  Impossible de charger le log des échecs: {e}")
                self.failed_trade_attempts = 0
        else:
            # Créer le fichier initial
            self._save_failed_trade_log()
    
    def _save_failed_trade_log(self):
        """Sauvegarde le log des échecs de trades dans un fichier JSON"""
        try:
            # Charger les données existantes pour préserver l'historique
            existing_data = {'total_failed_attempts': 0, 'failed_trades': []}
            if os.path.exists(self.failed_trade_log_file):
                try:
                    with open(self.failed_trade_log_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass
            
            # Mettre à jour le compteur total
            existing_data['total_failed_attempts'] = self.failed_trade_attempts
            existing_data['last_updated'] = datetime.now().isoformat()
            
            with open(self.failed_trade_log_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"⚠️  Impossible de sauvegarder le log des échecs: {e}")
    
    def log_failed_trade_attempt(self, symbol: str, trade_type: str, reason: str, 
                                  error_code: Optional[int] = None, error_message: Optional[str] = None):
        """
        Enregistre une tentative de trade échouée à cause d'un bug/erreur
        
        Args:
            symbol: Symbole concerné (ex: US30, NAS100)
            trade_type: Type de trade (LONG ou SHORT)
            reason: Raison de l'échec
            error_code: Code d'erreur MT5 si disponible
            error_message: Message d'erreur détaillé si disponible
        """
        self.failed_trade_attempts += 1
        
        # Créer l'entrée de log
        failed_trade_entry = {
            'timestamp': datetime.now().isoformat(),
            'iteration': getattr(self, '_current_iteration', 0),
            'symbol': symbol,
            'trade_type': trade_type,
            'reason': reason,
            'error_code': error_code,
            'error_message': error_message
        }
        
        # Charger et mettre à jour le fichier de log
        try:
            existing_data = {'total_failed_attempts': 0, 'failed_trades': []}
            if os.path.exists(self.failed_trade_log_file):
                try:
                    with open(self.failed_trade_log_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass
            
            # Ajouter la nouvelle entrée
            if 'failed_trades' not in existing_data:
                existing_data['failed_trades'] = []
            existing_data['failed_trades'].append(failed_trade_entry)
            existing_data['total_failed_attempts'] = self.failed_trade_attempts
            existing_data['last_updated'] = datetime.now().isoformat()
            
            # Sauvegarder
            with open(self.failed_trade_log_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
                
        except IOError as e:
            print(f"⚠️  Impossible de sauvegarder l'échec de trade: {e}")
        
        # Afficher dans la console
        print(f"\n🚨 ÉCHEC TRADE #{self.failed_trade_attempts}")
        print(f"   Symbole: {symbol}")
        print(f"   Type: {trade_type}")
        print(f"   Raison: {reason}")
        if error_code is not None:
            print(f"   Code erreur: {error_code}")
        if error_message:
            print(f"   Message: {error_message}")
        print(f"   Timestamp: {failed_trade_entry['timestamp']}")
    
    def get_failed_trades_summary(self) -> str:
        """Retourne un résumé des échecs de trades"""
        summary = f"🚨 Total échecs de trades: {self.failed_trade_attempts}"
        
        if os.path.exists(self.failed_trade_log_file):
            try:
                with open(self.failed_trade_log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    failed_trades = data.get('failed_trades', [])
                    
                    if failed_trades:
                        # Compter par raison
                        reasons = {}
                        for trade in failed_trades:
                            reason = trade.get('reason', 'Unknown')
                            reasons[reason] = reasons.get(reason, 0) + 1
                        
                        summary += "\n   Par raison:"
                        for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
                            summary += f"\n   - {reason}: {count}"
                        
                        # Derniers échecs
                        last_5 = failed_trades[-5:]
                        if last_5:
                            summary += "\n   Derniers échecs:"
                            for trade in reversed(last_5):
                                ts = trade.get('timestamp', 'N/A')[:19]
                                sym = trade.get('symbol', 'N/A')
                                tt = trade.get('trade_type', 'N/A')
                                summary += f"\n   - [{ts}] {sym} {tt}"
            except (json.JSONDecodeError, IOError):
                pass
        
        return summary
    
    def reset_failed_trades_counter(self):
        """Réinitialise le compteur d'échecs (par exemple au début d'une nouvelle journée)"""
        self.failed_trade_attempts = 0
        # Archiver les anciennes données
        if os.path.exists(self.failed_trade_log_file):
            archive_name = f"failed_trades_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            try:
                os.rename(self.failed_trade_log_file, archive_name)
                print(f"📋 Ancien log archivé: {archive_name}")
            except IOError:
                pass
        self._save_failed_trade_log()
        print("🔄 Compteur d'échecs de trades réinitialisé")
    
    def check_connection(self) -> bool:
        """Vérifie si la connexion MT5 est toujours active et reconnecte si nécessaire"""
        account_info = mt5.account_info()
        if account_info is None:
            self.log("⚠️  Connexion MT5 perdue, tentative de reconnexion...")
            if not self.connect():
                self.log("❌ Échec de reconnexion")
                return False
            self.log("✅ Reconnexion réussie")
        return True
    
    def find_symbol_variant(self, symbol_base: str) -> Optional[str]:
        """Trouve la variante exacte d'un symbole dans MT5"""
        # Variantes possibles à essayer
        variants = [
            symbol_base,  # Nom exact tel quel
            symbol_base.upper(),  # Tout en majuscules
            symbol_base.lower(),  # Tout en minuscules
            symbol_base.capitalize(),  # Première lettre majuscule
            symbol_base.replace('.', ''),  # Sans point
            symbol_base.replace('.', '_'),  # Point remplacé par underscore
            symbol_base.replace('Cash', 'cash'),  # Cash en minuscule
            symbol_base.replace('cash', 'Cash'),  # Cash en majuscule
            symbol_base.replace('.Cash', '.cash'),  # .Cash en .cash
            symbol_base.replace('.cash', '.Cash'),  # .cash en .Cash
        ]
        
        # Essayer chaque variante
        for variant in variants:
            symbol_info = mt5.symbol_info(variant)
            if symbol_info is not None:
                return variant
        
        # Si aucune variante ne fonctionne, chercher dans tous les symboles disponibles
        print(f"   🔍 Recherche de symboles similaires à '{symbol_base}'...")
        all_symbols = mt5.symbols_get()
        if all_symbols:
            matching = []
            symbol_upper = symbol_base.upper()
            for sym in all_symbols:
                sym_name = sym.name
                if symbol_upper in sym_name.upper() or sym_name.upper() in symbol_upper:
                    matching.append(sym_name)
            
            if matching:
                print(f"   💡 Symboles similaires trouvés: {', '.join(matching[:5])}")
                # Retourner le premier match
                return matching[0]
        
        return None
    
    def get_market_data(self, symbol: str, count: int = 300) -> Optional[pd.DataFrame]:
        """Récupère les données de marché depuis MT5"""
        try:
            # Vérifier que le symbole existe
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                # Essayer de trouver une variante
                found_symbol = self.find_symbol_variant(symbol)
                if found_symbol and found_symbol != symbol:
                    print(f"   ✅ Symbole trouvé: '{found_symbol}' (au lieu de '{symbol}')")
                    symbol = found_symbol
                    symbol_info = mt5.symbol_info(symbol)
                else:
                    print(f"   ❌ Symbole {symbol} non trouvé dans MT5")
                    return None
            
            # S'assurer que le symbole est visible
            if not symbol_info.visible:
                if not mt5.symbol_select(symbol, True):
                    print(f"   ❌ Impossible d'activer le symbole {symbol}")
                    return None
            
            # Récupérer les données historiques
            rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MT5, 0, count)
            
            if rates is None or len(rates) == 0:
                print(f"   ❌ Aucune donnée pour {symbol}")
                return None
            
            # Convertir en DataFrame
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            # Tri chronologique (plus ancien → plus récent) pour que rolling/ewm soient valides en fin de df
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
            
            return df
            
        except Exception as e:
            print(f"❌ Erreur récupération données {symbol}: {e}")
            return None
    
    def check_ema_slope(self, df: pd.DataFrame) -> bool:
        """Vérifie si la bougie clôture au-dessus ou en-dessous de l'EMA 20. Aligné backtest : iloc[-1]."""
        if len(df) < 2:
            return False
        current = df.iloc[-1]
        price_close = current['close']
        ema20 = current[f'EMA_{EMA_FAST}']
        
        # La bougie doit clôturer au-dessus OU en-dessous de l'EMA 20
        # (pas exactement sur l'EMA 20)
        return price_close != ema20
    
    def is_ema200_flat(self, df: pd.DataFrame) -> bool:
        """SMA 50 plate ou non. Aligné backtest : iloc[-1]=dernière barre fermée. Même seuil que backtest (len < 2)."""
        if len(df) < 2:
            return True
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
        """Vérifie la volatilité avec ATR (anti faux signaux). Aligné backtest : len < ATR_LOOKBACK + 1."""
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
    
    def load_h1_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Charge les données H1 pour l'analyse de tendance supérieure"""
        if not USE_H1_TREND_FILTER:
            return None
        
        try:
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
            
            # Récupérer TOUTES les données H1 disponibles
            # Limiter à 100000 bougies pour éviter les blocages (environ 11 ans de données)
            max_attempts = [10000, 50000, 100000]
            best_rates = None
            best_count = 0
            
            for max_bars in max_attempts:
                try:
                    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, max_bars)
                    
                    if rates is not None and len(rates) > 0:
                        count = len(rates)
                        if count > best_count:
                            best_rates = rates
                            best_count = count
                        
                        if count < max_bars:
                            break
                except Exception as e:
                    # Si erreur, continuer avec la prochaine tentative
                    self.log(f"   ⚠️  Erreur chargement H1 ({max_bars} bougies): {e}")
                    continue
            
            if best_rates is None or len(best_rates) == 0:
                return None
            
            # Convertir en DataFrame
            df = pd.DataFrame(best_rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True)
            
            # Les SMA ne sont utilisées QUE sur M5, pas sur H1
            # On détermine la tendance H1 uniquement avec le prix (pas de SMA)
            
            return df
        except Exception as e:
            self.log(f"   ⚠️  Exception lors du chargement H1 pour {symbol}: {e}")
            return None
    
    def reload_last_3_h1_bars(self, symbol: str) -> bool:
        """
        Recharge uniquement les 3 dernières bougies H1 (plus efficace)
        Retourne True si le rechargement a réussi
        """
        if not USE_H1_TREND_FILTER:
            return False
        
        try:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                found = self.find_symbol_variant(symbol)
                if found:
                    symbol = found
                    symbol_info = mt5.symbol_info(symbol)
                else:
                    return False
            
            if not symbol_info.visible:
                if not mt5.symbol_select(symbol, True):
                    return False
            
            # Récupérer uniquement les 3 dernières bougies H1
            rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_H1, 0, 3)
            
            if rates is None or len(rates) == 0:
                return False
            
            # Convertir en DataFrame
            df_new = pd.DataFrame(rates)
            df_new['time'] = pd.to_datetime(df_new['time'], unit='s')
            df_new.set_index('time', inplace=True)
            df_new.sort_index(inplace=True)
            
            # Si on a déjà des données H1 en cache, mettre à jour avec les nouvelles
            if symbol in self.h1_data and len(self.h1_data[symbol]) > 0:
                df_existing = self.h1_data[symbol]
                
                # Remplacer les 3 dernières bougies par les nouvelles
                # Garder toutes les bougies sauf les 3 dernières
                if len(df_existing) >= 3:
                    # Vérifier si les nouvelles bougies sont plus récentes
                    last_existing_time = df_existing.index[-1]
                    first_new_time = df_new.index[0]
                    
                    if first_new_time > last_existing_time:
                        # Les nouvelles bougies sont plus récentes, les ajouter
                        df_updated = pd.concat([df_existing, df_new])
                    else:
                        # Les nouvelles bougies remplacent les 3 dernières
                        df_updated = pd.concat([df_existing.iloc[:-3], df_new])
                else:
                    # Si on a moins de 3 bougies, utiliser les nouvelles
                    df_updated = df_new
                
                df_updated.sort_index(inplace=True)
                # Supprimer les doublons (garder la dernière occurrence)
                df_updated = df_updated[~df_updated.index.duplicated(keep='last')]
                self.h1_data[symbol] = df_updated
            else:
                # Pas de cache, charger toutes les données H1 disponibles
                # (on ne peut pas utiliser seulement 3 bougies car on a besoin d'historique)
                df_full = self.load_h1_data(symbol)
                if df_full is not None:
                    self.h1_data[symbol] = df_full
                else:
                    # En dernier recours, utiliser les 3 nouvelles bougies
                    self.h1_data[symbol] = df_new
            
            return True
        except Exception as e:
            self.log(f"   ⚠️  Erreur rechargement 3 dernières bougies H1 pour {symbol}: {e}")
            return False
    
    def get_h1_data_at_time(self, symbol: str, current_time: datetime) -> Optional[pd.DataFrame]:
        """
        Récupère les données H1 jusqu'à un moment donné (pour analyse de tendance)
        VERSION NON-BLOQUANTE: Utilise un fallback si le rechargement échoue
        """
        try:
            # Vérifier si on a des données H1 en cache
            if symbol not in self.h1_data or len(self.h1_data.get(symbol, pd.DataFrame())) == 0:
                # Recharger les données H1 si elles ne sont pas disponibles
                df_h1 = self.load_h1_data(symbol)
                if df_h1 is not None:
                    self.h1_data[symbol] = df_h1
                else:
                    return None
            
            df_h1 = self.h1_data[symbol]
            
            # Vérifier si on a besoin de données plus récentes
            # Si la dernière bougie H1 est trop ancienne, essayer de recharger
            # MAIS utiliser les données en cache comme fallback si le rechargement échoue
            if len(df_h1) > 0:
                last_h1_time = df_h1.index[-1]
                # Si la dernière bougie H1 est plus ancienne que current_time - 2h, essayer de recharger
                if last_h1_time < current_time - timedelta(hours=2):
                    # Essayer de recharger, mais ne pas bloquer si ça échoue
                    try:
                        df_h1_new = self.load_h1_data(symbol)
                        if df_h1_new is not None and len(df_h1_new) > 0:
                            self.h1_data[symbol] = df_h1_new
                            df_h1 = df_h1_new
                        # Si le rechargement échoue, utiliser les données en cache (fallback)
                    except Exception as e:
                        # En cas d'erreur, utiliser les données en cache
                        self.log(f"   ⚠️  Erreur rechargement H1 pour {symbol}, utilisation du cache: {e}")
            
            # Aligné backtest : données H1 jusqu'à current_time inclus (même logique que backtest)
            ts = pd.Timestamp(current_time)
            if hasattr(df_h1.index, 'tz') and df_h1.index.tz is not None and getattr(ts, 'tzinfo', None) is None:
                ts = ts.tz_localize(df_h1.index.tz)
            h1_data_until_now = df_h1[df_h1.index <= ts]
            
            if len(h1_data_until_now) < 3:
                return None
            
            return h1_data_until_now
        except Exception as e:
            self.log(f"   ❌ ERREUR H1: Exception dans get_h1_data_at_time pour {symbol}: {e}")
            # En cas d'erreur, retourner None (le filtre H1 bloquera le trade - OBLIGATOIRE)
            return None
    
    def check_h1_trend(self, symbol: str, current_time: datetime, trade_type: TradeType) -> bool:
        """
        Détermine la tendance sur H1 en analysant les 3 dernières bougies H1
        Retourne False si les données H1 ne sont pas disponibles (trade bloqué)
        """
        if not USE_H1_TREND_FILTER:
            return True
        
        try:
            df_h1 = self.get_h1_data_at_time(symbol, current_time)
            if df_h1 is None or len(df_h1) < 3:
                # ERREUR: Données H1 insuffisantes -> bloquer le trade (OBLIGATOIRE pour la stratégie)
                self.log(f"   ❌ ERREUR H1: Données H1 insuffisantes pour {symbol} (df_h1={'None' if df_h1 is None else len(df_h1)} bougies)")
                self.log(f"   ❌ Trade BLOQUÉ: Le filtre H1 est OBLIGATOIRE pour la stratégie")
                return False
            
            last_3_bars = df_h1.iloc[-3:]
            prices = last_3_bars['close'].values
            
            if len(prices) < 3:
                self.log(f"   ❌ ERREUR H1: Moins de 3 prix disponibles pour {symbol}")
                self.log(f"   ❌ Trade BLOQUÉ: Le filtre H1 est OBLIGATOIRE pour la stratégie")
                return False
            
            if trade_type == TradeType.LONG:
                price_first = prices[0]
                price_last = prices[-1]
                if price_last < price_first:
                    return False
                rises = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
                return rises >= 2
            else:  # SHORT
                price_first = prices[0]
                price_last = prices[-1]
                if price_last > price_first:
                    return False
                falls = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i-1])
                return falls >= 2
        except Exception as e:
            # ERREUR: Exception lors de l'analyse H1 -> bloquer le trade
            self.log(f"   ❌ ERREUR H1: Exception dans check_h1_trend pour {symbol}: {e}")
            self.log(f"   ❌ Trade BLOQUÉ: Le filtre H1 est OBLIGATOIRE pour la stratégie")
            return False
    
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
    
    def check_trend_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Vérifie que la bougie clôture au-dessus (LONG) ou en-dessous (SHORT) de l'EMA 20"""
        if not USE_TREND_FILTER or len(df) < 1:
            return True
        
        # Cette fonction n'est plus utilisée avec la stratégie croisement
        # Conservée pour compatibilité mais toujours retourne True
        return True
    
    def check_momentum_filter(self, df: pd.DataFrame, trade_type: TradeType) -> bool:
        """Vérifie le momentum avant l'entrée. Aligné backtest : iloc[-1]/[-2]."""
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
        """Évite les entrées trop loin de la SMA 50. Aligné backtest : len < 1, iloc[-1]."""
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
        """Évite les spreads trop larges entre EMA20 et SMA50. Aligné backtest : len < 1, iloc[-1]."""
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
        """Confirmation sur plusieurs bougies. Aligné backtest : LONG last > first, SHORT last < first."""
        if not USE_CONFIRMATION_FILTER or len(df) < CONFIRMATION_BARS + 1:
            return True
        recent_closes = df['close'].iloc[-(CONFIRMATION_BARS + 1):]
        if trade_type == TradeType.LONG:
            # Pour LONG: haussier = dernière > première
            return recent_closes.iloc[-1] > recent_closes.iloc[0]
        else:  # SHORT
            # Pour SHORT: baissier = dernière < première
            return recent_closes.iloc[-1] < recent_closes.iloc[0]
    
    def check_volatility_filter(self, df: pd.DataFrame) -> bool:
        """Évite les entrées dans volatilité excessive. Aligné backtest : len < ATR_LOOKBACK + 1."""
        if not USE_VOLATILITY_FILTER or 'ATR' not in df.columns or len(df) < ATR_LOOKBACK + 1:
            return True
        current_atr = df['ATR'].iloc[-1]
        if pd.isna(current_atr) or current_atr <= 0:
            return True
        atr_values = df['ATR'].iloc[-1 - ATR_LOOKBACK:-1]
        atr_avg = atr_values.mean()
        
        if atr_avg <= 0:
            return True
        
        # Éviter si volatilité trop élevée (marché agité)
        return current_atr <= (atr_avg * MAX_VOLATILITY_MULTIPLIER)
    
    def find_last_low(self, symbol: str, df: pd.DataFrame, lookback: int = 10) -> float:
        """Calcule le SL pour LONG (ATR ou dernier swing). Aligné backtest : lookback = len(df) si len < lookback."""
        current_price = df['close'].iloc[-1]
        
        if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
            current_atr = df['ATR'].iloc[-1]
            if not pd.isna(current_atr) and current_atr > 0:
                return current_price - (current_atr * ATR_SL_MULTIPLIER)
        
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
        """Calcule le SL pour SHORT (ATR ou dernier swing). Aligné backtest : lookback = len(df) si len < lookback."""
        current_price = df['close'].iloc[-1]
        
        if USE_ATR_SL and 'ATR' in df.columns and len(df) > 0:
            current_atr = df['ATR'].iloc[-1]
            if not pd.isna(current_atr) and current_atr > 0:
                return current_price + (current_atr * ATR_SL_MULTIPLIER)
        
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
    
    def check_long_entry(self, df: pd.DataFrame, symbol: str = "") -> bool:
        """
        Vérifie les conditions d'entrée LONG sur M5.
        Même logique que le backtest : df ne contient que des barres fermées, donc iloc[-1]=dernière fermée.
        """
        if len(df) < 5:
            return False
        
        current_time = df.index[-1]
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime()
        
        if not self.is_valid_trading_session(current_time):
            return False
        
        if USE_H1_TREND_FILTER and symbol:
            h1_trend_ok = self.check_h1_trend(symbol, current_time, TradeType.LONG)
            if not h1_trend_ok:
                return False
        
        # iloc[-1]=dernière barre fermée, iloc[-2]=avant-dernière (aligné backtest)
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema20_current = current[f'EMA_{EMA_FAST}']
        sma50_current = current[f'SMA_{SMA_SLOW}']
        ema20_prev = prev[f'EMA_{EMA_FAST}']
        sma50_prev = prev[f'SMA_{SMA_SLOW}']
        
        # Aligné backtest : pas de vérification NaN sur les indicateurs d'entrée
        
        # Condition M5: EMA 20 doit croiser au-dessus de SMA 50
        # EMA 20 était en dessous de SMA 50 à la bougie précédente
        # EMA 20 est maintenant au-dessus de SMA 50
        if ema20_prev >= sma50_prev:
            return False  # Pas de croisement haussier
        
        if ema20_current <= sma50_current:
            return False  # Pas encore au-dessus après croisement
        
        return True
    
    def check_short_entry(self, df: pd.DataFrame, symbol: str = "") -> bool:
        """
        Vérifie les conditions d'entrée SHORT sur M5 (même logique que backtest : iloc[-1]/[-2]).
        """
        if len(df) < 5:
            return False
        
        current_time = df.index[-1]
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime()
        
        if not self.is_valid_trading_session(current_time):
            return False
        
        if USE_H1_TREND_FILTER and symbol:
            h1_trend_ok = self.check_h1_trend(symbol, current_time, TradeType.SHORT)
            if not h1_trend_ok:
                return False
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema20_current = current[f'EMA_{EMA_FAST}']
        sma50_current = current[f'SMA_{SMA_SLOW}']
        ema20_prev = prev[f'EMA_{EMA_FAST}']
        sma50_prev = prev[f'SMA_{SMA_SLOW}']
        
        # Aligné backtest : pas de vérification NaN sur les indicateurs d'entrée
        
        # Condition M5: EMA 20 doit croiser en-dessous de SMA 50
        # EMA 20 était au-dessus de SMA 50 à la bougie précédente
        # EMA 20 est maintenant en-dessous de SMA 50
        if ema20_prev <= sma50_prev:
            return False  # Pas de croisement baissier
        
        if ema20_current >= sma50_current:
            return False  # Pas encore en-dessous après croisement
        
        return True
    
    def calculate_lot_size(self, symbol: str, entry_price: float, stop_loss: float) -> float:
        """Calcule la taille du lot selon le risque"""
        # Vérifier la connexion
        if not self.check_connection():
            return 0
        
        # Récupérer les infos du compte
        account_info = mt5.account_info()
        if not account_info:
            return 0
        
        balance = account_info.balance
        risk_amount = balance * (self.risk_percent / 100.0)
        
        # Récupérer les infos du symbole
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return 0
        
        stop_distance = abs(entry_price - stop_loss)
        
        if stop_distance <= 0:
            return 0
        
        # Calculer le risque par lot
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size
        point = symbol_info.point
        
        if tick_size > 0 and tick_value > 0:
            ticks_in_stop = stop_distance / tick_size
            risk_per_lot = ticks_in_stop * tick_value
        else:
            # Fallback: utiliser contract size
            contract_size = symbol_info.trade_contract_size
            if contract_size > 0:
                risk_per_lot = (stop_distance * contract_size) / entry_price
            else:
                # Dernier recours: estimation
                risk_per_lot = stop_distance * point
        
        if risk_per_lot <= 0:
            return 0
        
        # Calculer le lot nécessaire
        lot_size = risk_amount / risk_per_lot
        
        # Normaliser selon les contraintes du broker
        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        lot_step = symbol_info.volume_step
        
        if lot_size < min_lot:
            lot_size = min_lot
        if lot_size > max_lot:
            lot_size = max_lot
        
        # Arrondir au step
        if lot_step > 0:
            lot_size = (lot_size // lot_step) * lot_step
        
        return round(lot_size, 2)
    
    def calculate_profit(self, symbol: str, entry_price: float, exit_price: float, lot_size: float, trade_type: TradeType) -> float:
        """Calcule le profit/perte d'un trade (utilise la même logique que calculate_lot_size)
        Note: Dans la production, MT5 calcule automatiquement le profit via pos.profit.
        Cette fonction est utile pour des estimations ou des logs."""
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
    
    def has_open_position(self, symbol: str) -> bool:
        """Vérifie si au moins une position est ouverte pour ce symbole"""
        # Vérifier la connexion
        if not self.check_connection():
            return False
        
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return False
        
        count = sum(1 for pos in positions if pos.magic == self.magic_number)
        return count > 0
    
    def get_open_positions_count(self, symbol: str) -> int:
        """Retourne le nombre de positions ouvertes pour ce symbole"""
        if not self.check_connection():
            return 0
        
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return 0
        
        return sum(1 for pos in positions if pos.magic == self.magic_number)
    
    def get_daily_loss(self) -> float:
        """
        Calcule la perte quotidienne à partir des DEALS du jour (P&L réalisé des trades fermés uniquement).
        N'inclut pas les dépôts/retraits, pour éviter de déclencher la protection à tort.
        """
        if not self.check_connection():
            return 0.0
        
        account_info = mt5.account_info()
        if not account_info:
            return 0.0
        
        current_date = datetime.now().date()
        current_balance = account_info.balance
        was_new_day = False
        
        # Mise à jour du suivi du jour (pour logs et affichage)
        if self.last_trading_date is None or current_date > self.last_trading_date:
            was_new_day = self.last_trading_date is not None
            self.daily_start_balance = current_balance
            self.daily_start_equity = account_info.equity
            self.trading_stopped_daily = False
            self.last_trading_date = current_date
            if was_new_day:
                self.log(f"🔄 Nouveau jour détecté - Réinitialisation du suivi quotidien")
                self.log(f"   Balance de début: {self.daily_start_balance:.2f} {account_info.currency}")
        elif self.daily_start_balance is None:
            self.daily_start_balance = current_balance
            self.daily_start_equity = account_info.equity
            self.trading_stopped_daily = False
            self.last_trading_date = current_date
        
        if self.daily_start_balance is None:
            self.daily_start_balance = current_balance
            self.trading_stopped_daily = False
            self.last_trading_date = current_date
            self.log(f"⚠️  Initialisation d'urgence balance de début: {self.daily_start_balance:.2f} {account_info.currency}")
        
        # P&L quotidien = somme des deals du jour (trades fermés uniquement, pas dépôts/retraits)
        date_from = datetime(current_date.year, current_date.month, current_date.day, 0, 0, 0)
        date_to = datetime.now()
        deals = mt5.history_deals_get(date_from, date_to)
        daily_pnl = 0.0
        if deals is not None:
            for d in deals:
                # Chaque deal: profit + commission + swap (P&L réalisé du trade)
                daily_pnl += getattr(d, 'profit', 0.0) + getattr(d, 'commission', 0.0) + getattr(d, 'swap', 0.0)
        else:
            # Fallback si l'historique n'est pas dispo (broker/API) : utiliser balance (peut inclure dépôts/retraits)
            daily_pnl = current_balance - self.daily_start_balance
            if self.daily_start_balance is not None and abs(daily_pnl) > 500:
                self.log(f"⚠️  Historique des deals indisponible, P&L basé sur la balance. Dépôts/retraits peuvent fausser la protection.")
        
        return daily_pnl
    
    def can_trade_today(self) -> Tuple[bool, str]:
        """
        Vérifie si on peut trader aujourd'hui (pas de limite de perte atteinte)
        
        NOTE: La protection est basée sur le P&L réalisé du jour (somme des deals), pas sur la
        variation de balance (dépôts/retraits exclus). Si la limite est atteinte, elle s'applique à TOUS les symboles.
        Cependant, on vérifie la protection AVANT de traiter chaque symbole
        pour permettre à tous les symboles d'être évalués dans la même itération
        avant que la protection ne soit déclenchée.
        """
        # Calculer la perte quotidienne (cela réinitialise aussi si nouveau jour)
        daily_loss = self.get_daily_loss()
        
        # Vérifier si la limite est atteinte
        if daily_loss <= self.max_daily_loss:
            # Marquer comme arrêté seulement si pas déjà arrêté (évite les messages répétés)
            if not self.trading_stopped_daily:
                self.trading_stopped_daily = True
                account_info = mt5.account_info()
                currency = account_info.currency if account_info else "USD"
                self.log(f"🛡️  Protection quotidienne déclenchée: {daily_loss:.2f} {currency} <= {self.max_daily_loss:.2f} {currency}")
            
            account_info = mt5.account_info()
            currency = account_info.currency if account_info else "USD"
            return False, f"Limite de perte quotidienne atteinte: {daily_loss:.2f} {currency} (limite: {self.max_daily_loss:.2f} {currency})"
        
        # Si on était arrêté mais que la perte est maintenant acceptable (nouveau jour ou récupération)
        if self.trading_stopped_daily:
            self.trading_stopped_daily = False
        
        return True, ""
    
    def has_recent_same_setup(self, symbol: str, trade_type: TradeType, current_bar_time: datetime) -> Tuple[bool, Optional[str]]:
        """Vérifie si une position du même type a été ouverte récemment sur ce symbole"""
        key = (symbol, trade_type)
        
        if key not in self.last_trade_by_symbol_type:
            return False, None
        
        last_trade_time = self.last_trade_by_symbol_type[key]
        
        # Calculer le nombre de bougies écoulées depuis la dernière position
        # Timeframe M5 = 5 minutes par bougie
        time_diff = current_bar_time - last_trade_time
        bars_elapsed = time_diff.total_seconds() / 300  # 300 secondes = 5 minutes
        
        if bars_elapsed < MIN_BARS_BETWEEN_SAME_SETUP:
            remaining_bars = int(MIN_BARS_BETWEEN_SAME_SETUP - bars_elapsed)
            return True, f"Setup {trade_type.value} déjà traité il y a {int(bars_elapsed)} bougie(s). Attendre encore {remaining_bars} bougie(s) avant de rouvrir."
        
        return False, None
    
    def is_in_cooldown(self, current_bar_time: datetime) -> bool:
        """Vérifie si on est en cooldown après une perte (pas de re-entrée immédiate)"""
        if self.last_loss_time is None:
            return False
        
        time_diff = current_bar_time - self.last_loss_time
        bars_elapsed = time_diff.total_seconds() / 300  # 300 secondes = 5 minutes
        
        return bars_elapsed < COOLDOWN_AFTER_LOSS
    
    def record_trade(self, symbol: str, trade_type: TradeType, trade_time: datetime):
        """Enregistre qu'une position a été ouverte pour ce symbole et type"""
        key = (symbol, trade_type)
        self.last_trade_by_symbol_type[key] = trade_time
    
    def open_long_position(self, symbol: str, df: pd.DataFrame, entry_price_bar_override: Optional[float] = None) -> Optional[Trade]:
        """Ouvre une position LONG réelle sur MT5"""
        if not ALLOW_LONG:
            return None
        
        # Vérifier la connexion avant d'ouvrir
        if not self.check_connection():
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Connexion MT5 perdue",
                error_message="Impossible de se connecter à MT5"
            )
            print(f"❌ Impossible d'ouvrir LONG {symbol}: connexion MT5 perdue")
            return None
        
        # Permettre plusieurs positions simultanées - pas de vérification bloquante
        
        # Récupérer les prix réels de MT5
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Symbol info non disponible",
                error_message="mt5.symbol_info() a retourné None"
            )
            return None
        
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Tick non disponible",
                error_message="mt5.symbol_info_tick() a retourné None"
            )
            self.log(f"❌ Impossible de récupérer le tick pour {symbol}")
            return None
        
        # Parité backtest: prix de référence = open de la bougie suivante (ou close de la bougie signal en fallback)
        if entry_price_bar_override is not None:
            entry_price_bar = float(entry_price_bar_override)
        else:
            entry_price_bar = float(df['close'].iloc[-1])
        if entry_price_bar <= 0 or (entry_price_bar != entry_price_bar):  # NaN check
            self.log(f"❌ Prix d'entrée de référence invalide pour LONG {symbol}: {entry_price_bar}")
            return None
        ask = getattr(tick, 'ask', None)
        if ask is None:
            self.log(f"❌ Tick.ask indisponible pour LONG {symbol}")
            return None
        try:
            execution_price = float(ask)
        except (TypeError, ValueError):
            self.log(f"❌ Tick.ask non convertible en float pour LONG {symbol}: {ask}")
            return None
        if execution_price <= 0 or (execution_price != execution_price):
            self.log(f"❌ Prix exécution (ask) invalide pour LONG {symbol}: {execution_price}")
            return None
        
        # Calculer stop-loss basé sur les données historiques (même logique que backtest)
        stop_loss = self.find_last_low(symbol, df, 10)
        
        if stop_loss <= 0 or stop_loss >= entry_price_bar:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Stop-loss invalide",
                error_message=f"SL: {stop_loss:.2f}, Entry bar: {entry_price_bar:.2f}"
            )
            self.log(f"❌ Stop-loss invalide pour LONG {symbol} (SL: {stop_loss:.2f}, Entry: {entry_price_bar:.2f})")
            return None
        
        # Validation stricte: vérifier que le SL est raisonnable (max 5% du prix d'entrée barre)
        sl_distance_pct = abs(entry_price_bar - stop_loss) / entry_price_bar
        if sl_distance_pct > 0.05:  # 5% max
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Stop-loss trop éloigné",
                error_message=f"Distance: {sl_distance_pct*100:.2f}% > 5%"
            )
            self.log(f"❌ Stop-loss trop éloigné pour LONG {symbol} ({sl_distance_pct*100:.2f}% > 5%)")
            return None
        
        # SL doit rester sous le prix d'exécution (sinon ordre invalide)
        if stop_loss >= execution_price:
            self.log(f"❌ Stop-loss {stop_loss:.2f} >= prix exécution {execution_price:.2f} (marché a bougé), abandon LONG {symbol}")
            return None
        
        # R:R adaptatif selon pente SMA 50 — PROD: tout basé sur le prix d'exécution pour que risque et gain en $ soient exacts
        rr_ratio = self.get_risk_reward_ratio(df)
        sl_distance = execution_price - stop_loss  # distance réelle depuis le prix de fill
        take_profit = execution_price + (sl_distance * rr_ratio)
        
        is_flat = self.is_ema200_flat(df)
        self.log(f"   📊 R:R utilisé: 1:{rr_ratio:.1f} ({'SMA50 plate' if is_flat else 'SMA50 penche'})")
        self.log(f"   📍 Prix exécution (ask): {execution_price:.2f} | SL niveau: {stop_loss:.2f} | distance SL: {sl_distance:.2f}")
        
        # Normaliser les prix selon les digits du symbole (pour SL/TP uniquement)
        digits = getattr(symbol_info, 'digits', 2)
        if digits is None:
            digits = 2
        digits = int(digits)
        stop_loss = round(stop_loss, digits)
        take_profit = round(take_profit, digits)
        
        # Vérifier que les stops respectent la distance minimale requise par le broker
        stops_level = getattr(symbol_info, 'trade_stops_level', getattr(symbol_info, 'stops_level', 0)) or 0
        point = getattr(symbol_info, 'point', None)
        if point is None or point <= 0:
            point = 0.01  # fallback pour indices
        if stops_level == 0:
            min_distance = 50 * point
        else:
            min_distance = max(stops_level, 50) * point
        
        # Pour LONG: SL au moins min_distance sous le prix d'exécution
        if sl_distance < min_distance:
            stop_loss = execution_price - min_distance
            stop_loss = round(stop_loss, digits)
            sl_distance = execution_price - stop_loss
            take_profit = execution_price + (sl_distance * rr_ratio)
            take_profit = round(take_profit, digits)
            self.log(f"   ⚠️  SL ajusté pour respecter stops_level ({stops_level} points)")
        
        # TP au moins min_distance au-dessus du prix d'exécution
        tp_distance = take_profit - execution_price
        if tp_distance < min_distance:
            take_profit = execution_price + min_distance
            take_profit = round(take_profit, digits)
            self.log(f"   ⚠️  TP ajusté pour respecter stops_level ({stops_level} points)")
        
        # Lot basé sur le prix d'exécution réel pour que perte au SL = exactement risk% du capital
        lot_size = self.calculate_lot_size(symbol, execution_price, stop_loss)
        
        if lot_size <= 0:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Lot size invalide",
                error_message="calculate_lot_size() a retourné 0 ou négatif"
            )
            self.log(f"❌ Lot size invalide pour LONG {symbol}")
            return None
        
        # Préparer la requête avec SL/TP calculés selon le R:R
        # Utiliser le prix actuel du tick (comme dans l'ancien code qui fonctionnait)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick.ask,  # Prix actuel du marché (comme dans l'ancien code)
            "sl": stop_loss,  # SL calculé selon R:R
            "tp": take_profit,  # TP calculé selon R:R
            "deviation": 10,
            "magic": self.magic_number,
            "comment": self.trade_comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="LONG",
                reason="Erreur order_send MT5",
                error_code=result.retcode,
                error_message=result.comment
            )
            self.log(f"❌ Erreur ouverture LONG {symbol}: {result.retcode} - {result.comment}")
            return None
        
        # Récupérer le ticket de la position ouverte
        position_ticket = result.order
        
        # Attendre un peu pour que la position soit visible
        time.sleep(0.5)
        
        # Vérifier si SL et TP ont été appliqués
        positions = mt5.positions_get(ticket=position_ticket)
        if positions and len(positions) > 0:
            pos = positions[0]
            
            # Si SL ou TP ne sont pas définis, les ajouter
            if pos.sl == 0 or pos.tp == 0:
                self.log(f"   ⚠️  SL/TP non appliqués à l'ouverture, ajout après ouverture...")
                
                modify_request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": symbol,
                    "position": position_ticket,
                    "sl": stop_loss,
                    "tp": take_profit,
                }
                
                modify_result = mt5.order_send(modify_request)
                if modify_result.retcode == mt5.TRADE_RETCODE_DONE:
                    self.log(f"   ✅ SL et TP ajoutés avec succès")
                else:
                    self.log(f"   ⚠️  Erreur ajout SL/TP: {modify_result.retcode} - {modify_result.comment}")
                    self.log(f"   ⚠️  Vous devrez peut-être les ajouter manuellement")
            else:
                self.log(f"   ✅ SL et TP appliqués automatiquement")
        
        # Créer l'objet Trade
        trade = Trade(
            symbol=symbol,
            type=TradeType.LONG,
            entry_price=result.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            ticket=position_ticket
        )
        
        # Profit attendu au TP (même logique que risque au SL × R:R)
        account_info = mt5.account_info()
        if account_info:
            risk_amount = account_info.balance * (self.risk_percent / 100.0)
            expected_tp_profit = risk_amount * rr_ratio
            self.log(f"   📈 Profit attendu au TP (1%×R:R): ~{expected_tp_profit:.2f} {account_info.currency}")
        self.log(f"\n✅ LONG ouvert: {symbol}")
        self.log(f"   Ticket: {position_ticket}")
        self.log(f"   Entry: {result.price:.2f}")
        self.log(f"   SL: {stop_loss:.2f} (distance: {sl_distance:.2f})")
        self.log(f"   TP: {take_profit:.2f} (distance: {take_profit - result.price:.2f})")
        self.log(f"   Lot: {lot_size}")
        self.log(f"   R:R = 1:{rr_ratio:.1f}")
        
        # Ajouter le trade à la liste (plusieurs trades par symbole)
        if symbol not in self.open_trades:
            self.open_trades[symbol] = []
        elif not isinstance(self.open_trades[symbol], list):
            # Corriger si une ancienne version avait stocké un seul Trade au lieu d'une liste
            self.open_trades[symbol] = [self.open_trades[symbol]]
        self.open_trades[symbol].append(trade)
        self.trade_history.append(trade)
        
        current_bar_time = df.index[-1]
        if hasattr(current_bar_time, 'to_pydatetime'):
            current_bar_time = current_bar_time.to_pydatetime()
        self.record_trade(symbol, TradeType.LONG, current_bar_time)
        
        return trade
    
    def open_short_position(self, symbol: str, df: pd.DataFrame, entry_price_bar_override: Optional[float] = None) -> Optional[Trade]:
        """Ouvre une position SHORT réelle sur MT5"""
        if not ALLOW_SHORT:
            return None
        
        # Vérifier la connexion avant d'ouvrir
        if not self.check_connection():
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Connexion MT5 perdue",
                error_message="Impossible de se connecter à MT5"
            )
            print(f"❌ Impossible d'ouvrir SHORT {symbol}: connexion MT5 perdue")
            return None
        
        # Permettre plusieurs positions simultanées - pas de vérification bloquante (comme en backtest)
        
        # Récupérer les prix réels de MT5
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Symbol info non disponible",
                error_message="mt5.symbol_info() a retourné None"
            )
            return None
        
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Tick non disponible",
                error_message="mt5.symbol_info_tick() a retourné None"
            )
            print(f"❌ Impossible de récupérer le tick pour {symbol}")
            return None
        
        # Parité backtest: prix de référence = open de la bougie suivante (ou close de la bougie signal en fallback)
        if entry_price_bar_override is not None:
            entry_price_bar = float(entry_price_bar_override)
        else:
            entry_price_bar = float(df['close'].iloc[-1])
        if entry_price_bar <= 0 or (entry_price_bar != entry_price_bar):
            self.log(f"❌ Prix d'entrée de référence invalide pour SHORT {symbol}: {entry_price_bar}")
            return None
        bid = getattr(tick, 'bid', None)
        if bid is None:
            self.log(f"❌ Tick.bid indisponible pour SHORT {symbol}")
            return None
        try:
            execution_price = float(bid)
        except (TypeError, ValueError):
            self.log(f"❌ Tick.bid non convertible en float pour SHORT {symbol}: {bid}")
            return None
        if execution_price <= 0 or (execution_price != execution_price):
            self.log(f"❌ Prix exécution (bid) invalide pour SHORT {symbol}: {execution_price}")
            return None
        
        # Calculer stop-loss basé sur les données historiques (même logique que backtest)
        stop_loss = self.find_last_high(symbol, df, 10)
        
        if stop_loss <= 0 or stop_loss <= entry_price_bar:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Stop-loss invalide",
                error_message=f"SL: {stop_loss:.2f}, Entry bar: {entry_price_bar:.2f}"
            )
            self.log(f"❌ Stop-loss invalide pour SHORT {symbol} (SL: {stop_loss:.2f}, Entry: {entry_price_bar:.2f})")
            return None
        
        # Validation stricte: vérifier que le SL est raisonnable (max 5% du prix d'entrée barre)
        sl_distance_pct = abs(stop_loss - entry_price_bar) / entry_price_bar
        if sl_distance_pct > 0.05:  # 5% max
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Stop-loss trop éloigné",
                error_message=f"Distance: {sl_distance_pct*100:.2f}% > 5%"
            )
            self.log(f"❌ Stop-loss trop éloigné pour SHORT {symbol} ({sl_distance_pct*100:.2f}% > 5%)")
            return None
        
        # SL doit rester au-dessus du prix d'exécution (sinon ordre invalide)
        if stop_loss <= execution_price:
            self.log(f"❌ Stop-loss {stop_loss:.2f} <= prix exécution {execution_price:.2f} (marché a bougé), abandon SHORT {symbol}")
            return None
        
        # R:R adaptatif — PROD: tout basé sur le prix d'exécution pour que risque et gain en $ soient exacts
        rr_ratio = self.get_risk_reward_ratio(df)
        sl_distance = stop_loss - execution_price  # distance réelle depuis le prix de fill
        take_profit = execution_price - (sl_distance * rr_ratio)
        
        is_flat = self.is_ema200_flat(df)
        self.log(f"   📊 R:R utilisé: 1:{rr_ratio:.1f} ({'SMA50 plate' if is_flat else 'SMA50 penche'})")
        self.log(f"   📍 Prix exécution (bid): {execution_price:.2f} | SL niveau: {stop_loss:.2f} | distance SL: {sl_distance:.2f}")
        
        # Normaliser les prix selon les digits du symbole (pour SL/TP uniquement)
        digits = getattr(symbol_info, 'digits', 2)
        if digits is None:
            digits = 2
        digits = int(digits)
        stop_loss = round(stop_loss, digits)
        take_profit = round(take_profit, digits)
        
        # Vérifier que les stops respectent la distance minimale requise par le broker
        stops_level = getattr(symbol_info, 'trade_stops_level', getattr(symbol_info, 'stops_level', 0)) or 0
        point = getattr(symbol_info, 'point', None)
        if point is None or point <= 0:
            point = 0.01  # fallback pour indices
        if stops_level == 0:
            min_distance = 50 * point
        else:
            min_distance = max(stops_level, 50) * point
        
        # Pour SHORT: SL au moins min_distance au-dessus du prix d'exécution
        if sl_distance < min_distance:
            stop_loss = execution_price + min_distance
            stop_loss = round(stop_loss, digits)
            sl_distance = stop_loss - execution_price
            take_profit = execution_price - (sl_distance * rr_ratio)
            take_profit = round(take_profit, digits)
            self.log(f"   ⚠️  SL ajusté pour respecter stops_level ({stops_level} points)")
        
        # TP au moins min_distance en dessous du prix d'exécution
        tp_distance = execution_price - take_profit
        if tp_distance < min_distance:
            take_profit = execution_price - min_distance
            take_profit = round(take_profit, digits)
            self.log(f"   ⚠️  TP ajusté pour respecter stops_level ({stops_level} points)")
        
        # Lot basé sur le prix d'exécution réel pour que perte au SL = exactement risk% du capital
        lot_size = self.calculate_lot_size(symbol, execution_price, stop_loss)
        
        if lot_size <= 0:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Lot size invalide",
                error_message="calculate_lot_size() a retourné 0 ou négatif"
            )
            self.log(f"❌ Lot size invalide pour SHORT {symbol}")
            return None
        
        # Préparer la requête avec SL/TP calculés selon le R:R
        # Utiliser le prix actuel du tick (comme dans l'ancien code qui fonctionnait)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_SELL,
            "price": tick.bid,  # Prix actuel du marché (comme dans l'ancien code)
            "sl": stop_loss,  # SL calculé selon R:R
            "tp": take_profit,  # TP calculé selon R:R
            "deviation": 10,
            "magic": self.magic_number,
            "comment": self.trade_comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.log_failed_trade_attempt(
                symbol=symbol,
                trade_type="SHORT",
                reason="Erreur order_send MT5",
                error_code=result.retcode,
                error_message=result.comment
            )
            self.log(f"❌ Erreur ouverture SHORT {symbol}: {result.retcode} - {result.comment}")
            return None
        
        # Récupérer le ticket de la position ouverte
        position_ticket = result.order
        
        # Attendre un peu pour que la position soit visible
        time.sleep(0.5)
        
        # Vérifier si SL et TP ont été appliqués
        positions = mt5.positions_get(ticket=position_ticket)
        if positions and len(positions) > 0:
            pos = positions[0]
            
            # Si SL ou TP ne sont pas définis, les ajouter
            if pos.sl == 0 or pos.tp == 0:
                self.log(f"   ⚠️  SL/TP non appliqués à l'ouverture, ajout après ouverture...")
                
                modify_request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": symbol,
                    "position": position_ticket,
                    "sl": stop_loss,
                    "tp": take_profit,
                }
                
                modify_result = mt5.order_send(modify_request)
                if modify_result.retcode == mt5.TRADE_RETCODE_DONE:
                    self.log(f"   ✅ SL et TP ajoutés avec succès")
                else:
                    self.log(f"   ⚠️  Erreur ajout SL/TP: {modify_result.retcode} - {modify_result.comment}")
                    self.log(f"   ⚠️  Vous devrez peut-être les ajouter manuellement")
            else:
                self.log(f"   ✅ SL et TP appliqués automatiquement")
        
        # Créer l'objet Trade
        trade = Trade(
            symbol=symbol,
            type=TradeType.SHORT,
            entry_price=result.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            ticket=position_ticket
        )
        
        # Profit attendu au TP (même logique que risque au SL × R:R)
        account_info = mt5.account_info()
        if account_info:
            risk_amount = account_info.balance * (self.risk_percent / 100.0)
            expected_tp_profit = risk_amount * rr_ratio
            self.log(f"   📈 Profit attendu au TP (1%×R:R): ~{expected_tp_profit:.2f} {account_info.currency}")
        self.log(f"\n✅ SHORT ouvert: {symbol}")
        self.log(f"   Ticket: {position_ticket}")
        self.log(f"   Entry: {result.price:.2f}")
        self.log(f"   SL: {stop_loss:.2f} (distance: {sl_distance:.2f})")
        self.log(f"   TP: {take_profit:.2f} (distance: {execution_price - take_profit:.2f})")
        self.log(f"   Lot: {lot_size}")
        self.log(f"   R:R = 1:{rr_ratio:.1f}")
        
        # Ajouter le trade à la liste (comme pour LONG - plusieurs positions par symbole)
        if symbol not in self.open_trades:
            self.open_trades[symbol] = []
        elif not isinstance(self.open_trades[symbol], list):
            # Corriger si une ancienne version avait stocké un seul Trade au lieu d'une liste
            self.open_trades[symbol] = [self.open_trades[symbol]]
        self.open_trades[symbol].append(trade)
        self.trade_history.append(trade)
        
        current_bar_time = df.index[-1]
        if hasattr(current_bar_time, 'to_pydatetime'):
            current_bar_time = current_bar_time.to_pydatetime()
        self.record_trade(symbol, TradeType.SHORT, current_bar_time)
        
        return trade
    
    def process_symbol(self, symbol: str):
        """Traite un symbole: vérifie les conditions et ouvre des positions"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log(f"\n[{timestamp}] 🔍 Analyse de {symbol}...")
        
        # Vérifier si on peut trader aujourd'hui (protection quotidienne)
        can_trade, reason = self.can_trade_today()
        if not can_trade:
            self.log(f"   🛡️  {reason}")
            return
        
        # Afficher la perte quotidienne actuelle
        daily_loss = self.get_daily_loss()
        account_info = mt5.account_info()
        currency = account_info.currency if account_info else "USD"
        loss_pct = (daily_loss / self.daily_start_balance * 100) if self.daily_start_balance and self.daily_start_balance > 0 else 0
        self.log(f"   📊 Perte quotidienne: {daily_loss:.2f} {currency} ({loss_pct:.2f}%) | Limite: {self.max_daily_loss:.2f} {currency}")
        
        # Récupérer les données depuis MT5
        raw_df = self.get_market_data(symbol)
        if raw_df is None or len(raw_df) < SMA_SLOW + 10:
            self.log(f"   ⚠️  Données insuffisantes pour {symbol}")
            return

        # Parité backtest:
        # - df = barres fermées (signal)
        # - current_open = open de la bougie suivante (prix de référence d'entrée)
        if len(raw_df) <= 1:
            self.log(f"   ⚠️  Données insuffisantes après séparation barres fermées/en cours pour {symbol}")
            return
        df = raw_df.iloc[:-1]
        current_open = float(raw_df.iloc[-1]['open']) if self.use_next_bar_open_for_entry else float(df.iloc[-1]['close'])
        
        # iloc[-1] = dernière barre fermée (indicateurs valides)
        current = df.iloc[-1]
        price = current['close']
        ema20 = current[f'EMA_{EMA_FAST}']
        sma50 = current[f'SMA_{SMA_SLOW}']
        
        self.log(f"   💹 Prix: {price:.2f} | EMA{EMA_FAST}: {ema20:.2f} | SMA{SMA_SLOW}: {sma50:.2f}")
        
        # Nouvelle bougie = index[-1] (dernière barre fermée)
        current_time = df.index[-1]
        # Convertir en datetime si c'est un Timestamp pandas
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime()
        
        if symbol in self.last_bar_time and current_time <= self.last_bar_time[symbol]:
            self.log(f"   ⏸️  Pas de nouvelle bougie (dernière: {self.last_bar_time[symbol].strftime('%H:%M')})")
            return  # Pas de nouvelle bougie
        
        self.last_bar_time[symbol] = current_time
        
        # Afficher la session de trading actuelle
        session = self.get_trading_session(current_time)
        session_emoji = "🌍" if session == TradingSession.ASIA else "🇪🇺" if session == TradingSession.EUROPE else "🇺🇸" if session == TradingSession.US else "🌙"
        session_status = "✅" if session != TradingSession.OFF_HOURS else "❌"
        self.log(f"   ✅ Nouvelle bougie détectée: {current_time.strftime('%H:%M:%S')} UTC | Session: {session_emoji} {session.value} {session_status}")
        
        # Recharger les 3 dernières bougies H1 à chaque heure pile (minute = 0)
        if USE_H1_TREND_FILTER and current_time.minute == 0:
            current_hour = current_time.hour
            # Vérifier si on n'a pas déjà rechargé pour cette heure
            if symbol not in self.last_h1_reload_hour or self.last_h1_reload_hour[symbol] != current_hour:
                self.log(f"   🔄 Rechargement des 3 dernières bougies H1 (heure pile: {current_time.strftime('%H:00')} UTC)")
                if self.reload_last_3_h1_bars(symbol):
                    self.last_h1_reload_hour[symbol] = current_hour
                    self.log(f"   ✅ Données H1 mises à jour (3 dernières bougies rechargées)")
                else:
                    self.log(f"   ⚠️  Échec du rechargement des données H1, utilisation du cache")
        
        # Vérifier et logger l'état des données H1
        if USE_H1_TREND_FILTER:
            df_h1_check = self.get_h1_data_at_time(symbol, current_time)
            if df_h1_check is not None and len(df_h1_check) >= 3:
                last_h1_time = df_h1_check.index[-1]
                # Convertir en datetime si nécessaire
                if hasattr(last_h1_time, 'to_pydatetime'):
                    last_h1_time_py = last_h1_time.to_pydatetime()
                else:
                    last_h1_time_py = last_h1_time
                h1_age_minutes = (current_time - last_h1_time_py).total_seconds() / 60
                
                # Vérifier si la dernière bougie H1 est trop ancienne
                # Normalement, la dernière bougie H1 complète devrait être celle qui vient de se terminer
                # - 0-60 min : ✅ Normal (bougie récente)
                # - 60-90 min : ⚠️ Suspect (on a peut-être manqué un rechargement)
                # - > 90 min : ❌ Anormal (sauf si le marché est fermé)
                if h1_age_minutes <= 60:
                    status_emoji = "✅"
                    status_msg = ""
                elif h1_age_minutes <= 90:
                    status_emoji = "⚠️"
                    status_msg = " (Bougie H1 > 60 min - rechargement manqué à l'heure pile ?)"
                    # Si on n'est pas à l'heure pile, essayer de recharger maintenant
                    if current_time.minute != 0:
                        self.log(f"   🔄 Rechargement H1 déclenché (bougie > 60 min: {h1_age_minutes:.0f} min)")
                        if self.reload_last_3_h1_bars(symbol):
                            # Recalculer l'âge après rechargement
                            df_h1_updated = self.get_h1_data_at_time(symbol, current_time)
                            if df_h1_updated is not None and len(df_h1_updated) > 0:
                                last_h1_time_updated = df_h1_updated.index[-1]
                                if hasattr(last_h1_time_updated, 'to_pydatetime'):
                                    last_h1_time_updated_py = last_h1_time_updated.to_pydatetime()
                                else:
                                    last_h1_time_updated_py = last_h1_time_updated
                                h1_age_minutes = (current_time - last_h1_time_updated_py).total_seconds() / 60
                                if h1_age_minutes <= 60:
                                    status_emoji = "✅"
                                    status_msg = " (Rechargement réussi)"
                                else:
                                    status_msg = f" (Rechargement effectué mais bougie toujours > 60 min: {h1_age_minutes:.0f} min)"
                else:
                    status_emoji = "⚠️"
                    status_msg = ""
                
                if h1_age_minutes > 90:
                    # Vérifier si le marché est ouvert
                    symbol_info = mt5.symbol_info(symbol)
                    if symbol_info:
                        # Vérifier les heures de trading (certaines versions MT5 n'ont pas SYMBOL_TRADE_MODE_CLOSE_ONLY)
                        trade_mode = symbol_info.trade_mode
                        mode_disabled = getattr(mt5, 'SYMBOL_TRADE_MODE_DISABLED', 0)
                        mode_close_only = getattr(mt5, 'SYMBOL_TRADE_MODE_CLOSE_ONLY', None)
                        if trade_mode == mode_disabled:
                            status_msg = " (Marché fermé - SYMBOL_TRADE_MODE_DISABLED)"
                        elif mode_close_only is not None and trade_mode == mode_close_only:
                            status_msg = " (Marché en clôture uniquement)"
                        else:
                            # Forcer un rechargement des données H1
                            self.log(f"   🔄 Tentative de rechargement des données H1 (dernière bougie trop ancienne: {h1_age_minutes:.0f} min)")
                            try:
                                df_h1_new = self.load_h1_data(symbol)
                                if df_h1_new is not None and len(df_h1_new) > 0:
                                    last_h1_time_new = df_h1_new.index[-1]
                                    if hasattr(last_h1_time_new, 'to_pydatetime'):
                                        last_h1_time_new_py = last_h1_time_new.to_pydatetime()
                                    else:
                                        last_h1_time_new_py = last_h1_time_new
                                    h1_age_new = (current_time - last_h1_time_new_py).total_seconds() / 60
                                    if h1_age_new < h1_age_minutes:
                                        self.h1_data[symbol] = df_h1_new
                                        last_h1_time_py = last_h1_time_new_py
                                        h1_age_minutes = h1_age_new
                                        self.log(f"   ✅ Données H1 mises à jour (nouvelle dernière bougie: {last_h1_time_py.strftime('%H:%M:%S')} UTC, il y a {h1_age_minutes:.0f} min)")
                                    else:
                                        status_msg = " (Rechargement n'a pas amélioré les données - possible problème de synchronisation MT5)"
                            except Exception as e:
                                status_msg = f" (Erreur rechargement: {e})"
                
                self.log(f"   📊 H1: {status_emoji} Données H1 disponibles ({len(df_h1_check)} bougies) | Dernière bougie H1: {last_h1_time_py.strftime('%H:%M:%S')} UTC (il y a {h1_age_minutes:.0f} min){status_msg}")
            else:
                self.log(f"   📊 H1: ❌ Données H1 indisponibles ou insuffisantes (df_h1={'None' if df_h1_check is None else len(df_h1_check)} bougies)")
        
        # Si on est en session OFF_HOURS, indiquer que le trading est bloqué
        if session == TradingSession.OFF_HOURS:
            self.log(f"   ⏸️  Trading bloqué: Session OFF_HOURS (21:00-00:00 UTC)")
        
        # Afficher les positions ouvertes (plusieurs positions possibles)
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            our_positions = [pos for pos in positions if pos.magic == self.magic_number]
            if our_positions:
                self.log(f"   📍 {len(our_positions)} position(s) ouverte(s) pour {symbol}:")
                for pos in our_positions:
                    pos_type = "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT"
                    self.log(f"      - {pos_type} (Ticket: {pos.ticket}, Profit: {pos.profit:.2f})")
        
        # Cooldown désactivé (comme en backtest)
        # if self.is_in_cooldown(current_time):
        #     print(f"   ⏸️  Cooldown actif après perte - attente de {COOLDOWN_AFTER_LOSS} bougies")
        #     return
        
        # FILTRES DÉSACTIVÉS (comme en backtest actuel)
        # FILTRE 1: La bougie doit clôturer au-dessus ou en-dessous de l'EMA 20
        # if not self.check_ema_slope(df):
        #     print(f"   ⏸️  Pas de signal (filtre EMA slope)")
        #     return
        
        # FILTRE 2: Volatilité (éviter marchés compressés)
        # if not self.check_atr_filter(df):
        #     print(f"   ⏸️  Marché en range (ATR) - pas de signal")
        #     return
        
        # Vérifier les conditions d'entrée
        long_signal = self.check_long_entry(df, symbol)
        short_signal = self.check_short_entry(df, symbol)
        
        # Log détaillé pour diagnostic (df = barres fermées uniquement → iloc[-1]/[-2])
        if not long_signal and not short_signal:
            current = df.iloc[-1]
            prev = df.iloc[-2]
            ema20_curr = current[f'EMA_{EMA_FAST}']
            sma50_curr = current[f'SMA_{SMA_SLOW}']
            ema20_prev = prev[f'EMA_{EMA_FAST}']
            sma50_prev = prev[f'SMA_{SMA_SLOW}']
            cross_long = (ema20_prev < sma50_prev) and (ema20_curr > sma50_curr)
            cross_short = (ema20_prev > sma50_prev) and (ema20_curr < sma50_curr)
            self.log(f"   📊 Signaux: LONG: {'✅' if long_signal else '❌'} | SHORT: {'✅' if short_signal else '❌'}")
            if cross_long or cross_short:
                self.log(f"   🔍 Croisement M5 détecté (LONG={cross_long}, SHORT={cross_short}) → signal bloqué par filtre H1 (tendance)")
            else:
                self.log(f"   🔍 Pas de croisement EMA20/SMA50 sur cette bougie → en attente de signal")
        else:
            self.log(f"   📊 Signaux: LONG: {'✅' if long_signal else '❌'} | SHORT: {'✅' if short_signal else '❌'}")
        
        # Parité backtest: LONG puis SHORT, pas de elif (les deux signaux peuvent être traités sur la même bougie)
        if long_signal:
            if self.has_open_position_on_other_symbol(symbol):
                self.log(f"   ⏸️  Un autre actif a déjà des positions ouvertes - un seul actif à la fois (règle stratégie)")
                long_signal = False
            else:
                self.log(f"   🟢 Signal LONG détecté - tentative d'ouverture...")
                try:
                    trade = self.open_long_position(symbol, df, entry_price_bar_override=current_open)
                    if trade is None:
                        self.log(f"   ⚠️  Échec d'ouverture LONG {symbol} (vérifications de sécurité)")
                except Exception as e:
                    self.log_failed_trade_attempt(
                        symbol=symbol,
                        trade_type="LONG",
                        reason="Exception inattendue",
                        error_message=str(e)
                    )
                    self.log(f"   ❌ Exception lors de l'ouverture LONG {symbol}: {e}")

        if short_signal:
            if self.has_open_position_on_other_symbol(symbol):
                self.log(f"   ⏸️  Un autre actif a déjà des positions ouvertes - un seul actif à la fois (règle stratégie)")
                short_signal = False
            else:
                self.log(f"   🔴 Signal SHORT détecté - tentative d'ouverture...")
                try:
                    trade = self.open_short_position(symbol, df, entry_price_bar_override=current_open)
                    if trade is None:
                        self.log(f"   ⚠️  Échec d'ouverture SHORT {symbol} (vérifications de sécurité)")
                except Exception as e:
                    self.log_failed_trade_attempt(
                        symbol=symbol,
                        trade_type="SHORT",
                        reason="Exception inattendue",
                        error_message=str(e)
                    )
                    self.log(f"   ❌ Exception lors de l'ouverture SHORT {symbol}: {e}")
        if not long_signal and not short_signal:
            self.log(f"   ⏸️  Aucun signal d'entrée valide")
    
    def log_open_positions(self):
        """Affiche toutes les positions ouvertes actuellement"""
        if not self.check_connection():
            return
        
        all_positions = mt5.positions_get()
        if all_positions is None:
            return
        
        our_positions = [pos for pos in all_positions if pos.magic == self.magic_number]
        
        if not our_positions:
            self.log("\n📋 POSITIONS OUVERTES: Aucune")
            return
        
        self.log(f"\n📋 POSITIONS OUVERTES: {len(our_positions)} position(s)")
        self.log("-" * 70)
        
        account_info = mt5.account_info()
        currency = account_info.currency if account_info else "USD"
        
        total_profit = 0.0
        positions_by_symbol = {}
        
        for pos in our_positions:
            if pos.symbol not in positions_by_symbol:
                positions_by_symbol[pos.symbol] = []
            positions_by_symbol[pos.symbol].append(pos)
            total_profit += pos.profit
        
        for symbol, positions in positions_by_symbol.items():
            self.log(f"\n{symbol}: {len(positions)} position(s)")
            for pos in positions:
                pos_type = "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT"
                profit_pct = ((pos.profit / (pos.price_open * pos.volume * 100000)) * 100) if pos.volume > 0 else 0
                self.log(f"  • {pos_type} | Ticket: {pos.ticket}")
                self.log(f"    Entry: {pos.price_open:.2f} | SL: {pos.sl:.2f} | TP: {pos.tp:.2f}")
                self.log(f"    Volume: {pos.volume} | Profit: {pos.profit:.2f} {currency} ({profit_pct:+.2f}%)")
        
        self.log(f"\n💰 Profit total: {total_profit:.2f} {currency}")
        self.log("-" * 70)
    
    def display_status(self):
        """Affiche le statut actuel"""
        self.log("\n" + "=" * 70)
        self.log(f"📊 STATUT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log("=" * 70)
        
        # Infos compte
        account_info = mt5.account_info()
        if account_info:
            # Réinitialiser l'equity de début si nouveau jour
            current_date = datetime.now().date()
            if self.daily_start_equity is None:
                self.daily_start_equity = account_info.equity
                self.trading_stopped_daily = False
            
            # Calculer la perte quotidienne
            daily_loss = self.get_daily_loss()
            loss_pct = (daily_loss / self.daily_start_balance * 100) if self.daily_start_balance and self.daily_start_balance > 0 else 0
            
            self.log(f"\n💰 Compte:")
            self.log(f"   Balance: {account_info.balance:.2f} {account_info.currency}")
            self.log(f"   Equity: {account_info.equity:.2f} {account_info.currency}")
            self.log(f"   Profit: {account_info.profit:.2f} {account_info.currency}")
            self.log(f"\n📅 Performance Quotidienne:")
            if self.daily_start_equity is None:
                self.log(f"   ⚠️  Equity début: NON INITIALISÉ (problème détecté!)")
                # Initialiser immédiatement
                self.daily_start_equity = account_info.equity
                self.last_trading_date = datetime.now().date()
                daily_loss = 0.0
                loss_pct = 0.0
                self.log(f"   ✅ Equity début initialisé: {self.daily_start_equity:.2f} {account_info.currency}")
            else:
                self.log(f"   Equity début: {self.daily_start_equity:.2f} {account_info.currency}")
            self.log(f"   Perte quotidienne: {daily_loss:.2f} {account_info.currency} ({loss_pct:.2f}%)")
            self.log(f"   Limite: {self.max_daily_loss:.2f} {account_info.currency}")
            if self.trading_stopped_daily:
                self.log(f"   🛡️  Trading arrêté pour la journée (limite atteinte)")
                remaining = 0.0
            else:
                # Calcul correct de la marge restante
                # max_daily_loss = -250 (limite de perte)
                # daily_loss = equity_actuelle - equity_début (négatif si perte, positif si gain) - FTMO utilise equity
                # 
                # Exemples:
                # - daily_loss = 0 (pas de perte) : marge = 250 (limite complète disponible)
                # - daily_loss = -100 (perte de 100) : marge = 250 - 100 = 150 (il reste 150)
                # - daily_loss = -250 (perte de 250) : marge = 0 (limite atteinte)
                # - daily_loss = -300 (perte de 300) : marge = 0 (limite dépassée, devrait être arrêté)
                
                if daily_loss >= 0:
                    # Pas de perte (gain ou équilibre), marge complète disponible
                    remaining = abs(self.max_daily_loss)
                else:
                    # On a une perte, calculer ce qui reste
                    remaining = abs(self.max_daily_loss) - abs(daily_loss)
                    # S'assurer que remaining n'est pas négatif
                    if remaining < 0:
                        remaining = 0.0
                
                self.log(f"   ✅ Marge restante: {remaining:.2f} {account_info.currency}")
        
        # Afficher le résumé des échecs de trades
        self.log(f"\n{self.get_failed_trades_summary()}")
        
        # Déterminer l'actif du jour (si stratégie "actif du jour" activée)
        preferred_symbol = self.get_preferred_symbol_for_today() if self.use_daily_preferred_symbol else None
        
        for symbol in self.symbols:
            # Il faut assez de barres pour que SMA50 soit valide sur iloc[-2] (rolling(50) a besoin de 50 lignes avant)
            df = self.get_market_data(symbol, count=max(300, SMA_SLOW + 10))
            if df is None or len(df) < SMA_SLOW + 2:
                continue
            
            # Après sort_index: iloc[-2]=dernière barre fermée (indicateurs valides avec count >= SMA_SLOW+10)
            current = df.iloc[-2]
            price = current['close']
            ema20 = current[f'EMA_{EMA_FAST}']
            sma50 = current[f'SMA_{SMA_SLOW}']
            
            # Indiquer si c'est l'actif du jour ou non
            is_today_symbol = (preferred_symbol == symbol) if preferred_symbol else True
            if preferred_symbol:
                status_prefix = "📅 Actif du jour" if is_today_symbol else "⏸ Non tradé aujourd'hui"
            else:
                status_prefix = ""  # Pas de stratégie "actif du jour" → tous les actifs sont traités
            
            self.log(f"\n{symbol}: {status_prefix}")
            self.log(f"  Prix: {price:.2f}")
            self.log(f"  EMA {EMA_FAST}: {ema20:.2f}")
            self.log(f"  SMA {SMA_SLOW}: {sma50:.2f}")
            
            # Vérifier les positions ouvertes (plusieurs positions possibles)
            positions = mt5.positions_get(symbol=symbol)
            our_positions = [pos for pos in positions if pos.magic == self.magic_number] if positions else []
            
            if our_positions:
                self.log(f"  ✅ {len(our_positions)} position(s) ouverte(s):")
                for pos in our_positions:
                    pos_type = "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT"
                    self.log(f"     - {pos_type} (Ticket: {pos.ticket}, Entry: {pos.price_open:.2f}, Profit: {pos.profit:.2f} {account_info.currency if account_info else ''})")
            else:
                if is_today_symbol:
                    bias = "HAUSSIER" if price > sma50 else "BAISSIER" if price < sma50 else "NEUTRE"
                    self.log(f"  ⏸ En attente - Biais: {bias}")
                else:
                    self.log(f"  ⏸ Non tradé aujourd'hui (actif du jour: {preferred_symbol})")
        
        self.log("\n" + "=" * 70)
    
    def run(self, update_interval: int = 300):
        """Lance le bot en mode continu"""
        self.log(f"\n🚀 Démarrage du bot [{self.account_name}] (mise à jour toutes les {update_interval} secondes)")
        self.log(f"   Magic number: {self.magic_number}")
        self.log("   Appuyez sur Ctrl+C pour arrêter\n")
        
        iteration = 0
        last_status_time = None
        
        try:
            while True:
                iteration += 1
                self._current_iteration = iteration  # Pour le logging des échecs
                current_time = datetime.now()
                timestamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
                
                # Vérifier si nouveau jour et réinitialiser si nécessaire
                current_date = current_time.date()
                if self.last_trading_date is not None and current_date > self.last_trading_date:
                    self.get_daily_loss()  # Cela réinitialisera automatiquement
                
                self.log(f"\n{'='*70}")
                self.log(f"🔄 Itération #{iteration} - {timestamp}")
                self.log(f"{'='*70}")
                
                # Vérifier la connexion au début de chaque itération
                if not self.check_connection():
                    self.log("⚠️  Connexion MT5 perdue, attente avant nouvelle tentative...")
                    time.sleep(60)  # Attendre 1 minute avant de réessayer
                    continue
                
                # Afficher les positions ouvertes au début de chaque itération
                self.log_open_positions()
                
                # Afficher le compteur d'échecs de trades
                if self.failed_trade_attempts > 0:
                    self.log(f"\n🚨 Échecs de trades: {self.failed_trade_attempts}")
                
                # Vérifier la protection quotidienne UNE FOIS au début de l'itération
                # Cela permet à tous les symboles d'être évalués dans la même itération
                # avant que la protection ne soit déclenchée (si elle l'est pendant le traitement)
                can_trade_globally, global_reason = self.can_trade_today()
                
                # Un seul actif tradé par jour (config PREFERRED_SYMBOL_BY_DAY) — recalculé à chaque cycle, donc mise à jour auto après minuit
                symbols_to_process = self.symbols
                preferred = self.get_preferred_symbol_for_today()
                day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
                current_weekday = datetime.now().weekday()
                day_name = day_names[current_weekday]
                if self.last_run_weekday is not None and current_weekday != self.last_run_weekday:
                    self.log(f"\n🕐 Nouveau jour détecté (minuit passé) → {day_name}, actif du jour: {preferred or 'tous'}")
                self.last_run_weekday = current_weekday
                if self.use_daily_preferred_symbol and preferred is not None:
                    symbols_to_process = [preferred]
                    self.log(f"\n📅 Jour: {day_name} → Actif tradé aujourd'hui: {preferred}")
                else:
                    self.log(f"\n📅 Jour: {day_name} → Actifs traités: {', '.join(symbols_to_process)}")
                
                for symbol in symbols_to_process:
                    try:
                        # Vérifier à nouveau la protection pour ce symbole (au cas où elle aurait été déclenchée)
                        # mais permettre à tous les symboles d'être traités dans la même itération
                        can_trade, reason = self.can_trade_today()
                        if not can_trade:
                            # Si la protection est déclenchée, on peut quand même afficher le statut du symbole
                            # mais ne pas essayer d'ouvrir de nouveaux trades
                            self.log(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 Analyse de {symbol}...")
                            self.log(f"   🛡️  {reason}")
                            # Log de diagnostic pour la protection quotidienne
                            daily_loss = self.get_daily_loss()
                            account_info = mt5.account_info()
                            currency = account_info.currency if account_info else "USD"
                            self.log(f"   📊 Perte quotidienne: {daily_loss:.2f} {currency} | Limite: {self.max_daily_loss:.2f} {currency}")
                            continue
                        
                        self.process_symbol(symbol)
                    except Exception as e:
                        self.log(f"❌ Erreur traitement {symbol}: {e}")
                        import traceback
                        traceback.print_exc()
                        # Continuer avec les autres symboles même en cas d'erreur
                
                # Afficher le statut toutes les 5 minutes ou à la première itération
                should_show_status = False
                if last_status_time is None:
                    should_show_status = True
                elif (current_time - last_status_time).total_seconds() >= 300:
                    should_show_status = True
                
                if should_show_status:
                    self.display_status()
                    last_status_time = current_time
                
                # Afficher le compte à rebours
                self.log(f"\n⏳ Prochaine vérification dans {update_interval} secondes...")
                self.log(f"   (Appuyez sur Ctrl+C pour arrêter)")
                
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            self.log("\n\n⏹️  Arrêt du bot demandé par l'utilisateur")
            self.log_open_positions()
            self.display_status()
            mt5.shutdown()
            self.session_logger.close()
            self.log("\n✅ Bot arrêté")
    
    def __del__(self):
        """Fermeture propre de MT5"""
        if hasattr(self, 'session_logger'):
            self.session_logger.close()
        mt5.shutdown()


def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description="EMA Trading Bot MT5 - Trading réel")
    parser.add_argument("--login", type=int, required=True,
                       help="Numéro de compte MT5")
    parser.add_argument("--password", type=str, required=True,
                       help="Mot de passe MT5")
    parser.add_argument("--server", type=str, required=True,
                       help="Serveur MT5 (ex: MetaQuotes-Demo, broker-Server)")
    parser.add_argument("--symbols", nargs="+", default=["US30", "NAS100"],
                       help="Symboles à trader (défaut: US30 NAS100)")
    parser.add_argument("--risk", type=float, default=1.0,
                       help="Pourcentage de risque par trade (défaut: 1.0%%)")
    parser.add_argument("--interval", type=int, default=300,
                       help="Intervalle entre vérifications en secondes (défaut: 300 = 5min)")
    parser.add_argument("--once", action="store_true",
                       help="Une seule analyse (pas de monitoring continu)")
    
    args = parser.parse_args()
    
    # Créer le bot
    bot = MT5TradingBot(
        login=args.login,
        password=args.password,
        server=args.server,
        symbols=args.symbols,
        risk_percent=args.risk
    )
    
    if args.once:
        # Une seule analyse
        for symbol in args.symbols:
            bot.process_symbol(symbol)
        bot.display_status()
    else:
        # Mode continu
        bot.run(update_interval=args.interval)


if __name__ == "__main__":
    main()
