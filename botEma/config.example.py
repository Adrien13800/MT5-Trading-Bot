"""
Configuration pour le bot MT5 (PRODUCTION)
COPIEZ ce fichier en config.py et remplissez vos identifiants
NE COMMITEZ JAMAIS config.py dans git!
"""

# Identifiants MT5 (à remplir avec vos vraies valeurs)
MT5_LOGIN = 123456789           # Votre numéro de compte
MT5_PASSWORD = "CHANGE_ME"      # Votre mot de passe
MT5_SERVER = "Broker-Server"    # Nom du serveur (ex: "FTMO-Server3", "MetaQuotes-Demo")

# Symboles à trader (vérifiez les noms exacts dans MT5)
SYMBOLS = ["US30.cash", "US100.cash", "US500.cash"]

# Stratégie "actif du jour" + un seul actif à la fois
USE_DAILY_PREFERRED_SYMBOL = True   # Ne trader que l'actif préféré ce jour-là
ONE_SYMBOL_AT_A_TIME = True         # Ne jamais avoir 2 actifs en position simultanément

# Actif à trader par jour (0=Lundi, 1=Mardi, 2=Mercredi, 3=Jeudi, 4=Vendredi)
# Basé sur le WR du backtest — à adapter selon vos résultats
PREFERRED_SYMBOL_BY_DAY = {
    0: "US30.cash",   # Lundi
    1: "US100.cash",  # Mardi
    2: "US500.cash",  # Mercredi
    3: "US30.cash",   # Jeudi
    4: "US500.cash",  # Vendredi
}

# Paramètres de trading
RISK_PERCENT = 0.5  # % de capital risqué par trade (0.5% = risque réduit)

# Protection quotidienne FTMO
MAX_DAILY_LOSS = -250.0  # Arrêter le trading si perte quotidienne atteint cette valeur

# Intervalle de vérification (en secondes)
UPDATE_INTERVAL = 60  # 60 = 1 minute
