#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de vérification de la configuration MT5
Vérifie que MT5 est correctement installé et configuré pour l'automatisation
"""

import sys
import os

# Fix encoding pour Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import MetaTrader5 as mt5
except ImportError:
    print("❌ MetaTrader5 n'est pas installé")
    print("   Installez-le avec: pip install MetaTrader5")
    sys.exit(1)

def check_mt5_installation():
    """Vérifie si MT5 est installé"""
    print("=" * 70)
    print("🔍 VÉRIFICATION DE L'INSTALLATION MT5")
    print("=" * 70)
    
    # Chemins possibles de MT5 sur Windows
    possible_paths = [
        r"C:\Program Files\MetaTrader 5\terminal64.exe",  # Chemin standard
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal\*\terminal64.exe"),
    ]
    
    print("\n📁 Recherche de MetaTrader 5...")
    found = False
    
    # Essayer d'initialiser sans chemin
    if mt5.initialize():
        print("   ✅ MT5 trouvé et initialisé avec succès")
        version = mt5.version()
        if version:
            print(f"   Version: {version[0]}.{version[1]}.{version[2]} (build {version[3]})")
        mt5.shutdown()
        return True
    
    # Si échec, essayer avec des chemins spécifiques
    for path in possible_paths:
        if '*' in path:
            # Pattern avec wildcard - chercher dans le répertoire
            import glob
            matches = glob.glob(path)
            for match in matches:
                print(f"   🔍 Essai avec: {match}")
                if mt5.initialize(path=match):
                    print(f"   ✅ MT5 trouvé: {match}")
                    version = mt5.version()
                    if version:
                        print(f"   Version: {version[0]}.{version[1]}.{version[2]} (build {version[3]})")
                    mt5.shutdown()
                    return True
        else:
            if os.path.exists(path):
                print(f"   🔍 Essai avec: {path}")
                if mt5.initialize(path=path):
                    print(f"   ✅ MT5 trouvé: {path}")
                    version = mt5.version()
                    if version:
                        print(f"   Version: {version[0]}.{version[1]}.{version[2]} (build {version[3]})")
                    mt5.shutdown()
                    return True
    
    print("   ❌ MT5 non trouvé aux emplacements standards")
    print("\n   💡 Solutions:")
    print("   1. Installez MetaTrader 5 depuis https://www.metatrader5.com/")
    print("   2. Ou spécifiez le chemin manuellement dans le code")
    return False

def check_mt5_authorization():
    """Vérifie l'autorisation MT5"""
    print("\n" + "=" * 70)
    print("🔐 VÉRIFICATION DE L'AUTORISATION MT5")
    print("=" * 70)
    
    if not mt5.initialize():
        error = mt5.last_error()
        print(f"\n❌ Erreur d'initialisation: {error}")
        
        if error[0] == -6:
            print("\n⚠️  PROBLÈME D'AUTORISATION DÉTECTÉ")
            print("\n📋 INSTRUCTIONS DÉTAILLÉES:")
            print("\n1️⃣  Ouvrez MetaTrader 5 manuellement")
            print("   - Double-cliquez sur l'icône MT5")
            print("   - Attendez qu'il se connecte à votre compte")
            
            print("\n2️⃣  Activez le trading algorithmique:")
            print("   - Menu: Outils → Options")
            print("   - (ou Tools → Options en anglais)")
            print("   - Onglet: Expert Advisors")
            print("   - (ou Algorithmic Trading en anglais)")
            
            print("\n3️⃣  Cochez les cases suivantes:")
            print("   ☑ Autoriser le trading algorithmique")
            print("     (Allow automated trading)")
            print("   ☑ Autoriser l'importation de DLL (optionnel)")
            print("     (Allow DLL imports)")
            
            print("\n4️⃣  Activez AutoTrading dans MT5:")
            print("   - Cherchez le bouton 'AutoTrading' en haut de MT5")
            print("   - Il doit être VERT/ACTIVÉ")
            print("   - Si gris, cliquez dessus pour l'activer")
            
            print("\n5️⃣  Redémarrez MetaTrader 5:")
            print("   - Fermez complètement MT5")
            print("   - Rouvrez-le")
            print("   - Vérifiez que AutoTrading est toujours activé")
            
            print("\n6️⃣  Relancez le bot Python")
            
        elif error[0] == -1:
            print("\n⚠️  MT5 N'EST PAS INSTALLÉ")
            print("   Téléchargez-le depuis: https://www.metatrader5.com/")
        
        return False
    
    print("\n✅ MT5 initialisé avec succès!")
    
    # Vérifier la connexion
    account_info = mt5.account_info()
    if account_info:
        print(f"✅ Connecté au compte: {account_info.login}")
        print(f"   Serveur: {account_info.server}")
        print(f"   Balance: {account_info.balance:.2f} {account_info.currency}")
    else:
        print("⚠️  Pas de compte connecté")
        print("   Connectez-vous manuellement dans MT5 d'abord")
    
    mt5.shutdown()
    return True

def main():
    """Point d'entrée principal"""
    print("\n" + "=" * 70)
    print("🔧 VÉRIFICATION DE LA CONFIGURATION MT5")
    print("=" * 70)
    
    # Vérifier l'installation
    if not check_mt5_installation():
        print("\n❌ Installation MT5 non trouvée")
        print("   Résolvez ce problème avant de continuer")
        return
    
    # Vérifier l'autorisation
    if not check_mt5_authorization():
        print("\n❌ Problème d'autorisation détecté")
        print("   Suivez les instructions ci-dessus")
        return
    
    print("\n" + "=" * 70)
    print("✅ TOUT EST CONFIGURÉ CORRECTEMENT!")
    print("=" * 70)
    print("\nVous pouvez maintenant lancer le bot avec:")
    print("   py run_bot.py")
    print("\n" + "=" * 70)

if __name__ == "__main__":
    main()
