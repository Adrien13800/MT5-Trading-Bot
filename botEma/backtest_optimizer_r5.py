#!/usr/bin/env python3
"""
backtest_optimizer_r5.py - Round 5: Combos des gagnants R4

Gagnants R4 individuels:
  - Cooldown 2 = 93.0%
  - Time 42b (210 min) = 92.6%
  - Time 60b (300 min) = 92.5%
  - RR 1:1.8 + Time 60b = 92.3%
  - Cooldown 1 = 83.5% (mais meilleur DD)
  - Cooldown 8 = 82.0% (meilleur Rdt/DD)

On teste toutes les combos prometteuses de ces gagnants.
"""

import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Reutilise tout le moteur R4
_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'backtest'))

from datetime import datetime
from backtest_optimizer_r4 import (
    load_config, connect_mt5, load_data, run_variant, compute_stats
)
import MetaTrader5 as mt5


def main():
    cfg = load_config()
    print("=" * 70)
    print("OPTIMISEUR R5 - Combos des gagnants R4")
    print("=" * 70)
    connect_mt5(cfg)
    print("\nChargement des donnees...")
    m5_data, h1_data = load_data(cfg['symbols'])
    initial = cfg.get('initial_balance', 10000.0)

    BASE = {"rr_flat": 2.0, "rr_trending": 2.0, "max_trade_minutes": 240,
            "atr_sl_mult": 1.5, "cooldown_bars": 3, "h1_bars": 2, "use_h1": True}

    variants = [
        # REF
        ("BASE (R4)", {**BASE}),

        # === Cooldown x Time Exit combos ===
        ("Cd2 + Time42b",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210}),
        ("Cd2 + Time36b",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 180}),
        ("Cd2 + Time54b",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 270}),
        ("Cd2 + Time60b",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 300}),
        ("Cd1 + Time42b",  {**BASE, "cooldown_bars": 1, "max_trade_minutes": 210}),
        ("Cd1 + Time60b",  {**BASE, "cooldown_bars": 1, "max_trade_minutes": 300}),
        ("Cd0 + Time42b",  {**BASE, "cooldown_bars": 0, "max_trade_minutes": 210}),
        ("Cd0 + Time60b",  {**BASE, "cooldown_bars": 0, "max_trade_minutes": 300}),

        # === Cooldown x RR combos ===
        ("Cd2 + RR1.8",  {**BASE, "cooldown_bars": 2, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd2 + RR2.2",  {**BASE, "cooldown_bars": 2, "rr_flat": 2.2, "rr_trending": 2.2}),
        ("Cd1 + RR1.8",  {**BASE, "cooldown_bars": 1, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd0 + RR1.8",  {**BASE, "cooldown_bars": 0, "rr_flat": 1.8, "rr_trending": 1.8}),

        # === Triple combos: Cd + Time + RR ===
        ("Cd2 + Time42b + RR1.8",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd2 + Time60b + RR1.8",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 300, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd2 + Time42b + RR2.2",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "rr_flat": 2.2, "rr_trending": 2.2}),
        ("Cd2 + Time54b + RR1.8",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 270, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd1 + Time42b + RR1.8",  {**BASE, "cooldown_bars": 1, "max_trade_minutes": 210, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd0 + Time42b + RR1.8",  {**BASE, "cooldown_bars": 0, "max_trade_minutes": 210, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd2 + Time36b + RR1.8",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 180, "rr_flat": 1.8, "rr_trending": 1.8}),
        ("Cd2 + Time42b + RR2.5",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "rr_flat": 2.5, "rr_trending": 2.5}),

        # === Best with slightly adjusted SL ===
        ("Cd2 + Time42b + SL1.3x",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "atr_sl_mult": 1.3}),
        ("Cd2 + Time42b + SL1.7x",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "atr_sl_mult": 1.7}),
        ("Cd2 + Time42b + RR1.8 + SL1.3x",  {**BASE, "cooldown_bars": 2, "max_trade_minutes": 210, "rr_flat": 1.8, "rr_trending": 1.8, "atr_sl_mult": 1.3}),
    ]

    results = []
    n = len(variants)
    print(f"\nTest de {n} variantes...\n")

    for i, (name, kwargs) in enumerate(variants, 1):
        print(f"  [{i:2d}/{n}] {name}...", end=" ", flush=True)
        trades, final = run_variant(m5_data, h1_data, cfg, **kwargs)
        stats = compute_stats(trades, initial, final)
        stats['name'] = name
        results.append(stats)
        print(f"{stats['trades']} trades | WR {stats['wr']:.1f}% | PF {stats['pf']:.2f} | "
              f"Rdt {stats['rendement']:.1f}% | DD {stats['max_dd']:.0f} | "
              f"TIME {stats['time_exits']}")

    # === CLASSEMENT ===
    results_rdt = sorted(results, key=lambda x: x['rendement'], reverse=True)

    print(f"\n{'=' * 140}")
    print(f"{'CLASSEMENT PAR RENDEMENT':^140}")
    print(f"{'=' * 140}")
    print(f"{'#':>3} {'Variante':<42} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Net$':>10} {'Rdt%':>8} {'MaxDD$':>8} {'DD%':>6} {'AvgW':>7} {'AvgL':>7} {'TIME':>5}")
    print("-" * 140)
    for i, s in enumerate(results_rdt, 1):
        dd_pct = s['max_dd'] / initial * 100
        marker = " <-- BEST" if i == 1 else ""
        print(f"{i:3d} {s['name']:<42} {s['trades']:6d} {s['wr']:5.1f}% {s['pf']:6.2f} "
              f"{s['net']:9.0f}$ {s['rendement']:7.1f}% {s['max_dd']:7.0f}$ {dd_pct:5.1f}% "
              f"{s.get('avg_win', 0):6.0f}$ {s.get('avg_loss', 0):6.0f}$ {s.get('time_exits', 0):5d}{marker}")

    # Rdt/DD
    print(f"\n{'=' * 140}")
    print(f"{'CLASSEMENT PAR RENDEMENT / DRAWDOWN':^140}")
    print(f"{'=' * 140}")
    print(f"{'#':>3} {'Variante':<42} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Rdt%':>8} {'MaxDD$':>8} {'DD%':>6} {'Rdt/DD':>8}")
    print("-" * 140)
    results_cal = sorted(results, key=lambda x: x['rendement'] / (x['max_dd'] / initial * 100) if x['max_dd'] > 0 else 0, reverse=True)
    for i, s in enumerate(results_cal, 1):
        dd_pct = s['max_dd'] / initial * 100
        ratio = s['rendement'] / dd_pct if dd_pct > 0 else 0
        print(f"{i:3d} {s['name']:<42} {s['trades']:6d} {s['wr']:5.1f}% {s['pf']:6.2f} "
              f"{s['rendement']:7.1f}% {s['max_dd']:7.0f}$ {dd_pct:5.1f}% {ratio:7.2f}")

    mt5.shutdown()
    print("\nTermine.")


if __name__ == "__main__":
    main()
