#!/usr/bin/env python3
"""
Export des données MT5 vers CSV.
A LANCER SUR TON PC WINDOWS où MT5 est installé.

Usage:
    python export_mt5_data.py

Génère dans le dossier 'data/' :
    - {symbol}_M5.csv   (données 5 minutes)
    - {symbol}_H1.csv   (données 1 heure)
    - symbols_info.json  (tick_value, tick_size, contract_size, etc.)
"""

import os
import sys
import json
from datetime import datetime

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError:
    print("ERREUR: MetaTrader5 et pandas requis.")
    print("   pip install MetaTrader5 pandas")
    sys.exit(1)

# ============================================================================
# CONFIGURATION - Compte VT Markets
# ============================================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from config import ACCOUNTS
    _vtm = ACCOUNTS["vtmarkets"]
    MT5_LOGIN = _vtm["MT5_LOGIN"]
    MT5_PASSWORD = _vtm["MT5_PASSWORD"]
    MT5_SERVER = _vtm["MT5_SERVER"]
    MT5_TERMINAL_PATH = _vtm.get("MT5_TERMINAL_PATH")
    SYMBOLS = _vtm["SYMBOLS"]
except ImportError:
    MT5_LOGIN = 20839419
    MT5_PASSWORD = "Qxo$LQo7"
    MT5_SERVER = "VTMarkets-Live 2"
    MT5_TERMINAL_PATH = r"C:\MT5_VTMarkets\terminal64.exe"
    SYMBOLS = ["DJ30.", "NAS100.", "SP500."]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def export_symbol(symbol: str):
    """Exporte les données M5 et H1 pour un symbole."""
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"  ERREUR: Symbole {symbol} non trouvé dans MT5")
        return None

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    info = {
        "name": symbol,
        "trade_tick_value": symbol_info.trade_tick_value,
        "trade_tick_size": symbol_info.trade_tick_size,
        "trade_contract_size": symbol_info.trade_contract_size,
        "point": symbol_info.point,
        "volume_min": symbol_info.volume_min,
        "volume_max": symbol_info.volume_max,
        "volume_step": symbol_info.volume_step,
    }

    # Export M5
    print(f"  Récupération M5 pour {symbol}...")
    best_rates = None
    for max_bars in [50000, 100000, 200000, 500000, 1000000, 2000000]:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, max_bars)
        if rates is not None and len(rates) > 0:
            best_rates = rates
            if len(rates) < max_bars:
                break
        else:
            break

    if best_rates is None or len(best_rates) == 0:
        print(f"  ERREUR: Aucune donnée M5 pour {symbol}")
        return None

    df_m5 = pd.DataFrame(best_rates)
    df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
    df_m5.set_index('time', inplace=True)
    m5_path = os.path.join(OUTPUT_DIR, f"{symbol.replace('.', '_')}_M5.csv")
    df_m5.to_csv(m5_path)
    print(f"  OK M5: {len(df_m5)} barres -> {m5_path}")
    print(f"     Période: {df_m5.index[0]} à {df_m5.index[-1]}")

    # Export H1
    print(f"  Récupération H1 pour {symbol}...")
    best_rates_h1 = None
    for max_bars in [10000, 50000, 100000, 200000, 500000]:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, max_bars)
        if rates is not None and len(rates) > 0:
            best_rates_h1 = rates
            if len(rates) < max_bars:
                break
        else:
            break

    if best_rates_h1 is not None and len(best_rates_h1) > 0:
        df_h1 = pd.DataFrame(best_rates_h1)
        df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s')
        df_h1.set_index('time', inplace=True)
        h1_path = os.path.join(OUTPUT_DIR, f"{symbol.replace('.', '_')}_H1.csv")
        df_h1.to_csv(h1_path)
        print(f"  OK H1: {len(df_h1)} barres -> {h1_path}")
    else:
        print(f"  ATTENTION: Pas de données H1 pour {symbol}")

    return info


def main():
    print("=" * 70)
    print("EXPORT DONNÉES MT5 VERS CSV")
    print("=" * 70)

    init_kwargs = {}
    if MT5_TERMINAL_PATH:
        init_kwargs["path"] = MT5_TERMINAL_PATH
    if not mt5.initialize(**init_kwargs):
        print(f"ERREUR initialisation MT5: {mt5.last_error()}")
        sys.exit(1)

    print(f"Connexion au compte {MT5_LOGIN} sur {MT5_SERVER}...")
    if not mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        print(f"ERREUR connexion: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)
    print("OK Connecté\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    symbols_info = {}
    for symbol in SYMBOLS:
        print(f"\n{'='*50}")
        print(f"Export {symbol}")
        print(f"{'='*50}")
        info = export_symbol(symbol)
        if info:
            symbols_info[symbol] = info

    # Sauvegarder les infos des symboles
    info_path = os.path.join(OUTPUT_DIR, "symbols_info.json")
    with open(info_path, 'w') as f:
        json.dump(symbols_info, f, indent=2)
    print(f"\nInfos symboles -> {info_path}")

    mt5.shutdown()

    print("\n" + "=" * 70)
    print("EXPORT TERMINÉ")
    print(f"Fichiers dans: {OUTPUT_DIR}")
    print("\nProchaine étape:")
    print("  1. Copie le dossier 'data/' sur ton Mac")
    print("  2. Lance: python backtest_csv.py")
    print("=" * 70)


if __name__ == "__main__":
    main()
