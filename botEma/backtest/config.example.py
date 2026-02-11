"""
Configuration pour le backtest
COPIEZ ce fichier en config.py et remplissez vos identifiants
NE COMMITEZ JAMAIS config.py dans git!
"""

# Identifiants MT5 (nécessaires pour récupérer les données historiques)
MT5_LOGIN = 123456789           # Votre numéro de compte
MT5_PASSWORD = "CHANGE_ME"      # Votre mot de passe
MT5_SERVER = "Broker-Server"    # Nom du serveur

# Les 3 actifs à backtester (données chargées pour tous)
SYMBOLS = ["US30.cash", "US100.cash", "US500.cash"]

# Stratégie "actif du jour" + un seul actif à la fois (comme en prod)
USE_DAILY_PREFERRED_SYMBOL = True   # Ne trader que l'actif préféré ce jour-là
ONE_SYMBOL_AT_A_TIME = True         # Ne jamais avoir 2 actifs en position simultanément

# Actif à trader par jour (0=Lundi, 1=Mardi, 2=Mercredi, 3=Jeudi, 4=Vendredi)
# Même structure que config.py (prod) — garder aligné pour prod/backtest
PREFERRED_SYMBOL_BY_DAY = {
    0: "US30.cash",   # Lundi
    1: "US100.cash",  # Mardi
    2: "US500.cash",  # Mercredi
    3: "US30.cash",   # Jeudi
    4: "US500.cash",  # Vendredi
}

# Paramètres de trading (IDENTIQUES à la prod)
RISK_PERCENT = 0.5  # % de capital risqué par trade

# Protection quotidienne FTMO
MAX_DAILY_LOSS = -250.0  # Arrêter le trading si perte quotidienne atteint cette valeur

# Paramètres de backtest
INITIAL_BALANCE = 10000.0  # Balance de départ pour le backtest
YEARS_BACK = 3             # Nombre d'années de données historiques (si USE_ALL_AVAILABLE_DATA=False)
USE_ALL_AVAILABLE_DATA = True  # Si True, récupère TOUTES les données disponibles
# Limiter la période du backtest aux N derniers mois (None ou 0 = utiliser toute la période chargée)
MONTHS_BACK = 8  # Ex: 8 = backtest sur les 8 derniers mois seulement
# Appliquer la limite de perte quotidienne en backtest (False = ignorer → backtest sur toute la période)
USE_DAILY_LOSS_IN_BACKTEST = False
