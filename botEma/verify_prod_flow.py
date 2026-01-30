#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vérification du flux complet de prise de position en prod.
Valide la config (actif du jour, symboles) et le bon enchaînement jusqu'à l'ouverture d'ordre.
À lancer depuis le dossier du bot (où se trouve config.py) ou avec --config path/to/config.py
"""

import sys
import os
from datetime import datetime

def load_config(config_path=None):
    """Charge la config comme run_bot.py (avec ou sans --config)."""
    if config_path:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        sys.path.insert(0, config_dir)
        os.chdir(config_dir)
        config_name = os.path.basename(config_path).replace('.py', '')
        import importlib.util
        spec = importlib.util.spec_from_file_location(config_name, config_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    try:
        import config
        return config
    except ImportError:
        return None

def validate_config(config_module):
    """Vérifie la cohérence config: PREFERRED_SYMBOL_BY_DAY vs SYMBOLS."""
    errors = []
    symbols = getattr(config_module, 'SYMBOLS', None)
    preferred = getattr(config_module, 'PREFERRED_SYMBOL_BY_DAY', None)
    use_preferred = getattr(config_module, 'USE_DAILY_PREFERRED_SYMBOL', True)
    one_symbol = getattr(config_module, 'ONE_SYMBOL_AT_A_TIME', True)

    if not symbols or not isinstance(symbols, (list, tuple)):
        errors.append("SYMBOLS doit être une liste non vide (ex: ['US30.cash', 'US100.cash', 'US500.cash'])")
    if use_preferred and preferred:
        if not isinstance(preferred, dict):
            errors.append("PREFERRED_SYMBOL_BY_DAY doit être un dict {0: 'US30.cash', 1: 'US100.cash', ...}")
        else:
            for wd, sym in preferred.items():
                try:
                    w = int(wd)
                    if w < 0 or w > 6:
                        errors.append(f"PREFERRED_SYMBOL_BY_DAY: jour {wd} hors 0-6 (0=Lun, 6=Dim)")
                    if symbols and sym not in symbols:
                        errors.append(f"PREFERRED_SYMBOL_BY_DAY[{wd}] = '{sym}' n'est pas dans SYMBOLS {symbols}")
                except (TypeError, ValueError):
                    errors.append(f"PREFERRED_SYMBOL_BY_DAY: clé '{wd}' doit être un entier (0-6)")
    return errors, {
        "symbols": symbols or [],
        "preferred_by_day": preferred if isinstance(getattr(config_module, 'PREFERRED_SYMBOL_BY_DAY', None), dict) else {},
        "use_daily_preferred_symbol": use_preferred,
        "one_symbol_at_a_time": one_symbol,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Vérification du flux de prise de position (actif du jour + conditions)")
    parser.add_argument("--config", type=str, help="Chemin vers config.py (défaut: config.py du cwd)")
    parser.add_argument("--no-mt5", action="store_true", help="Ne pas initialiser MT5 (validation config uniquement)")
    args = parser.parse_args()

    config_path = args.config
    config_module = load_config(config_path)
    if not config_module:
        print("❌ config.py introuvable. Lancez depuis le dossier du bot ou utilisez --config path/to/config.py")
        sys.exit(1)

    print("=" * 70)
    print("🔍 VÉRIFICATION DU FLUX DE PRISE DE POSITION (PROD)")
    print("=" * 70)

    # 1) Validation config
    errs, info = validate_config(config_module)
    if errs:
        print("\n❌ ERREURS DE CONFIG:")
        for e in errs:
            print("   •", e)
        sys.exit(1)
    print("\n✅ Config chargée et cohérente")
    print("   SYMBOLS:", info["symbols"])
    print("   USE_DAILY_PREFERRED_SYMBOL:", info["use_daily_preferred_symbol"])
    print("   ONE_SYMBOL_AT_A_TIME:", info["one_symbol_at_a_time"])
    if info["preferred_by_day"]:
        day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        print("   PREFERRED_SYMBOL_BY_DAY:")
        for wd, sym in sorted(info["preferred_by_day"].items(), key=lambda x: int(x[0])):
            print(f"      {int(wd)} ({day_names[int(wd)]}): {sym}")

    # 2) Actif du jour (nécessite le bot, donc MT5 si pas --no-mt5)
    if args.no_mt5:
        print("\n⏭️  MT5 non chargé (--no-mt5). Pour vérifier l'actif du jour, relancez sans --no-mt5.")
        print("\n📋 RAPPEL DU FLUX EN PROD:")
        print("   1. run() ou run_bot.py --once")
        print("   2. can_trade_today() → si OK")
        print("   3. get_preferred_symbol_for_today() → symbole du jour (ou tous si pas de préférence)")
        print("   4. symbols_to_process = [preferred] ou self.symbols")
        print("   5. Pour chaque symbole: process_symbol(symbol)")
        print("   6. process_symbol: can_trade_today, get_market_data, nouvelle bougie, session, H1, check_long_entry/check_short_entry")
        print("   7. Si signal: has_open_position_on_other_symbol → open_long_position / open_short_position")
        sys.exit(0)

    try:
        from ema_mt5_bot import MT5TradingBot
    except ImportError as e:
        print("\n❌ Impossible d'importer MT5TradingBot:", e)
        sys.exit(1)

    login = getattr(config_module, 'MT5_LOGIN', None)
    password = getattr(config_module, 'MT5_PASSWORD', '')
    server = getattr(config_module, 'MT5_SERVER', '')
    symbols = getattr(config_module, 'SYMBOLS', [])
    risk = getattr(config_module, 'RISK_PERCENT', 0.5)
    max_loss = getattr(config_module, 'MAX_DAILY_LOSS', -250.0)

    if login is None:
        print("\n⚠️  MT5_LOGIN non défini dans config — création du bot en mode démo (connexion peut échouer)")
    bot = MT5TradingBot(
        login=login or 0,
        password=password,
        server=server,
        symbols=symbols,
        risk_percent=risk,
        max_daily_loss=max_loss,
    )

    preferred = bot.get_preferred_symbol_for_today()
    weekday = datetime.now().weekday()
    day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    today_name = day_names[weekday]

    print("\n📅 ACTIF DU JOUR (aujourd'hui)")
    print("   Jour:", today_name, f"(weekday={weekday})")
    print("   Actif tradé aujourd'hui:", preferred if preferred else f"tous ({symbols})")
    if bot.use_daily_preferred_symbol and preferred:
        symbols_to_process = [preferred]
        print("   → symbols_to_process =", symbols_to_process)
    else:
        symbols_to_process = list(bot.symbols)
        print("   → symbols_to_process =", symbols_to_process)

    print("\n✅ Flux actif du jour OK: seul l'actif du jour sera traité par process_symbol en mode continu (run) et en --once.")

    print("\n📋 FLUX COMPLET DE PRISE DE POSITION")
    print("   1. run() / run_bot.py → can_trade_today() → get_preferred_symbol_for_today()")
    print("   2. symbols_to_process = [preferred] si USE_DAILY_PREFERRED_SYMBOL et preferred présent")
    print("   3. Pour chaque symbole dans symbols_to_process: process_symbol(symbol)")
    print("   4. process_symbol: can_trade_today, get_market_data, nouvelle bougie M5, session (ASIA/EUROPE/US), H1")
    print("   5. check_long_entry(df, symbol) / check_short_entry(df, symbol) → session + H1 + croisement EMA20/SMA50")
    print("   6. Si signal: has_open_position_on_other_symbol(symbol) → sinon open_long_position / open_short_position")
    print("   7. open_*: connexion, symbol_info, tick, SL/TP, lot_size, order_send, record_trade")
    print("\n✅ Vérification terminée.")

if __name__ == "__main__":
    main()
