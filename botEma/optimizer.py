#!/usr/bin/env python3
"""
Optimiseur de strategie - Teste des centaines de combinaisons de parametres
et genere un rapport ultra-detaille pour identifier la config la plus rentable.

Usage:
    python optimizer.py                  # Scan complet
    python optimizer.py --months 8       # Derniers 8 mois
    python optimizer.py --top 5          # Affiche top 5

Genere:
    data/optimizer_results.csv         - Resultats de toutes les variantes
    data/optimizer_best_trades.csv     - Trades detailles de la meilleure variante
    data/optimizer_report.txt          - Rapport complet
"""

import os
import sys
import argparse
import itertools
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_engine import (
    StrategyParams, BacktestResult, TradeResult,
    load_all_data, load_symbols_info,
    run_backtest, DATA_DIR,
)


# ============================================================================
# PARAMETER GRID
# ============================================================================
def generate_variants() -> List[StrategyParams]:
    """Genere toutes les combinaisons de parametres a tester."""
    variants = []
    idx = 0

    # Axes d'optimisation
    risk_percents = [1, 2, 5, 10]
    blocked_sessions_options = [
        [],
        ["US"],
        ["US", "ASIA"],
        ["ASIA"],
    ]
    session_rr_options = [
        {},  # RR par defaut partout
        {"EUROPE": 2.0, "ASIA": 2.0},
        {"EUROPE": 2.5, "ASIA": 2.0},
        {"EUROPE": 2.5, "ASIA": 2.5},
        {"EUROPE": 3.0, "ASIA": 2.0},
        {"EUROPE": 2.0, "ASIA": 1.5},
        {"EUROPE": 1.5, "ASIA": 1.5},
    ]
    rr_defaults = [1.5, 2.0, 2.5, 3.0]
    one_at_a_time_options = [True, False]
    h1_options = [True, False]
    cooldowns = [0, 2, 4]
    time_exits = [0, 120, 210, 360]
    atr_sl_mults = [1.0, 1.5, 2.0]
    blocked_days_options = [
        [],
        [4],           # Vendredi
        [1, 3],        # Mardi, Jeudi
        [0, 4],        # Lundi, Vendredi
    ]

    # Phase 1 : sweep session + RR + risk (core parameters)
    for risk in risk_percents:
        for blocked_s in blocked_sessions_options:
            for sess_rr in session_rr_options:
                for rr_def in rr_defaults:
                    for one_at in one_at_a_time_options:
                        idx += 1
                        bs_label = "+".join(blocked_s) if blocked_s else "none"
                        rr_label = "/".join(f"{k[0]}{v}" for k, v in sess_rr.items()) if sess_rr else f"def{rr_def}"
                        name = f"R{risk}_B{bs_label}_RR{rr_label}_1at{one_at}"
                        variants.append(StrategyParams(
                            name=name,
                            risk_percent=risk,
                            blocked_sessions=blocked_s,
                            session_rr=sess_rr,
                            rr_default=rr_def,
                            one_symbol_at_a_time=one_at,
                        ))

    # Phase 2 : sweep filtres (H1, cooldown, time exit, ATR SL) avec best risk/session
    for h1 in h1_options:
        for cd in cooldowns:
            for te in time_exits:
                for atr_sl in atr_sl_mults:
                    for bd in blocked_days_options:
                        idx += 1
                        name = f"F_H1{h1}_CD{cd}_TE{te}_ATR{atr_sl}_BD{''.join(map(str,bd)) or 'none'}"
                        variants.append(StrategyParams(
                            name=name,
                            risk_percent=2,  # Risk modere pour phase 2
                            blocked_sessions=["US"],
                            session_rr={"EUROPE": 2.5, "ASIA": 2.0},
                            rr_default=2.0,
                            one_symbol_at_a_time=False,
                            use_h1_trend_filter=h1,
                            cooldown_after_loss=cd,
                            max_trade_duration_minutes=te,
                            atr_sl_multiplier=atr_sl,
                            blocked_days=bd,
                        ))

    print(f"  {len(variants)} variantes generees")
    return variants


def generate_quick_variants() -> List[StrategyParams]:
    """Version rapide : ~100 variantes les plus impactantes."""
    variants = []

    # Axe 1 : Risk x Sessions bloquees (risk est le plus impactant)
    for risk in [1, 2, 5]:
        for blocked_s in [[], ["US"], ["US", "ASIA"], ["ASIA"]]:
            for one_at in [True, False]:
                bs = "+".join(blocked_s) if blocked_s else "none"
                variants.append(StrategyParams(
                    name=f"A1_R{risk}_B{bs}_1at{one_at}",
                    risk_percent=risk, blocked_sessions=blocked_s,
                    one_symbol_at_a_time=one_at,
                ))

    # Axe 2 : R:R par session (avec risk=2, block US, H1 on)
    for rr_eu in [1.5, 2.0, 2.5, 3.0]:
        for rr_asia in [1.5, 2.0, 2.5, 3.0]:
            variants.append(StrategyParams(
                name=f"A2_EU{rr_eu}_AS{rr_asia}",
                risk_percent=2, blocked_sessions=["US"],
                session_rr={"EUROPE": rr_eu, "ASIA": rr_asia}, rr_default=2.0,
            ))

    # Axe 3 : H1 + cooldown + time exit + ATR SL
    for h1 in [True, False]:
        for cd in [0, 2, 4]:
            for te in [0, 120, 210, 360]:
                for atr_sl in [1.0, 1.5, 2.0]:
                    variants.append(StrategyParams(
                        name=f"A3_H1{h1}_CD{cd}_TE{te}_SL{atr_sl}",
                        risk_percent=2, blocked_sessions=["US"],
                        session_rr={"EUROPE": 2.5, "ASIA": 2.0},
                        use_h1_trend_filter=h1, cooldown_after_loss=cd,
                        max_trade_duration_minutes=te, atr_sl_multiplier=atr_sl,
                    ))

    # Axe 4 : Jours bloques
    for bd in [[], [4], [1, 3], [0, 4], [1, 3, 4]]:
        variants.append(StrategyParams(
            name=f"A4_BD{''.join(map(str,bd)) or 'none'}",
            risk_percent=2, blocked_sessions=["US"],
            session_rr={"EUROPE": 2.5, "ASIA": 2.0},
            blocked_days=bd,
        ))

    print(f"  {len(variants)} variantes generees (mode rapide)")
    return variants


# ============================================================================
# RUNNER
# ============================================================================
def _run_single(args):
    """Wrapper pour multiprocessing."""
    params, m5_data, h1_data, sym_info = args
    return run_backtest(params, m5_data, h1_data, sym_info, silent=True)


def run_optimization(variants: List[StrategyParams],
                     m5_data, h1_data, sym_info,
                     n_workers: int = 1) -> List[BacktestResult]:
    """Execute toutes les variantes."""
    results = []
    total = len(variants)

    # Sequentiel (plus simple, evite les problemes de pickle avec DataFrame)
    for i, params in enumerate(variants):
        r = run_backtest(params, m5_data, h1_data, sym_info, silent=True)
        results.append(r)
        if (i + 1) % 50 == 0 or i + 1 == total:
            print(f"   {i+1}/{total} variantes...")

    return results


# ============================================================================
# DETAILED REPORT
# ============================================================================
def print_detailed_report(result: BacktestResult, file=None):
    """Affiche un rapport ultra-detaille d'un backtest."""
    p = result.params
    trades = result.trades

    def out(msg=""):
        print(msg, file=file)

    out("=" * 80)
    out(f"RAPPORT DETAILLE : {p.name}")
    out("=" * 80)

    # Config
    out(f"\n--- CONFIGURATION ---")
    out(f"  Risk: {p.risk_percent}% | RR defaut: {p.rr_default}")
    out(f"  Sessions bloquees: {p.blocked_sessions or 'aucune'}")
    out(f"  R:R par session: {p.session_rr or 'defaut partout'}")
    out(f"  1 symbole a la fois: {p.one_symbol_at_a_time}")
    out(f"  H1 trend filter: {p.use_h1_trend_filter} (bars={p.h1_bars_required})")
    out(f"  Cooldown: {p.cooldown_after_loss} barres | Time exit: {p.max_trade_duration_minutes} min")
    out(f"  ATR SL mult: {p.atr_sl_multiplier} | Jours bloques: {p.blocked_days or 'aucun'}")

    # Resultats globaux
    out(f"\n--- RESULTATS GLOBAUX ---")
    out(f"  Balance: {p.initial_balance:.0f} -> {result.final_balance:.0f} ({result.return_pct:+.1f}%)")
    out(f"  Trades: {result.total_trades} | WR: {result.win_rate:.1f}%")
    out(f"  Profit Factor: {result.profit_factor:.2f}")
    out(f"  Gain moyen: {result.avg_win:.2f} | Perte moyenne: {result.avg_loss:.2f}")
    out(f"  Meilleur: {result.best_trade:.2f} | Pire: {result.worst_trade:.2f}")
    out(f"  R-multiple moyen: {result.avg_r_multiple:.3f}")
    out(f"  Max drawdown: {result.max_drawdown:.0f} ({result.max_drawdown_pct:.1f}%)")
    out(f"  Max pertes consecutives: {result.max_consecutive_losses}")

    if not trades:
        out("\n  Aucun trade.")
        return

    # Par session
    out(f"\n--- PAR SESSION ---")
    out(f"  {'Session':10s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Avg R':>8s} {'PF':>6s}")
    for sess in ["ASIA", "EUROPE", "US"]:
        st = [t for t in trades if t.session == sess]
        if st:
            w = len([t for t in st if t.profit > 0])
            pnl = sum(t.profit for t in st)
            wr = w / len(st) * 100
            avg_r = sum(t.r_multiple for t in st) / len(st)
            tw = sum(t.profit for t in st if t.profit > 0)
            tl = abs(sum(t.profit for t in st if t.profit <= 0))
            pf = tw / tl if tl > 0 else float('inf')
            out(f"  {sess:10s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_r:+8.3f} {pf:6.2f}")

    # Par symbole
    out(f"\n--- PAR SYMBOLE ---")
    out(f"  {'Symbole':14s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Avg R':>8s}")
    for sym in sorted(set(t.symbol for t in trades)):
        st = [t for t in trades if t.symbol == sym]
        w = len([t for t in st if t.profit > 0])
        pnl = sum(t.profit for t in st)
        wr = w / len(st) * 100
        avg_r = sum(t.r_multiple for t in st) / len(st)
        out(f"  {sym:14s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_r:+8.3f}")

    # Par jour
    out(f"\n--- PAR JOUR ---")
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    out(f"  {'Jour':12s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Avg R':>8s}")
    for d in range(7):
        st = [t for t in trades if t.day_of_week == d]
        if st:
            w = len([t for t in st if t.profit > 0])
            pnl = sum(t.profit for t in st)
            wr = w / len(st) * 100
            avg_r = sum(t.r_multiple for t in st) / len(st)
            out(f"  {days[d]:12s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_r:+8.3f}")

    # Par heure
    out(f"\n--- PAR HEURE (UTC) ---")
    out(f"  {'Heure':>5s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Avg R':>8s}")
    for h in range(24):
        st = [t for t in trades if t.hour == h]
        if st:
            w = len([t for t in st if t.profit > 0])
            pnl = sum(t.profit for t in st)
            wr = w / len(st) * 100
            avg_r = sum(t.r_multiple for t in st) / len(st)
            marker = " ***" if wr >= 50 else " !" if wr < 30 else ""
            out(f"  {h:5d} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_r:+8.3f}{marker}")

    # Par mois
    out(f"\n--- PAR MOIS ---")
    out(f"  {'Mois':>8s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s}")
    months = sorted(set(t.month for t in trades))
    for m in months:
        st = [t for t in trades if t.month == m]
        w = len([t for t in st if t.profit > 0])
        pnl = sum(t.profit for t in st)
        wr = w / len(st) * 100
        out(f"  {m:>8s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f}")

    # Par direction
    out(f"\n--- PAR DIRECTION ---")
    out(f"  {'Dir':>6s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Avg R':>8s}")
    for d in ["LONG", "SHORT"]:
        st = [t for t in trades if t.trade_type == d]
        if st:
            w = len([t for t in st if t.profit > 0])
            pnl = sum(t.profit for t in st)
            wr = w / len(st) * 100
            avg_r = sum(t.r_multiple for t in st) / len(st)
            out(f"  {d:>6s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_r:+8.3f}")

    # Par raison de sortie
    out(f"\n--- PAR SORTIE ---")
    out(f"  {'Raison':>6s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s} {'Duree moy':>10s}")
    for reason in ["TP", "SL", "TIME", "END"]:
        st = [t for t in trades if t.exit_reason == reason]
        if st:
            w = len([t for t in st if t.profit > 0])
            pnl = sum(t.profit for t in st)
            wr = w / len(st) * 100
            avg_dur = sum(t.duration_minutes for t in st) / len(st)
            out(f"  {reason:>6s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f} {avg_dur:9.0f}m")

    # Par session + symbole (combo)
    out(f"\n--- COMBOS SESSION x SYMBOLE ---")
    out(f"  {'Combo':24s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s}")
    combos = defaultdict(list)
    for t in trades:
        combos[f"{t.session} x {t.symbol}"].append(t)
    for combo, st in sorted(combos.items(), key=lambda x: sum(t.profit for t in x[1]), reverse=True):
        w = len([t for t in st if t.profit > 0])
        pnl = sum(t.profit for t in st)
        wr = w / len(st) * 100
        out(f"  {combo:24s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f}")

    # Par session + jour (combo)
    out(f"\n--- COMBOS SESSION x JOUR ---")
    out(f"  {'Combo':24s} {'Trades':>6s} {'WR':>7s} {'PnL':>10s}")
    combos2 = defaultdict(list)
    for t in trades:
        combos2[f"{t.session} x {days[t.day_of_week]}"].append(t)
    for combo, st in sorted(combos2.items(), key=lambda x: sum(t.profit for t in x[1]), reverse=True):
        w = len([t for t in st if t.profit > 0])
        pnl = sum(t.profit for t in st)
        wr = w / len(st) * 100
        out(f"  {combo:24s} {len(st):6d} {wr:6.1f}% {pnl:+10.0f}")

    # Streaks
    out(f"\n--- STREAKS ---")
    win_streak = 0
    loss_streak = 0
    max_ws = 0
    max_ls = 0
    for t in trades:
        if t.profit > 0:
            win_streak += 1
            loss_streak = 0
            max_ws = max(max_ws, win_streak)
        else:
            loss_streak += 1
            win_streak = 0
            max_ls = max(max_ls, loss_streak)
    out(f"  Max wins consecutifs: {max_ws}")
    out(f"  Max losses consecutifs: {max_ls}")

    # Distribution R-multiples
    out(f"\n--- DISTRIBUTION R-MULTIPLES ---")
    r_vals = [t.r_multiple for t in trades]
    bins = [(-999, -2), (-2, -1), (-1, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 999)]
    for lo, hi in bins:
        count = len([r for r in r_vals if lo <= r < hi])
        if count > 0:
            label = f"[{lo:+.1f}, {hi:+.1f})" if hi < 999 else f"[{lo:+.1f}, +inf)"
            if lo == -999:
                label = f"(-inf, {hi:+.1f})"
            bar = "#" * min(count, 60)
            out(f"  {label:16s} {count:4d} {bar}")

    # Monte Carlo
    out(f"\n--- SIMULATION MONTE CARLO (10 000 trajectoires) ---")
    mc = monte_carlo_simulation(trades, result.params.initial_balance, risk_pct=result.params.risk_percent)
    if mc:
        out(f"  Rendement median:  {mc['median_return']:+.1f}%")
        out(f"  Rendement moyen:   {mc['mean_return']:+.1f}%")
        out(f"  Pire 5%:           {mc['p5_return']:+.1f}%  (balance: {mc['p5_balance']:.0f})")
        out(f"  Meilleur 5%:       {mc['p95_return']:+.1f}%  (balance: {mc['p95_balance']:.0f})")
        out(f"  Pire 1%:           {mc['p1_return']:+.1f}%  (balance: {mc['p1_balance']:.0f})")
        out(f"  Prob. profit > 0:  {mc['prob_profit']:.1f}%")
        out(f"  Prob. perte > 50%: {mc['prob_ruin_50']:.1f}%")
        out(f"  Prob. perte > 90%: {mc['prob_ruin_90']:.1f}%")
        out(f"  Max DD median:     {mc['median_max_dd']:.0f} ({mc['median_max_dd_pct']:.1f}%)")
        out(f"  Max DD pire 5%:    {mc['p95_max_dd']:.0f} ({mc['p95_max_dd_pct']:.1f}%)")

    out("\n" + "=" * 80)


def monte_carlo_simulation(trades: List[TradeResult], initial_balance: float,
                           n_simulations: int = 10000, risk_pct: float = 5.0) -> Dict:
    """Simulation Monte Carlo avec compounding reel.

    Reshuffle l'ordre des trades et recalcule le P&L en recomposant
    la taille de position (risk_pct * balance courante) a chaque trade.
    Cela donne une distribution REELLE des resultats possibles.
    """
    import numpy as np

    if not trades or len(trades) < 5:
        return {}

    r_multiples = np.array([t.r_multiple for t in trades])
    n_trades = len(r_multiples)
    rng = np.random.default_rng(42)

    final_balances = np.zeros(n_simulations)
    max_drawdowns = np.zeros(n_simulations)
    max_dd_pcts = np.zeros(n_simulations)

    for sim in range(n_simulations):
        shuffled_r = rng.permutation(r_multiples)
        balance = initial_balance
        peak = balance
        max_dd = 0.0
        max_dd_pct = 0.0

        for r in shuffled_r:
            risk_amount = balance * (risk_pct / 100.0)
            profit = r * risk_amount
            balance += profit
            if balance <= 0:
                balance = 0
                max_dd = peak
                max_dd_pct = 100.0
                break
            if balance > peak:
                peak = balance
            dd = peak - balance
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak * 100) if peak > 0 else 0

        final_balances[sim] = balance
        max_drawdowns[sim] = max_dd
        max_dd_pcts[sim] = max_dd_pct

    returns = (final_balances - initial_balance) / initial_balance * 100

    return {
        'median_return': float(np.median(returns)),
        'mean_return': float(np.mean(returns)),
        'p1_return': float(np.percentile(returns, 1)),
        'p5_return': float(np.percentile(returns, 5)),
        'p95_return': float(np.percentile(returns, 95)),
        'p1_balance': float(np.percentile(final_balances, 1)),
        'p5_balance': float(np.percentile(final_balances, 5)),
        'p95_balance': float(np.percentile(final_balances, 95)),
        'prob_profit': float(np.mean(returns > 0) * 100),
        'prob_ruin_50': float(np.mean(returns < -50) * 100),
        'prob_ruin_90': float(np.mean(returns < -90) * 100),
        'median_max_dd': float(np.median(max_drawdowns)),
        'median_max_dd_pct': float(np.median(max_dd_pcts)),
        'p95_max_dd': float(np.percentile(max_drawdowns, 95)),
        'p95_max_dd_pct': float(np.percentile(max_dd_pcts, 95)),
    }


def export_trades_csv(trades: List[TradeResult], path: str):
    """Exporte tous les trades en CSV detaille."""
    rows = []
    for t in trades:
        rows.append({
            'symbol': t.symbol, 'type': t.trade_type,
            'entry_time': t.entry_time, 'exit_time': t.exit_time,
            'entry_price': t.entry_price, 'exit_price': t.exit_price,
            'stop_loss': t.stop_loss, 'take_profit': t.take_profit,
            'lot_size': t.lot_size, 'profit': round(t.profit, 2),
            'exit_reason': t.exit_reason,
            'session': t.session, 'day_of_week': t.day_of_week,
            'hour': t.hour, 'month': t.month,
            'duration_min': round(t.duration_minutes, 1),
            'r_multiple': round(t.r_multiple, 3),
            'rr_ratio_used': t.rr_ratio_used,
            'risk_amount': round(t.risk_amount, 2),
            'balance_after': round(t.balance_after, 2),
            'atr_at_entry': round(t.atr_at_entry, 4),
            'sl_distance_pts': round(t.sl_distance_pts, 2),
            'sl_distance_pct': round(t.sl_distance_pct, 5),
        })
    pd.DataFrame(rows).to_csv(path, index=False)


# ============================================================================
# MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Optimiseur de strategie")
    parser.add_argument("--months", type=int, default=0, help="Limiter aux N derniers mois")
    parser.add_argument("--top", type=int, default=10, help="Nombre de meilleurs resultats")
    parser.add_argument("--quick", action="store_true", help="Scan rapide (moins de variantes)")
    args = parser.parse_args()

    print("=" * 80)
    print("OPTIMISEUR DE STRATEGIE - EMA 20 / SMA 50")
    print("=" * 80)

    # Load data once
    all_symbols = ["DJ30.", "NAS100.", "SP500."]
    print("\nChargement des donnees...")
    m5_data, h1_data = load_all_data(all_symbols, DATA_DIR, args.months)
    sym_info = load_symbols_info(DATA_DIR)
    for sym, df in m5_data.items():
        days = (df.index[-1] - df.index[0]).days
        print(f"  {sym}: {len(df)} barres M5 ({days}j)")

    # Generate variants
    print("\nGeneration des variantes...")
    if args.quick:
        variants = generate_quick_variants()
    else:
        variants = generate_variants()

    # Run
    print(f"\nLancement de {len(variants)} backtests...")
    t0 = datetime.now()
    results = run_optimization(variants, m5_data, h1_data, sym_info)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\nTermine en {elapsed:.1f}s ({elapsed/len(variants)*1000:.0f}ms/variante)")

    # Sort by return %
    results.sort(key=lambda r: r.return_pct, reverse=True)

    # Summary table
    print(f"\n{'=' * 80}")
    print(f"TOP {args.top} VARIANTES (sur {len(results)})")
    print(f"{'=' * 80}")
    print(f"{'#':>3s} {'Nom':40s} {'Ret%':>8s} {'WR':>6s} {'PF':>6s} {'Trades':>6s} {'DD%':>6s} {'MaxL':>4s} {'AvgR':>7s}")
    print("-" * 90)
    for i, r in enumerate(results[:args.top]):
        print(f"{i+1:3d} {r.params.name:40s} {r.return_pct:+7.1f}% {r.win_rate:5.1f}% {r.profit_factor:5.2f} {r.total_trades:6d} {r.max_drawdown_pct:5.1f}% {r.max_consecutive_losses:4d} {r.avg_r_multiple:+6.3f}")

    # Worst
    print(f"\nPIRES 5:")
    for r in results[-5:]:
        print(f"  {r.params.name:40s} {r.return_pct:+7.1f}% WR={r.win_rate:.1f}% PF={r.profit_factor:.2f}")

    # Export results CSV
    rows = []
    for r in results:
        p = r.params
        rows.append({
            'name': p.name, 'risk_pct': p.risk_percent,
            'blocked_sessions': str(p.blocked_sessions),
            'session_rr': str(p.session_rr),
            'rr_default': p.rr_default,
            'one_at_a_time': p.one_symbol_at_a_time,
            'h1_filter': p.use_h1_trend_filter,
            'cooldown': p.cooldown_after_loss,
            'time_exit_min': p.max_trade_duration_minutes,
            'atr_sl_mult': p.atr_sl_multiplier,
            'blocked_days': str(p.blocked_days),
            'return_pct': round(r.return_pct, 2),
            'net_profit': round(r.net_profit, 2),
            'total_trades': r.total_trades,
            'win_rate': round(r.win_rate, 2),
            'profit_factor': round(r.profit_factor, 2),
            'max_drawdown': round(r.max_drawdown, 2),
            'max_drawdown_pct': round(r.max_drawdown_pct, 2),
            'avg_win': round(r.avg_win, 2),
            'avg_loss': round(r.avg_loss, 2),
            'avg_r_multiple': round(r.avg_r_multiple, 4),
            'max_consecutive_losses': r.max_consecutive_losses,
            'best_trade': round(r.best_trade, 2),
            'worst_trade': round(r.worst_trade, 2),
        })
    df_results = pd.DataFrame(rows)
    results_path = os.path.join(DATA_DIR, "optimizer_results.csv")
    df_results.to_csv(results_path, index=False)
    print(f"\nResultats: {results_path}")

    # Detailed report for best variant
    best = results[0]
    print(f"\n{'=' * 80}")
    print(f"RAPPORT DETAILLE DE LA MEILLEURE VARIANTE")
    print_detailed_report(best)

    # Export to file
    report_path = os.path.join(DATA_DIR, "optimizer_report.txt")
    with open(report_path, 'w') as f:
        for i, r in enumerate(results[:args.top]):
            print_detailed_report(r, file=f)
            f.write("\n\n")
    print(f"Rapport: {report_path}")

    # Export best trades CSV
    trades_path = os.path.join(DATA_DIR, "optimizer_best_trades.csv")
    export_trades_csv(best.trades, trades_path)
    print(f"Trades detailles: {trades_path}")


if __name__ == "__main__":
    main()
