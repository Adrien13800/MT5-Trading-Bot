#!/usr/bin/env python3
"""
Script pour telecharger le MAXIMUM de donnees M5 depuis MT5
Version 2: Essaie plusieurs methodes
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

# Charger la config
sys.path.insert(0, '.')
try:
    import config
    MT5_LOGIN = config.MT5_LOGIN
    MT5_PASSWORD = config.MT5_PASSWORD
    MT5_SERVER = config.MT5_SERVER
except:
    print("ERREUR: config.py non trouve")
    sys.exit(1)


def connect_mt5():
    """Initialise et connecte MT5"""
    print("Initialisation MT5...")
    
    if not mt5.initialize():
        print(f"   ERREUR initialisation: {mt5.last_error()}")
        return False
    
    print("   OK MT5 initialise")
    
    # Se connecter au compte
    print(f"Connexion au compte {MT5_LOGIN}...")
    
    authorized = mt5.login(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER
    )
    
    if not authorized:
        print(f"   ERREUR connexion: {mt5.last_error()}")
        return False
    
    account = mt5.account_info()
    if account:
        print(f"   OK Connecte: {account.login} sur {account.server}")
    
    return True


def find_symbol(base_name):
    """Trouve le symbole exact dans MT5"""
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


def download_max_m5(symbol_base):
    """Telecharge le maximum de donnees M5"""
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
    info = mt5.symbol_info(symbol)
    if info and not info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"   ERREUR: Impossible d'activer {symbol}")
            return None
        print(f"   OK Symbole active")
    
    # Methode 1: copy_rates_from_pos (depuis maintenant vers le passe)
    print("\n   Methode 1: copy_rates_from_pos...")
    
    best_rates = None
    best_count = 0
    
    for max_bars in [10000, 50000, 100000, 200000, 500000, 1000000]:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, max_bars)
        
        if rates is not None and len(rates) > 0:
            print(f"      {max_bars} demande -> {len(rates)} obtenu")
            if len(rates) > best_count:
                best_rates = rates
                best_count = len(rates)
            if len(rates) < max_bars:
                break
        else:
            error = mt5.last_error()
            print(f"      {max_bars} demande -> ERREUR: {error}")
            break
    
    # Methode 2: copy_rates_range (plage de dates)
    if best_rates is None or len(best_rates) < 50000:
        print("\n   Methode 2: copy_rates_range...")
        
        end_date = datetime.now()
        
        for years in [1, 2, 3, 5]:
            start_date = end_date - timedelta(days=years * 365)
            
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_date, end_date)
            
            if rates is not None and len(rates) > 0:
                print(f"      {years} ans demande -> {len(rates)} obtenu")
                if len(rates) > best_count:
                    best_rates = rates
                    best_count = len(rates)
            else:
                error = mt5.last_error()
                print(f"      {years} ans demande -> ERREUR: {error}")
    
    if best_rates is None or len(best_rates) == 0:
        print(f"\n   ERREUR: Aucune donnee recuperee")
        print(f"   Verifiez que MT5 est ouvert et connecte")
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
    
    # Sauvegarder
    output_file = f"{symbol_base}_M5_{oldest.strftime('%Y%m%d')}_{newest.strftime('%Y%m%d')}.csv"
    df.to_csv(output_file)
    print(f"   Sauvegarde: {output_file}")
    
    return df


def main():
    print("="*60)
    print("TELECHARGEMENT MAXIMUM DE DONNEES M5 - Version 2")
    print("="*60)
    
    if not connect_mt5():
        print("\nAssurez-vous que:")
        print("   1. MetaTrader 5 est ouvert")
        print("   2. Vous etes connecte a votre compte")
        print("   3. Les identifiants dans config.py sont corrects")
        sys.exit(1)
    
    # Lister les symboles disponibles
    print("\nSymboles disponibles contenant 'US':")
    all_symbols = mt5.symbols_get()
    if all_symbols:
        us_symbols = [s.name for s in all_symbols if 'US' in s.name.upper()]
        for s in us_symbols[:10]:
            print(f"   - {s}")
    
    # Telecharger
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
    
    if results:
        for symbol, df in results.items():
            days = (df.index.max() - df.index.min()).days
            print(f"   {symbol}: {len(df)} bougies ({days/365:.2f} ans)")
        
        print("\nFichiers CSV generes!")
        print("Pour les utiliser dans le backtest, modifiez run_backtest.py")
    else:
        print("Aucune donnee recuperee.")
        print("\nLe broker FTMO limite probablement l'historique.")
        print("\nSOLUTIONS:")
        print("   1. Ouvrir un compte demo chez un autre broker:")
        print("      - IC Markets (beaucoup d'historique)")
        print("      - Pepperstone")
        print("      - XM")
        print("   2. Acheter les donnees sur FirstRate Data (~$20)")
        print("      https://firstratedata.com")
    
    mt5.shutdown()


if __name__ == "__main__":
    main()
