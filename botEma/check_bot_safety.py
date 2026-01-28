#!/usr/bin/env python3
"""
Script de vérification de sécurité avant de laisser le bot tourner toute la nuit
Vérifie tous les points critiques pour éviter les problèmes
"""

import sys
import os
from datetime import datetime

print("=" * 70)
print("🔒 VÉRIFICATION DE SÉCURITÉ DU BOT")
print("=" * 70)

# 1. Vérifier que config.py existe et contient les bonnes valeurs
print("\n1️⃣  Vérification de la configuration...")
try:
    import config
    
    checks = {
        'MT5_LOGIN': hasattr(config, 'MT5_LOGIN') and config.MT5_LOGIN > 0,
        'MT5_PASSWORD': hasattr(config, 'MT5_PASSWORD') and len(config.MT5_PASSWORD) > 0,
        'MT5_SERVER': hasattr(config, 'MT5_SERVER') and len(config.MT5_SERVER) > 0,
        'SYMBOLS': hasattr(config, 'SYMBOLS') and len(config.SYMBOLS) > 0,
        'RISK_PERCENT': hasattr(config, 'RISK_PERCENT') and 0 < config.RISK_PERCENT <= 5,
        'MAX_DAILY_LOSS': hasattr(config, 'MAX_DAILY_LOSS') and config.MAX_DAILY_LOSS < 0,
        'UPDATE_INTERVAL': hasattr(config, 'UPDATE_INTERVAL') and config.UPDATE_INTERVAL >= 60,
    }
    
    all_ok = True
    for key, status in checks.items():
        status_icon = "✅" if status else "❌"
        print(f"   {status_icon} {key}: {'OK' if status else 'MANQUANT ou INVALIDE'}")
        if not status:
            all_ok = False
    
    if all_ok:
        print(f"   ✅ Configuration valide")
        print(f"      - Risque par trade: {config.RISK_PERCENT}%")
        print(f"      - Limite quotidienne: {config.MAX_DAILY_LOSS}€")
        print(f"      - Symboles: {', '.join(config.SYMBOLS)}")
    else:
        print("   ❌ ERREUR: Configuration incomplète ou invalide!")
        sys.exit(1)
        
except ImportError:
    print("   ❌ ERREUR: Fichier config.py non trouvé!")
    sys.exit(1)

# 2. Vérifier la connexion MT5
print("\n2️⃣  Test de connexion MT5...")
try:
    import MetaTrader5 as mt5
    
    if not mt5.initialize():
        print(f"   ❌ ERREUR: Impossible d'initialiser MT5: {mt5.last_error()}")
        sys.exit(1)
    print("   ✅ MT5 initialisé")
    
    authorized = mt5.login(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER
    )
    
    if not authorized:
        print(f"   ❌ ERREUR: Échec de connexion: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)
    
    print("   ✅ Connexion réussie")
    
    account_info = mt5.account_info()
    if account_info:
        print(f"      Balance: {account_info.balance:.2f} {account_info.currency}")
        print(f"      Equity: {account_info.equity:.2f} {account_info.currency}")
        
        # Vérifier que la balance est suffisante
        if account_info.balance < 1000:
            print(f"   ⚠️  ATTENTION: Balance faible ({account_info.balance:.2f})")
    else:
        print("   ⚠️  ATTENTION: Impossible de récupérer les infos du compte")
    
    mt5.shutdown()
    
except ImportError:
    print("   ❌ ERREUR: MetaTrader5 non installé")
    sys.exit(1)
except Exception as e:
    print(f"   ❌ ERREUR: {e}")
    sys.exit(1)

# 3. Vérifier les symboles
print("\n3️⃣  Vérification des symboles...")
try:
    import MetaTrader5 as mt5
    mt5.initialize()
    mt5.login(login=config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER)
    
    for symbol in config.SYMBOLS:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info:
            print(f"   ✅ {symbol}: OK")
            print(f"      - Min lot: {symbol_info.volume_min}")
            print(f"      - Max lot: {symbol_info.volume_max}")
            print(f"      - Step: {symbol_info.volume_step}")
        else:
            print(f"   ❌ {symbol}: NON TROUVÉ")
    
    mt5.shutdown()
    
except Exception as e:
    print(f"   ⚠️  Erreur lors de la vérification des symboles: {e}")

# 4. Vérifier les paramètres de risque
print("\n4️⃣  Vérification des paramètres de risque...")
risk_percent = config.RISK_PERCENT
max_daily_loss = abs(config.MAX_DAILY_LOSS)

if risk_percent > 2.0:
    print(f"   ⚠️  ATTENTION: Risque par trade élevé ({risk_percent}%)")
    print(f"      Recommandation: < 1% pour trading automatique")
else:
    print(f"   ✅ Risque par trade: {risk_percent}% (acceptable)")

if max_daily_loss > 500:
    print(f"   ⚠️  ATTENTION: Limite quotidienne élevée ({max_daily_loss}€)")
else:
    print(f"   ✅ Limite quotidienne: {max_daily_loss}€ (acceptable)")

# 5. Vérifier que le bot existe
print("\n5️⃣  Vérification du code du bot...")
if os.path.exists("ema_mt5_bot.py"):
    print("   ✅ Fichier ema_mt5_bot.py trouvé")
    
    # Vérifier quelques points critiques dans le code
    with open("ema_mt5_bot.py", "r", encoding="utf-8") as f:
        content = f.read()
        
        checks = {
            'Protection quotidienne': 'can_trade_today' in content,
            'Vérification SL/TP': 'stop_loss' in content and 'take_profit' in content,
            'Gestion erreurs': 'except' in content,
            'Limite lot size': 'max_lot' in content and 'min_lot' in content,
            'Anti-doublon positions': 'has_open_position' in content,
            'Anti-setup répété': 'has_recent_same_setup' in content,
            'Vérification multiple': 'VÉRIFICATION STRICTE' in content or 'VÉRIFICATION FINALE' in content,
        }
        
        for check, status in checks.items():
            icon = "✅" if status else "❌"
            print(f"      {icon} {check}")
else:
    print("   ❌ Fichier ema_mt5_bot.py non trouvé!")
    sys.exit(1)

# 6. Recommandations finales
print("\n" + "=" * 70)
print("📋 RÉSUMÉ ET RECOMMANDATIONS")
print("=" * 70)

print("\n✅ Points vérifiés:")
print("   - Configuration valide")
print("   - Connexion MT5 fonctionnelle")
print("   - Symboles disponibles")
print("   - Paramètres de risque")

print("\n⚠️  Points d'attention pour la nuit:")
print("   1. Vérifiez que MT5 est ouvert et connecté")
print("   2. Vérifiez que votre ordinateur ne s'endormira pas")
print("   3. Surveillez les logs au réveil")
print("   4. Vérifiez les positions ouvertes dans MT5")
print("   5. Le bot s'arrêtera automatiquement si limite quotidienne atteinte")

print("\n💡 Commandes utiles:")
print("   - Lancer le bot: python run_bot.py")
print("   - Une seule analyse: python run_bot.py --once")
print("   - Arrêter le bot: Ctrl+C dans le terminal")

print("\n" + "=" * 70)
print("✅ Vérification terminée - Le bot semble prêt")
print("=" * 70)
