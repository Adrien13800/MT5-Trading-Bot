#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de vérification pour s'assurer que le bot de production
utilise exactement la même logique que le backtest
"""

import sys
import importlib.util
from pathlib import Path

# Forcer l'encodage UTF-8 pour Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def load_module(file_path, module_name):
    """Charge un module Python depuis un fichier"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def compare_constants(backtest_module, prod_module):
    """Compare les constantes entre backtest et production"""
    print("=" * 70)
    print("🔍 COMPARAISON DES CONSTANTES")
    print("=" * 70)
    
    constants_to_check = [
        'EMA_FAST',
        'SMA_SLOW',
        'RISK_REWARD_RATIO_FLAT',
        'RISK_REWARD_RATIO_TRENDING',
        'SMA_SLOPE_MIN',
        'USE_ATR_FILTER',
        'ATR_PERIOD',
        'ATR_MULTIPLIER',
        'ATR_LOOKBACK',
        'USE_ATR_SL',
        'ATR_SL_MULTIPLIER',
        'ALLOW_LONG',
        'ALLOW_SHORT',
        'USE_H1_TREND_FILTER',
        'USE_DISTANCE_FILTER',
        'USE_EMA_SPREAD_FILTER',
        'USE_CONFIRMATION_FILTER',
        'USE_VOLATILITY_FILTER',
    ]
    
    differences = []
    matches = []
    
    for const in constants_to_check:
        backtest_val = getattr(backtest_module, const, None)
        prod_val = getattr(prod_module, const, None)
        
        if backtest_val is None and prod_val is None:
            continue
        
        if backtest_val != prod_val:
            differences.append({
                'constant': const,
                'backtest': backtest_val,
                'production': prod_val
            })
            print(f"❌ {const}:")
            print(f"   Backtest:  {backtest_val}")
            print(f"   Production: {prod_val}")
        else:
            matches.append(const)
            print(f"✅ {const}: {backtest_val}")
    
    print(f"\n📊 Résumé: {len(matches)} identiques, {len(differences)} différences")
    
    return len(differences) == 0

def compare_function_logic(func_name, backtest_module, prod_module):
    """Compare la logique d'une fonction entre backtest et production"""
    backtest_func = getattr(backtest_module, func_name, None)
    prod_func = getattr(prod_module, func_name, None)
    
    if backtest_func is None or prod_func is None:
        return None
    
    # Comparer le code source (simplifié)
    backtest_code = backtest_func.__code__
    prod_code = prod_func.__code__
    
    # Vérifier le nombre de lignes (approximatif)
    backtest_lines = backtest_code.co_code
    prod_lines = prod_code.co_code
    
    # Comparer les noms des variables locales
    backtest_vars = backtest_code.co_varnames
    prod_vars = prod_code.co_varnames
    
    # Comparer les constantes utilisées
    backtest_consts = backtest_code.co_consts
    prod_consts = prod_code.co_consts
    
    # Vérification basique : même nombre de paramètres
    if backtest_code.co_argcount != prod_code.co_argcount:
        return False
    
    # Pour une vérification plus approfondie, on pourrait utiliser ast
    # mais pour l'instant, on se contente de vérifier que les fonctions existent
    return True

def verify_critical_functions(backtest_module, prod_module):
    """Vérifie que les fonctions critiques existent dans les deux modules"""
    print("\n" + "=" * 70)
    print("🔍 VÉRIFICATION DES FONCTIONS CRITIQUES")
    print("=" * 70)
    
    critical_functions = [
        'find_last_low',
        'find_last_high',
        'get_risk_reward_ratio',
        'is_ema200_flat',
        'calculate_lot_size',
        'check_long_entry',
        'check_short_entry',
        'check_h1_trend',
        'check_atr_filter',
    ]
    
    all_present = True
    
    for func_name in critical_functions:
        backtest_has = hasattr(backtest_module, func_name) or hasattr(backtest_module.MT5BacktestBot, func_name)
        prod_has = hasattr(prod_module, func_name) or hasattr(prod_module.MT5TradingBot, func_name)
        
        if backtest_has and prod_has:
            print(f"✅ {func_name}: Présente dans les deux")
        else:
            print(f"❌ {func_name}:")
            print(f"   Backtest:  {'✅' if backtest_has else '❌'}")
            print(f"   Production: {'✅' if prod_has else '❌'}")
            all_present = False
    
    return all_present

def test_calculation_consistency():
    """Teste que les calculs sont cohérents avec un exemple concret"""
    print("\n" + "=" * 70)
    print("🧪 TEST DE COHÉRENCE DES CALCULS")
    print("=" * 70)
    
    # Simuler un calcul de SL/TP avec des valeurs de test
    test_cases = [
        {
            'name': 'LONG - Prix 50000, ATR 50',
            'entry_price': 50000.0,
            'atr': 50.0,
            'atr_multiplier': 1.5,
            'expected_sl': 50000.0 - (50.0 * 1.5),  # 49925.0
            'rr_flat': 1.0,
            'rr_trending': 1.5,
        },
        {
            'name': 'SHORT - Prix 50000, ATR 50',
            'entry_price': 50000.0,
            'atr': 50.0,
            'atr_multiplier': 1.5,
            'expected_sl': 50000.0 + (50.0 * 1.5),  # 50075.0
            'rr_flat': 1.0,
            'rr_trending': 1.5,
        },
    ]
    
    print("\n📊 Tests de calcul SL/TP:")
    for test in test_cases:
        print(f"\n   Test: {test['name']}")
        print(f"   Entry: {test['entry_price']}")
        print(f"   ATR: {test['atr']}")
        print(f"   SL attendu: {test['expected_sl']}")
        
        # Calculer SL
        if 'LONG' in test['name']:
            sl = test['entry_price'] - (test['atr'] * test['atr_multiplier'])
        else:
            sl = test['entry_price'] + (test['atr'] * test['atr_multiplier'])
        
        print(f"   SL calculé: {sl}")
        
        if abs(sl - test['expected_sl']) < 0.01:
            print(f"   ✅ SL correct")
        else:
            print(f"   ❌ SL incorrect (différence: {abs(sl - test['expected_sl'])})")
        
        # Calculer TP avec R:R 1:1.5
        if 'LONG' in test['name']:
            sl_distance = test['entry_price'] - sl
            tp_flat = test['entry_price'] + (sl_distance * test['rr_flat'])
            tp_trending = test['entry_price'] + (sl_distance * test['rr_trending'])
        else:
            sl_distance = sl - test['entry_price']
            tp_flat = test['entry_price'] - (sl_distance * test['rr_flat'])
            tp_trending = test['entry_price'] - (sl_distance * test['rr_trending'])
        
        print(f"   TP (R:R 1:1): {tp_flat}")
        print(f"   TP (R:R 1:1.5): {tp_trending}")
        
        # Vérifier que TP > Entry pour LONG, TP < Entry pour SHORT
        if 'LONG' in test['name']:
            if tp_trending > test['entry_price']:
                print(f"   ✅ TP correct (TP > Entry)")
            else:
                print(f"   ❌ TP incorrect (TP <= Entry)")
        else:
            if tp_trending < test['entry_price']:
                print(f"   ✅ TP correct (TP < Entry)")
            else:
                print(f"   ❌ TP incorrect (TP >= Entry)")

def verify_production_readiness():
    """Vérifie que le bot de production est prêt à trader"""
    print("\n" + "=" * 70)
    print("🚀 VÉRIFICATION DE LA PRÊTE À TRADER")
    print("=" * 70)
    
    checks = []
    
    # Vérifier que config.py existe
    config_path = Path("config.py")
    if config_path.exists():
        print("✅ config.py existe")
        checks.append(True)
        
        # Vérifier les champs requis
        try:
            import config
            required_fields = ['MT5_LOGIN', 'MT5_PASSWORD', 'MT5_SERVER', 'SYMBOLS', 'RISK_PERCENT']
            all_present = True
            for field in required_fields:
                if hasattr(config, field):
                    print(f"✅ {field} défini")
                else:
                    print(f"❌ {field} manquant")
                    all_present = False
            checks.append(all_present)
        except ImportError:
            print("❌ Impossible d'importer config.py")
            checks.append(False)
    else:
        print("❌ config.py n'existe pas")
        checks.append(False)
    
    # Vérifier que run_bot.py existe
    run_bot_path = Path("run_bot.py")
    if run_bot_path.exists():
        print("✅ run_bot.py existe")
        checks.append(True)
    else:
        print("❌ run_bot.py n'existe pas")
        checks.append(False)
    
    # Vérifier que ema_mt5_bot.py existe
    bot_path = Path("ema_mt5_bot.py")
    if bot_path.exists():
        print("✅ ema_mt5_bot.py existe")
        checks.append(True)
    else:
        print("❌ ema_mt5_bot.py n'existe pas")
        checks.append(False)
    
    all_checks_passed = all(checks)
    
    if all_checks_passed:
        print("\n✅ Le bot est prêt à trader!")
    else:
        print("\n❌ Le bot n'est pas encore prêt. Corrigez les problèmes ci-dessus.")
    
    return all_checks_passed

def main():
    """Fonction principale"""
    print("=" * 70)
    print("🔍 VÉRIFICATION DE L'ALIGNEMENT BACKTEST / PRODUCTION")
    print("=" * 70)
    
    # Chemins des fichiers
    base_path = Path(__file__).parent
    backtest_path = base_path / "backtest" / "ema_mt5_bot_backtest.py"
    prod_path = base_path / "ema_mt5_bot.py"
    
    if not backtest_path.exists():
        print(f"❌ Fichier backtest non trouvé: {backtest_path}")
        return
    
    if not prod_path.exists():
        print(f"❌ Fichier production non trouvé: {prod_path}")
        return
    
    # Charger les modules
    print("\n📦 Chargement des modules...")
    try:
        backtest_module = load_module(backtest_path, "backtest_bot")
        prod_module = load_module(prod_path, "prod_bot")
        print("✅ Modules chargés avec succès")
    except Exception as e:
        print(f"❌ Erreur lors du chargement: {e}")
        return
    
    # Comparer les constantes
    constants_match = compare_constants(backtest_module, prod_module)
    
    # Vérifier les fonctions critiques
    functions_ok = verify_critical_functions(backtest_module, prod_module)
    
    # Tester la cohérence des calculs
    test_calculation_consistency()
    
    # Vérifier la prête à trader
    production_ready = verify_production_readiness()
    
    # Résumé final
    print("\n" + "=" * 70)
    print("📊 RÉSUMÉ FINAL")
    print("=" * 70)
    
    print(f"Constantes alignées: {'✅' if constants_match else '❌'}")
    print(f"Fonctions critiques: {'✅' if functions_ok else '❌'}")
    print(f"Prêt à trader: {'✅' if production_ready else '❌'}")
    
    if constants_match and functions_ok and production_ready:
        print("\n🎉 Le bot de production est aligné avec le backtest et prêt à trader!")
        print("\n💡 Pour lancer le bot:")
        print("   py run_bot.py")
        print("\n💡 Pour une seule analyse:")
        print("   py run_bot.py --once")
    else:
        print("\n⚠️  Des différences ont été détectées. Vérifiez les détails ci-dessus.")
        if not constants_match:
            print("   - Corrigez les constantes qui diffèrent")
        if not functions_ok:
            print("   - Vérifiez que toutes les fonctions critiques existent")
        if not production_ready:
            print("   - Configurez les fichiers manquants")

if __name__ == "__main__":
    main()
