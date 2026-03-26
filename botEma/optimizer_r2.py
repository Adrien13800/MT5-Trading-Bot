#!/usr/bin/env python3
"""
Round 2 d'optimisation - Affinage de la meilleure variante V1.
Base: 5% risk, EU only, H1 on, 3 paires en parallele.

Axes d'optimisation:
  1. Bloquer heure 9h UTC (WR 17.9%)
  2. Bloquer Mercredi (WR 27.3%)
  3. ATR SL multiplier (1.5 vs 2.0 vs 2.5)
  4. Time exit (120, 210, 300, 360, 0)
  5. Cooldown (0, 2, 4, 6)
  6. R:R Europe (2.0, 2.5, 3.0, 3.5)
  7. Risk (3, 5, 7%)
  8. Bloquer heures faibles (8h, 9h, 13h)
"""

import os
import sys
import itertools
from datetime import datetime
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_engine import (
    StrategyParams, BacktestResult,
    load_all_data, load_symbols_info, run_backtest, DATA_DIR,
)
from optimizer import print_detailed_report, export_trades_csv, monte_carlo_simulation


def generate_r2_variants() -> List[StrategyParams]:
    variants = []

    # Base commune: EU only, H1 on, 3 paires
    base = dict(
        blocked_sessions=["US", "ASIA"],
        use_h1_trend_filter=True,
        one_symbol_at_a_time=False,
        h1_bars_required=2,
    )

    # Axe principal: toutes les combos des leviers identifies
    risks = [3, 5, 7]
    rr_eu = [2.0, 2.5, 3.0, 3.5]
    atr_sls = [1.5, 2.0, 2.5]
    time_exits = [0, 120, 210, 360]
    cooldowns = [0, 2, 4]
    blocked_hours_opts = [
        None,           # aucun
        [9],            # bloquer 9h
        [8, 9],         # bloquer 8-9h
        [9, 13],        # bloquer 9h et 13h
    ]
    blocked_days_opts = [
        [],             # aucun
        [2],            # mercredi
        [2, 4],         # mercredi + vendredi
    ]

    for risk in risks:
        for rr in rr_eu:
            for atr_sl in atr_sls:
                for te in time_exits:
                    for cd in cooldowns:
                        for bh in blocked_hours_opts:
                            for bd in blocked_days_opts:
                                bh_label = "".join(map(str, bh)) if bh else "none"
                                bd_label = "".join(map(str, bd)) if bd else "none"
                                name = f"R{risk}_RR{rr}_SL{atr_sl}_TE{te}_CD{cd}_BH{bh_label}_BD{bd_label}"
                                variants.append(StrategyParams(
                                    name=name,
                                    risk_percent=risk,
                                    session_rr={"EUROPE": rr},
                                    rr_default=rr,
                                    atr_sl_multiplier=atr_sl,
                                    max_trade_duration_minutes=te,
                                    cooldown_after_loss=cd,
                                    allowed_hours=list(set(range(8, 14)) - set(bh)) if bh else None,
                                    blocked_days=bd,
                                    **base,
                                ))

    print(f"  {len(variants)} variantes R2 generees")
    return variants


def main():
    print("=" * 80)
    print("OPTIMISEUR R2 - Affinage meilleure variante")
    print("=" * 80)

    print("\nChargement des donnees...")
    m5, h1 = load_all_data(["DJ30.", "NAS100.", "SP500."])
    si = load_symbols_info()
    for sym, df in m5.items():
        print(f"  {sym}: {len(df)} barres")

    print("\nGeneration des variantes...")
    variants = generate_r2_variants()

    # C'est beaucoup - on va sampler si > 500
    if len(variants) > 500:
        import random
        random.seed(42)
        # Garder la variante de base + sample
        base_variant = StrategyParams(
            name="V1_BASE",
            risk_percent=5,
            blocked_sessions=["US", "ASIA"],
            session_rr={"EUROPE": 2.5},
            rr_default=2.0,
            one_symbol_at_a_time=False,
            use_h1_trend_filter=True,
            cooldown_after_loss=2,
            max_trade_duration_minutes=210,
            atr_sl_multiplier=1.5,
        )
        sampled = random.sample(variants, 499)
        variants = [base_variant] + sampled
        print(f"  Reduit a {len(variants)} variantes (sample aleatoire + base)")

    print(f"\nLancement de {len(variants)} backtests...")
    t0 = datetime.now()
    results = []
    for i, params in enumerate(variants):
        r = run_backtest(params, m5, h1, si, silent=True)
        results.append(r)
        if (i + 1) % 50 == 0 or i + 1 == len(variants):
            elapsed = (datetime.now() - t0).total_seconds()
            eta = elapsed / (i + 1) * (len(variants) - i - 1)
            print(f"   {i+1}/{len(variants)} ({elapsed:.0f}s ecoulees, ETA {eta:.0f}s)")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\nTermine en {elapsed:.0f}s")

    # Trier par rendement
    results.sort(key=lambda r: r.return_pct, reverse=True)

    # Top 20
    print(f"\n{'=' * 100}")
    print(f"TOP 20 VARIANTES R2")
    print(f"{'=' * 100}")
    print(f"{'#':>3s} {'Nom':50s} {'Ret%':>8s} {'WR':>6s} {'PF':>6s} {'Trades':>6s} {'DD%':>6s} {'MaxL':>4s} {'AvgR':>7s}")
    print("-" * 100)
    for i, r in enumerate(results[:20]):
        print(f"{i+1:3d} {r.params.name:50s} {r.return_pct:+7.1f}% {r.win_rate:5.1f}% {r.profit_factor:5.2f} {r.total_trades:6d} {r.max_drawdown_pct:5.1f}% {r.max_consecutive_losses:4d} {r.avg_r_multiple:+6.3f}")

    # Comparaison avec V1
    v1 = [r for r in results if r.params.name == "V1_BASE"]
    if v1:
        v1 = v1[0]
        v1_rank = next(i for i, r in enumerate(results) if r.params.name == "V1_BASE") + 1
        print(f"\nV1 BASE: rang {v1_rank}/{len(results)} | {v1.return_pct:+.1f}% | WR {v1.win_rate:.1f}% | PF {v1.profit_factor:.2f}")

    # Export
    import pandas as pd
    rows = []
    for r in results:
        p = r.params
        rows.append({
            'name': p.name, 'risk_pct': p.risk_percent,
            'rr': p.session_rr.get("EUROPE", p.rr_default),
            'atr_sl': p.atr_sl_multiplier,
            'time_exit': p.max_trade_duration_minutes,
            'cooldown': p.cooldown_after_loss,
            'allowed_hours': str(p.allowed_hours) if p.allowed_hours else 'all',
            'blocked_days': str(p.blocked_days),
            'return_pct': round(r.return_pct, 2),
            'net_profit': round(r.net_profit, 2),
            'total_trades': r.total_trades,
            'win_rate': round(r.win_rate, 2),
            'profit_factor': round(r.profit_factor, 2),
            'max_drawdown_pct': round(r.max_drawdown_pct, 2),
            'avg_r_multiple': round(r.avg_r_multiple, 4),
            'max_consecutive_losses': r.max_consecutive_losses,
        })
    pd.DataFrame(rows).to_csv(os.path.join(DATA_DIR, "optimizer_r2_results.csv"), index=False)

    # Rapport detaille du best
    best = results[0]
    print(f"\n{'=' * 80}")
    print("RAPPORT DETAILLE - MEILLEURE VARIANTE R2")
    print_detailed_report(best)

    # Export trades
    export_trades_csv(best.trades, os.path.join(DATA_DIR, "optimizer_r2_best_trades.csv"))

    # Sauver aussi dans best_version si meilleur que V1
    if v1 and best.return_pct > v1[0].return_pct if isinstance(v1, list) else best.return_pct > v1.return_pct:
        bv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_version")
        export_trades_csv(best.trades, os.path.join(bv_dir, "r2_best_trades.csv"))
        with open(os.path.join(bv_dir, "r2_params.txt"), 'w') as f:
            f.write(f"Best Version R2 - {best.params.name}\n")
            f.write(f"Resultat: {best.return_pct:+.1f}% | WR: {best.win_rate:.1f}% | PF: {best.profit_factor:.2f}\n")
            f.write(f"Trades: {best.total_trades} | Max DD: {best.max_drawdown_pct:.1f}%\n\n")
            p = best.params
            f.write(f"risk_percent: {p.risk_percent}\n")
            f.write(f"session_rr: {p.session_rr}\n")
            f.write(f"atr_sl_multiplier: {p.atr_sl_multiplier}\n")
            f.write(f"max_trade_duration_minutes: {p.max_trade_duration_minutes}\n")
            f.write(f"cooldown_after_loss: {p.cooldown_after_loss}\n")
            f.write(f"allowed_hours: {p.allowed_hours}\n")
            f.write(f"blocked_days: {p.blocked_days}\n")
        print(f"\nSauvegarde dans best_version/")

    print("\nFichiers generes:")
    print(f"  data/optimizer_r2_results.csv")
    print(f"  data/optimizer_r2_best_trades.csv")


if __name__ == "__main__":
    main()
