#!/usr/bin/env python3
"""
Script pour lancer le bot MT5 — supporte le multi-compte.

Usage:
    python run_bot.py                       # Compte par défaut (config.py plat)
    python run_bot.py --account ftmo        # Compte nommé "ftmo" dans config.ACCOUNTS
    python run_bot.py --account vtmarkets   # Compte nommé "vtmarkets" dans config.ACCOUNTS
    python run_bot.py --once                # Une seule itération
    python run_bot.py --config path/to/cfg.py --account vtmarkets
"""

import sys
import os
import importlib.util
from ema_mt5_bot import MT5TradingBot


def load_config_module(config_path: str = None):
    """Charge le module config.py (ou un chemin personnalisé)."""
    if config_path:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        sys.path.insert(0, config_dir)
        os.chdir(config_dir)
        config_name = os.path.basename(config_path).replace('.py', '')
        spec = importlib.util.spec_from_file_location(config_name, config_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    try:
        import config
        return config
    except ImportError:
        print("❌ Erreur: Fichier config.py non trouvé")
        print("\nCréez un fichier config.py basé sur config.example.py")
        print("   cp config.example.py config.py")
        print("   # Puis éditez config.py avec vos identifiants")
        sys.exit(1)


def extract_account_config(config_module, account_name: str = None) -> dict:
    """
    Extrait la configuration pour un compte donné.
    - Si account_name est fourni : cherche dans config.ACCOUNTS[account_name]
    - Sinon : utilise les variables à plat (rétro-compatible)
    """
    if account_name:
        accounts = getattr(config_module, 'ACCOUNTS', None)
        if not accounts:
            print("❌ Erreur: ACCOUNTS non défini dans config.py")
            print("   Ajoutez un dict ACCOUNTS (voir config.example.py)")
            sys.exit(1)

        if account_name not in accounts:
            available = ', '.join(accounts.keys())
            print(f"❌ Erreur: Compte '{account_name}' introuvable dans ACCOUNTS")
            print(f"   Comptes disponibles: {available}")
            sys.exit(1)

        acct = accounts[account_name]
        return {
            'account_name': account_name,
            'login': acct['MT5_LOGIN'],
            'password': acct['MT5_PASSWORD'],
            'server': acct['MT5_SERVER'],
            'mt5_terminal_path': acct.get('MT5_TERMINAL_PATH'),
            'symbols': acct.get('SYMBOLS', ['US30', 'NAS100']),
            'risk': acct.get('RISK_PERCENT', 1.0),
            'max_daily_loss': acct.get('MAX_DAILY_LOSS', -250.0),
            'interval': acct.get('UPDATE_INTERVAL', 300),
            'magic_number': acct.get('MAGIC_NUMBER', 123456),
            'trade_comment': acct.get('TRADE_COMMENT', 'EMA20_SMA50_Cross'),
            'preferred_symbol_by_day': acct.get('PREFERRED_SYMBOL_BY_DAY'),
            'use_daily_preferred_symbol': acct.get('USE_DAILY_PREFERRED_SYMBOL'),
            'one_symbol_at_a_time': acct.get('ONE_SYMBOL_AT_A_TIME'),
            'use_next_bar_open_for_entry': acct.get('USE_NEXT_BAR_OPEN_FOR_ENTRY'),
        }

    # Rétro-compatible : variables à plat
    return {
        'account_name': None,
        'login': config_module.MT5_LOGIN,
        'password': config_module.MT5_PASSWORD,
        'server': config_module.MT5_SERVER,
        'mt5_terminal_path': getattr(config_module, 'MT5_TERMINAL_PATH', None),
        'symbols': getattr(config_module, 'SYMBOLS', ['US30', 'NAS100']),
        'risk': getattr(config_module, 'RISK_PERCENT', 1.0),
        'max_daily_loss': getattr(config_module, 'MAX_DAILY_LOSS', -250.0),
        'interval': getattr(config_module, 'UPDATE_INTERVAL', 300),
        'magic_number': getattr(config_module, 'MAGIC_NUMBER', 123456),
        'trade_comment': getattr(config_module, 'TRADE_COMMENT', 'EMA20_SMA50_Cross'),
        'preferred_symbol_by_day': getattr(config_module, 'PREFERRED_SYMBOL_BY_DAY', None),
        'use_daily_preferred_symbol': getattr(config_module, 'USE_DAILY_PREFERRED_SYMBOL', None),
        'one_symbol_at_a_time': getattr(config_module, 'ONE_SYMBOL_AT_A_TIME', None),
        'use_next_bar_open_for_entry': getattr(config_module, 'USE_NEXT_BAR_OPEN_FOR_ENTRY', None),
    }


def main():
    """Point d'entrée principal"""
    import argparse

    parser = argparse.ArgumentParser(
        description="EMA Trading Bot MT5 — multi-compte",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python run_bot.py --account ftmo
  python run_bot.py --account vtmarkets
  python run_bot.py --account ftmo --once
  python run_bot.py  (utilise les variables à plat de config.py)
""",
    )
    parser.add_argument("--account", type=str, default=None,
                        help="Nom du compte dans config.ACCOUNTS (ex: ftmo, vtmarkets)")
    parser.add_argument("--once", action="store_true",
                        help="Une seule analyse (pas de monitoring continu)")
    parser.add_argument("--config", type=str, default=None,
                        help="Chemin vers le fichier de configuration (défaut: config.py)")

    args = parser.parse_args()

    config_module = load_config_module(args.config)
    cfg = extract_account_config(config_module, args.account)

    label = cfg['account_name'] or cfg['server']
    print(f"\n{'='*60}")
    print(f"  Démarrage du bot pour le compte : {label}")
    print(f"  Login : {cfg['login']}  |  Serveur : {cfg['server']}")
    print(f"  Magic : {cfg['magic_number']}  |  Symboles : {cfg['symbols']}")
    print(f"{'='*60}\n")

    bot = MT5TradingBot(
        login=cfg['login'],
        password=cfg['password'],
        server=cfg['server'],
        symbols=cfg['symbols'],
        risk_percent=cfg['risk'],
        max_daily_loss=cfg['max_daily_loss'],
        magic_number=cfg['magic_number'],
        trade_comment=cfg['trade_comment'],
        mt5_terminal_path=cfg['mt5_terminal_path'],
        account_name=cfg['account_name'],
        preferred_symbol_by_day=cfg['preferred_symbol_by_day'],
        use_daily_preferred_symbol=cfg['use_daily_preferred_symbol'],
        one_symbol_at_a_time=cfg['one_symbol_at_a_time'],
        use_next_bar_open_for_entry=cfg['use_next_bar_open_for_entry'],
    )

    try:
        if args.once:
            symbols_to_process = cfg['symbols']
            preferred = bot.get_preferred_symbol_for_today()
            if getattr(bot, 'use_daily_preferred_symbol', False) and preferred is not None:
                symbols_to_process = [preferred]
            for symbol in symbols_to_process:
                bot.process_symbol(symbol)
            bot.display_status()
        else:
            bot.run(update_interval=cfg['interval'])
    finally:
        if hasattr(bot, 'session_logger'):
            bot.session_logger.close()


if __name__ == "__main__":
    main()
