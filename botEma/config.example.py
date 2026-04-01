"""
Configuration pour le bot MT5 (PRODUCTION)
COPIEZ ce fichier en config.py et remplissez vos identifiants
NE COMMITEZ JAMAIS config.py dans git!

MULTI-COMPTE : Définissez autant de comptes que nécessaire dans le dict ACCOUNTS.
Lancez le bot avec :  python run_bot.py --account ftmo
                      python run_bot.py --account vtmarkets
"""

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# ============================================================================
# NOTIFICATIONS TELEGRAM
# ============================================================================
# Remplir les valeurs dans le fichier .env (voir .env.example)
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================================
# COMPTES MT5 — Un bloc par broker / compte
# ============================================================================
ACCOUNTS = {

    # ---- Compte FTMO (ou autre prop firm) ----
    "ftmo": {
        "MT5_LOGIN": 123456789,
        "MT5_PASSWORD": "CHANGE_ME",
        "MT5_SERVER": "FTMO-Server3",
        "MT5_TERMINAL_PATH": r"C:\Program Files\MetaTrader 5\terminal64.exe",

        "SYMBOLS": ["US30.cash", "US100.cash", "US500.cash"],

        "RISK_PERCENT": 0.5,
        "MAX_DAILY_LOSS": -250.0,
        "UPDATE_INTERVAL": 60,

        "MAGIC_NUMBER": 100001,
        "TRADE_COMMENT": "EMA_FTMO",

        "USE_DAILY_PREFERRED_SYMBOL": True,
        "ONE_SYMBOL_AT_A_TIME": True,
        "PREFERRED_SYMBOL_BY_DAY": {
            0: "US30.cash",  # Lundi
            1: "US100.cash",  # Mardi
            2: "US500.cash",  # Mercredi
            3: "US30.cash",  # Jeudi
            4: "US500.cash",  # Vendredi
        },
    },
    # ---- Compte VT Markets ----
    "vtmarkets": {
        "MT5_LOGIN": 987654321,
        "MT5_PASSWORD": "CHANGE_ME",
        "MT5_SERVER": "VTMarketsSC-Live",
        "MT5_TERMINAL_PATH": r"C:\Program Files\MetaTrader 5 VTMarkets\terminal64.exe",
        "SYMBOLS": ["US30", "US100", "US500"],
        "RISK_PERCENT": 0.5,
        "MAX_DAILY_LOSS": -250.0,
        "UPDATE_INTERVAL": 60,
        "MAGIC_NUMBER": 200001,
        "TRADE_COMMENT": "EMA_VTM",
        "USE_DAILY_PREFERRED_SYMBOL": False,
        "ONE_SYMBOL_AT_A_TIME": False,
        "PREFERRED_SYMBOL_BY_DAY": {},
    },
}

# ============================================================================
# VALEURS PAR DÉFAUT (utilisées si on lance sans --account, rétro-compatible)
# ============================================================================
MT5_LOGIN = ACCOUNTS["ftmo"]["MT5_LOGIN"]
MT5_PASSWORD = ACCOUNTS["ftmo"]["MT5_PASSWORD"]
MT5_SERVER = ACCOUNTS["ftmo"]["MT5_SERVER"]
MT5_TERMINAL_PATH = ACCOUNTS["ftmo"]["MT5_TERMINAL_PATH"]
SYMBOLS = ACCOUNTS["ftmo"]["SYMBOLS"]
RISK_PERCENT = ACCOUNTS["ftmo"]["RISK_PERCENT"]
MAX_DAILY_LOSS = ACCOUNTS["ftmo"]["MAX_DAILY_LOSS"]
UPDATE_INTERVAL = ACCOUNTS["ftmo"]["UPDATE_INTERVAL"]
USE_DAILY_PREFERRED_SYMBOL = ACCOUNTS["ftmo"]["USE_DAILY_PREFERRED_SYMBOL"]
ONE_SYMBOL_AT_A_TIME = ACCOUNTS["ftmo"]["ONE_SYMBOL_AT_A_TIME"]
PREFERRED_SYMBOL_BY_DAY = ACCOUNTS["ftmo"]["PREFERRED_SYMBOL_BY_DAY"]
