#!/usr/bin/env python3
"""
Script pour télécharger des données historiques depuis Yahoo Finance
Permet d'obtenir jusqu'à 5+ ans de données pour le backtest

Usage:
    pip install yfinance pandas
    python download_historical_data.py
"""

import sys
from datetime import datetime, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("ERREUR: yfinance et pandas sont requis")
    print("   Installez-les avec: pip install yfinance pandas")
    sys.exit(1)


def download_data(symbol: str, yahoo_symbol: str, period: str = "5y", interval: str = "1h"):
    """
    Télécharge les données historiques depuis Yahoo Finance
    
    Args:
        symbol: Nom du fichier de sortie (ex: "US30")
        yahoo_symbol: Symbole Yahoo Finance (ex: "^DJI" pour Dow Jones)
        period: Période à télécharger ("1y", "2y", "5y", "10y", "max")
        interval: Intervalle des bougies ("1m", "5m", "15m", "1h", "1d")
                  Note: Pour les intervalles < 1h, Yahoo limite à 60 jours
                        Pour 1h, Yahoo permet jusqu'à 2 ans
                        Pour 1d, Yahoo permet tout l'historique
    
    Returns:
        DataFrame avec les données ou None si erreur
    """
    print(f"\n{'='*60}")
    print(f"Téléchargement de {symbol} ({yahoo_symbol})")
    print(f"   Période: {period} | Intervalle: {interval}")
    print(f"{'='*60}")
    
    try:
        # Télécharger les données
        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(period=period, interval=interval)
        
        if df is None or len(df) == 0:
            print(f"   ERREUR: Aucune donnée récupérée pour {yahoo_symbol}")
            return None
        
        # Renommer les colonnes pour correspondre au format attendu
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'tick_volume'
        })
        
        # Garder seulement les colonnes nécessaires
        df = df[['open', 'high', 'low', 'close', 'tick_volume']]
        
        # Sauvegarder en CSV
        output_file = f"{symbol}_{interval}_{period}.csv"
        df.to_csv(output_file)
        
        # Afficher les stats
        oldest = df.index.min()
        newest = df.index.max()
        days = (newest - oldest).days
        
        print(f"   ✅ {len(df)} bougies téléchargées")
        print(f"   📅 Période: {oldest.strftime('%Y-%m-%d')} à {newest.strftime('%Y-%m-%d')}")
        print(f"   ⏱️  Durée: {days} jours ({days/30:.1f} mois, {days/365:.1f} ans)")
        print(f"   💾 Sauvegardé: {output_file}")
        
        return df
        
    except Exception as e:
        print(f"   ERREUR: {e}")
        return None


def download_intraday_max(symbol: str, yahoo_symbol: str):
    """
    Télécharge le maximum de données intraday possibles
    Yahoo Finance limite:
    - 1m/5m/15m: 60 jours max
    - 1h: ~2 ans max
    - 1d: tout l'historique
    
    Stratégie: Télécharger en 1h (2 ans) puis convertir en M5 synthétique si besoin
    """
    print(f"\n{'='*60}")
    print(f"Téléchargement MAXIMUM de données pour {symbol}")
    print(f"{'='*60}")
    
    # 1. Télécharger données horaires (max ~2 ans)
    print("\n[1/2] Données horaires (H1) - jusqu'à 2 ans...")
    df_h1 = download_data(symbol, yahoo_symbol, period="2y", interval="1h")
    
    # 2. Télécharger données journalières (historique complet)
    print("\n[2/2] Données journalières (D1) - historique complet...")
    df_d1 = download_data(symbol, yahoo_symbol, period="max", interval="1d")
    
    return df_h1, df_d1


def main():
    """Point d'entrée principal"""
    print("="*60)
    print("TÉLÉCHARGEMENT DE DONNÉES HISTORIQUES")
    print("Source: Yahoo Finance")
    print("="*60)
    
    # Symboles à télécharger
    symbols = {
        # Indices US
        "US30": "^DJI",      # Dow Jones Industrial Average
        "US100": "^NDX",     # Nasdaq 100
        # Alternatives
        # "SP500": "^GSPC",   # S&P 500
        # "NAS_COMP": "^IXIC", # Nasdaq Composite
    }
    
    print("\nSymboles configurés:")
    for name, yahoo in symbols.items():
        print(f"   • {name} → {yahoo}")
    
    # Télécharger chaque symbole
    for symbol, yahoo_symbol in symbols.items():
        download_intraday_max(symbol, yahoo_symbol)
    
    print("\n" + "="*60)
    print("TÉLÉCHARGEMENT TERMINÉ")
    print("="*60)
    print("\nFichiers générés:")
    print("   • *_1h_2y.csv  → Données horaires (2 ans)")
    print("   • *_1d_max.csv → Données journalières (historique complet)")
    print("\nPour utiliser ces données dans le backtest:")
    print("   1. Modifiez run_backtest.py pour charger les CSV")
    print("   2. Ou utilisez: bot.load_from_csv('US30_1h_2y.csv', 'US30')")
    print("\n⚠️  Note: Yahoo Finance ne fournit pas de données M5 sur longue période")
    print("   Les données H1 peuvent être utilisées pour valider les tendances")
    print("   Pour un backtest M5 précis, utilisez les données MT5 disponibles")


if __name__ == "__main__":
    main()
