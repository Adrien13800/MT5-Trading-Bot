#!/usr/bin/env python3
"""
Script simplifié pour lancer le bot MT5 avec un fichier de configuration
"""

import sys
import os
from ema_mt5_bot import MT5TradingBot

def load_config():
    """Charge la configuration depuis config.py"""
    try:
        import config
        return {
            'login': config.MT5_LOGIN,
            'password': config.MT5_PASSWORD,
            'server': config.MT5_SERVER,
            'symbols': getattr(config, 'SYMBOLS', ['US30', 'NAS100']),
            'risk': getattr(config, 'RISK_PERCENT', 1.0),
            'max_daily_loss': getattr(config, 'MAX_DAILY_LOSS', -250.0),
            'interval': getattr(config, 'UPDATE_INTERVAL', 300)
        }
    except ImportError:
        print("❌ Erreur: Fichier config.py non trouvé")
        print("\nCréez un fichier config.py basé sur config_example.py")
        print("   cp config_example.py config.py")
        print("   # Puis éditez config.py avec vos identifiants")
        sys.exit(1)
    except AttributeError as e:
        print(f"❌ Erreur dans config.py: {e}")
        print("   Vérifiez que tous les champs sont définis")
        sys.exit(1)

def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description="EMA Trading Bot MT5 - Lancement simplifié")
    parser.add_argument("--once", action="store_true",
                       help="Une seule analyse (pas de monitoring continu)")
    parser.add_argument("--config", type=str,
                       help="Chemin vers le fichier de configuration (défaut: config.py)")
    
    args = parser.parse_args()
    
    # Charger la configuration
    if args.config:
        # Ajouter le répertoire du fichier config au path
        config_dir = os.path.dirname(os.path.abspath(args.config))
        sys.path.insert(0, config_dir)
        os.chdir(config_dir)
        config_name = os.path.basename(args.config).replace('.py', '')
        import importlib.util
        spec = importlib.util.spec_from_file_location(config_name, args.config)
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        
        config_data = {
            'login': config_module.MT5_LOGIN,
            'password': config_module.MT5_PASSWORD,
            'server': config_module.MT5_SERVER,
            'symbols': getattr(config_module, 'SYMBOLS', ['US30', 'NAS100']),
            'risk': getattr(config_module, 'RISK_PERCENT', 1.0),
            'max_daily_loss': getattr(config_module, 'MAX_DAILY_LOSS', -250.0),
            'interval': getattr(config_module, 'UPDATE_INTERVAL', 300)
        }
    else:
        config_data = load_config()
    
    # Créer le bot
    bot = MT5TradingBot(
        login=config_data['login'],
        password=config_data['password'],
        server=config_data['server'],
        symbols=config_data['symbols'],
        risk_percent=config_data['risk'],
        max_daily_loss=config_data['max_daily_loss']
    )
    
    try:
        if args.once:
            # Une seule analyse — respecter l'actif du jour (même logique que run())
            symbols_to_process = config_data['symbols']
            preferred = bot.get_preferred_symbol_for_today()
            if getattr(bot, 'use_daily_preferred_symbol', False) and preferred is not None:
                symbols_to_process = [preferred]
            for symbol in symbols_to_process:
                bot.process_symbol(symbol)
            bot.display_status()
        else:
            # Mode continu
            bot.run(update_interval=config_data['interval'])
    finally:
        # Fermer le fichier de log proprement
        if hasattr(bot, 'session_logger'):
            bot.session_logger.close()

if __name__ == "__main__":
    main()
