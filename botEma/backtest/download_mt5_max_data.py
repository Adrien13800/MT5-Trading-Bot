#!/usr/bin/env python3
"""
Script pour telecharger le MAXIMUM de donnees M5 depuis MT5
Essaie differentes methodes pour recuperer le plus de donnees possible
"""

import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError:
    print("ERREUR: MetaTrader5 et pandas sont requis")
    print("   pip install MetaTrader5 pandas")
    sys.exit(1)


def connect_mt5():
    """Initialise MT5"""
    if not mt5.initialize():
        print(f"ERREUR initialisation MT5: {mt5.last_error()}")
        return False
    
    # Afficher les infos de connexion
    account = mt5.account_info()
    if account:
        print(f"Connecte: {account.login} sur {account.server}")
    
    return True


def find_symbol(base_name):
    """Trouve le symbole exact dans MT5"""
    # Essayer differentes variantes
    variants = [
        base_name,
        base_name + ".cash",
        base_name + ".Cash",
        base_name + "Cash",
        base_name.upper(),
        base_name.lower(),
    ]
    
    for variant in variants:
        info = mt5.symbol_info(variant)
        if info is not None:
            return variant
    
    # Chercher dans tous les symboles
    all_symbols = mt5.symbols_get()
    if all_symbols:
        for sym in all_symbols:
            if base_name.upper() in sym.name.upper():
                return sym.name
    
    return None


def download_max_m5(symbol_base, output_file=None):
    """
    Telecharge le maximum de donnees M5 disponibles
    """
    print(f"\n{'='*60}")
    print(f"Telechargement M5 pour {symbol_base}")
    print(f"{'='*60}")
    
    # Trouver le symbole
    symbol = find_symbol(symbol_base)
    if not symbol:
        print(f"   ERREUR: Symbole {symbol_base} non trouve")
        return None
    
    print(f"   Symbole trouve: {symbol}")
    
    # Activer le symbole
    if not mt5.symbol_select(symbol, True):
        print(f"   ERREUR: Impossible d'activer {symbol}")
        return None
    
    # Essayer de telecharger de plus en plus de donnees
    attempts = [
        100000,    # ~1 an
        200000,    # ~2 ans
        500000,    # ~5 ans
        1000000,   # ~10 ans
        2000000,   # Maximum
    ]
    
    best_rates = None
    best_count = 0
    
    for max_bars in attempts:
        print(f"   Tentative: {max_bars} bougies...", end=" ")
        
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, max_bars)
        
        if rates is not None and len(rates) > 0:
            count = len(rates)
            print(f"OK ({count} bougies)")
            
            if count > best_count:
                best_rates = rates
                best_count = count
            
            # Si on a moins que demande, on a tout
            if count < max_bars:
                print(f"   --> Toutes les donnees disponibles recuperees")
                break
        else:
            print(f"ERREUR")
            break
    
    if best_rates is None or len(best_rates) == 0:
        print(f"   ERREUR: Aucune donnee recuperee")
        return None
    
    # Convertir en DataFrame
    df = pd.DataFrame(best_rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    
    # Stats
    oldest = df.index.min()
    newest = df.index.max()
    days = (newest - oldest).days
    
    print(f"\n   RESULTATS:")
    print(f"   Bougies: {len(df)}")
    print(f"   Periode: {oldest.strftime('%Y-%m-%d')} a {newest.strftime('%Y-%m-%d')}")
    print(f"   Duree: {days} jours ({days/30:.1f} mois, {days/365:.2f} ans)")
    
    # Sauvegarder en CSV si demande
    if output_file is None:
        output_file = f"{symbol_base}_M5_{oldest.strftime('%Y%m%d')}_{newest.strftime('%Y%m%d')}.csv"
    
    df.to_csv(output_file)
    print(f"   Sauvegarde: {output_file}")
    
    return df


def main():
    print("="*60)
    print("TELECHARGEMENT MAXIMUM DE DONNEES M5")
    print("="*60)
    
    if not connect_mt5():
        sys.exit(1)
    
    # Symboles a telecharger
    symbols = ["US30", "US100"]
    
    results = {}
    
    for symbol in symbols:
        df = download_max_m5(symbol)
        if df is not None:
            results[symbol] = df
    
    # Resume
    print("\n" + "="*60)
    print("RESUME")
    print("="*60)
    
    for symbol, df in results.items():
        days = (df.index.max() - df.index.min()).days
        print(f"   {symbol}: {len(df)} bougies ({days/365:.2f} ans)")
    
    if results:
        print("\nFichiers CSV generes. Utilisez-les avec le backtest:")
        print("   bot.load_from_csv('US30_M5_xxx.csv', 'US30')")
    else:
        print("\nAucune donnee recuperee.")
        print("Votre broker limite peut-etre l'historique disponible.")
        print("\nOptions:")
        print("   1. Essayez un autre broker MT5 (compte demo)")
        print("   2. Achetez des donnees sur FirstRate Data (~$20)")
    
    mt5.shutdown()


if __name__ == "__main__":
    main()
