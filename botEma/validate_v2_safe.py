#!/usr/bin/env python3
"""
Validation V2_SAFE avec corrections de fiabilite:
  1. Spread/slippage inclus (3 pts par defaut)
  2. Monte Carlo avec compounding reel
  3. Validation train/test (70/30)
  4. Warm-up indicateurs 200 barres
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_engine import (
    StrategyParams, load_all_data, load_symbols_info, run_backtest, DATA_DIR,
)
from optimizer import print_detailed_report, export_trades_csv, monte_carlo_simulation


V2_SAFE = StrategyParams(
    name='V2_SAFE',
    risk_percent=5,
    blocked_sessions=['US', 'ASIA'],
    session_rr={'EUROPE': 3.5},
    rr_default=3.5,
    one_symbol_at_a_time=False,
    use_h1_trend_filter=True,
    h1_bars_required=2,
    cooldown_after_loss=2,
    max_trade_duration_minutes=360,
    atr_sl_multiplier=2.0,
    allowed_hours=[8, 10, 11, 12],
    blocked_days=[2],
    spread_per_symbol={"DJ30.": 4.0, "NAS100.": 2.0, "SP500.": 0.7},
)


def split_data(m5_data, h1_data, train_ratio=0.70):
    """Split temporel 70/30 sur les donnees M5 et H1."""
    m5_train, m5_test = {}, {}
    h1_train, h1_test = {}, {}

    for sym, df in m5_data.items():
        n = len(df)
        split_idx = int(n * train_ratio)
        split_time = df.index[split_idx]

        m5_train[sym] = df.iloc[:split_idx].copy()
        m5_test[sym] = df.iloc[split_idx:].copy()

        if sym in h1_data:
            h1_df = h1_data[sym]
            h1_train[sym] = h1_df[h1_df.index < split_time].copy()
            h1_test[sym] = h1_df[h1_df.index >= split_time].copy()

    return m5_train, h1_train, m5_test, h1_test, split_time


def print_comparison(label, result):
    """Affiche une ligne de comparaison."""
    r = result
    days = 0
    if r.trades:
        first = r.trades[0].entry_time
        last = r.trades[-1].exit_time or r.trades[-1].entry_time
        days = (last - first).days
    print(f"  {label:20s} {r.return_pct:+8.1f}% | WR {r.win_rate:5.1f}% | "
          f"PF {r.profit_factor:5.2f} | {r.total_trades:3d} trades | "
          f"DD {r.max_drawdown_pct:5.1f}% | MaxL {r.max_consecutive_losses:2d} | "
          f"AvgR {r.avg_r_multiple:+6.3f} | {days}j")


def main():
    print("=" * 90)
    print("VALIDATION V2_SAFE - Backtest corrige (spread, warm-up, Monte Carlo, train/test)")
    print("=" * 90)

    symbols = ["DJ30.", "NAS100.", "SP500."]
    print("\nChargement des donnees...")
    m5, h1 = load_all_data(symbols)
    si = load_symbols_info()
    for sym, df in m5.items():
        days = (df.index[-1] - df.index[0]).days
        print(f"  {sym}: {len(df)} barres M5 ({days}j)")

    # Split train/test
    m5_train, h1_train, m5_test, h1_test, split_time = split_data(m5, h1, 0.70)
    print(f"\nSplit temporel au {split_time.strftime('%Y-%m-%d %H:%M')}:")
    for sym in symbols:
        if sym in m5_train:
            t0 = m5_train[sym].index[0].strftime('%Y-%m-%d')
            t1 = m5_train[sym].index[-1].strftime('%Y-%m-%d')
            t2 = m5_test[sym].index[0].strftime('%Y-%m-%d')
            t3 = m5_test[sym].index[-1].strftime('%Y-%m-%d')
            print(f"  {sym}: TRAIN {t0} -> {t1} ({len(m5_train[sym])} bars) | "
                  f"TEST {t2} -> {t3} ({len(m5_test[sym])} bars)")

    # ================================================================
    # 1. BACKTEST COMPLET (avec spread)
    # ================================================================
    print(f"\n{'='*90}")
    print("1. BACKTEST COMPLET (spread par symbole: DJ30=4, NAS100=2, SP500=0.7, warm-up=200 bars)")
    print(f"{'='*90}")

    t0 = datetime.now()
    result_full = run_backtest(V2_SAFE, m5, h1, si, silent=True)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"  Termine en {elapsed:.1f}s")
    print_comparison("FULL (avec spread)", result_full)

    # Comparaison sans spread
    from dataclasses import asdict
    v2_no_spread = StrategyParams(**{
        k: getattr(V2_SAFE, k) for k in V2_SAFE.__dataclass_fields__
    })
    v2_no_spread.spread_points = 0.0
    v2_no_spread.spread_per_symbol = {}
    v2_no_spread.name = "V2_SAFE_no_spread"
    result_no_spread = run_backtest(v2_no_spread, m5, h1, si, silent=True)
    print_comparison("FULL (sans spread)", result_no_spread)

    spread_impact = result_no_spread.return_pct - result_full.return_pct
    print(f"\n  Impact du spread: -{spread_impact:.1f}% de rendement")

    # ================================================================
    # 2. TRAIN / TEST SPLIT
    # ================================================================
    print(f"\n{'='*90}")
    print("2. VALIDATION TRAIN / TEST (70% / 30%)")
    print(f"{'='*90}")

    result_train = run_backtest(V2_SAFE, m5_train, h1_train, si, silent=True)
    result_test = run_backtest(V2_SAFE, m5_test, h1_test, si, silent=True)

    print_comparison("TRAIN (70%)", result_train)
    print_comparison("TEST  (30%)", result_test)

    # Ratio de degradation
    if result_train.return_pct > 0 and result_test.return_pct != 0:
        # Annualiser pour comparer equitablement
        train_days = 0
        test_days = 0
        if result_train.trades:
            train_days = max(1, (result_train.trades[-1].entry_time - result_train.trades[0].entry_time).days)
        if result_test.trades:
            test_days = max(1, (result_test.trades[-1].entry_time - result_test.trades[0].entry_time).days)

        train_annual = result_train.return_pct / max(1, train_days) * 365
        test_annual = result_test.return_pct / max(1, test_days) * 365

        print(f"\n  Rendement annualise:")
        print(f"    TRAIN: {train_annual:+.1f}%/an ({train_days}j)")
        print(f"    TEST:  {test_annual:+.1f}%/an ({test_days}j)")
        if train_annual > 0:
            retention = test_annual / train_annual * 100
            print(f"    Retention: {retention:.0f}% (>70% = bon, <50% = overfitting probable)")

    # ================================================================
    # 3. MONTE CARLO (compounding reel)
    # ================================================================
    print(f"\n{'='*90}")
    print("3. MONTE CARLO - 10 000 trajectoires (compounding reel)")
    print(f"{'='*90}")

    mc = monte_carlo_simulation(result_full.trades, V2_SAFE.initial_balance,
                                risk_pct=V2_SAFE.risk_percent)
    if mc:
        print(f"  Rendement median:  {mc['median_return']:+.1f}%")
        print(f"  Rendement moyen:   {mc['mean_return']:+.1f}%")
        print(f"  Pire 5%:           {mc['p5_return']:+.1f}%  (balance: {mc['p5_balance']:.0f})")
        print(f"  Meilleur 5%:       {mc['p95_return']:+.1f}%  (balance: {mc['p95_balance']:.0f})")
        print(f"  Pire 1%:           {mc['p1_return']:+.1f}%  (balance: {mc['p1_balance']:.0f})")
        print(f"  Prob. profit > 0:  {mc['prob_profit']:.1f}%")
        print(f"  Prob. perte > 50%: {mc['prob_ruin_50']:.1f}%")
        print(f"  Prob. perte > 90%: {mc['prob_ruin_90']:.1f}%")
        print(f"  Max DD median:     {mc['median_max_dd']:.0f} ({mc['median_max_dd_pct']:.1f}%)")
        print(f"  Max DD pire 5%:    {mc['p95_max_dd']:.0f} ({mc['p95_max_dd_pct']:.1f}%)")

    # ================================================================
    # 4. RAPPORT DETAILLE (avec spread)
    # ================================================================
    print(f"\n{'='*90}")
    print("4. RAPPORT DETAILLE V2_SAFE (corrige)")
    print_detailed_report(result_full)

    # Export
    bv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_version")
    os.makedirs(bv_dir, exist_ok=True)
    export_trades_csv(result_full.trades, os.path.join(bv_dir, "v2_safe_validated_trades.csv"))

    # Sauvegarder le rapport
    with open(os.path.join(bv_dir, "v2_safe_validation.txt"), 'w') as f:
        f.write(f"VALIDATION V2_SAFE - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"{'='*70}\n\n")
        f.write(f"Corrections appliquees:\n")
        f.write(f"  - Spread/slippage par symbole: {V2_SAFE.spread_per_symbol}\n")
        f.write(f"  - Warm-up indicateurs: 200 barres (vs 60)\n")
        f.write(f"  - Monte Carlo avec compounding reel\n")
        f.write(f"  - Validation train/test 70/30\n\n")
        f.write(f"RESULTATS:\n")
        f.write(f"  FULL:  {result_full.return_pct:+.1f}% | WR {result_full.win_rate:.1f}% | PF {result_full.profit_factor:.2f} | {result_full.total_trades} trades | DD {result_full.max_drawdown_pct:.1f}%\n")
        f.write(f"  TRAIN: {result_train.return_pct:+.1f}% | WR {result_train.win_rate:.1f}% | PF {result_train.profit_factor:.2f} | {result_train.total_trades} trades | DD {result_train.max_drawdown_pct:.1f}%\n")
        f.write(f"  TEST:  {result_test.return_pct:+.1f}% | WR {result_test.win_rate:.1f}% | PF {result_test.profit_factor:.2f} | {result_test.total_trades} trades | DD {result_test.max_drawdown_pct:.1f}%\n")
        if mc:
            f.write(f"\nMONTE CARLO (10k trajectoires):\n")
            f.write(f"  Median: {mc['median_return']:+.1f}% | Pire 5%: {mc['p5_return']:+.1f}% | Pire 1%: {mc['p1_return']:+.1f}%\n")
            f.write(f"  Prob profit: {mc['prob_profit']:.1f}% | Max DD median: {mc['median_max_dd_pct']:.1f}%\n")

    print(f"\nFichiers generes:")
    print(f"  best_version/v2_safe_validated_trades.csv")
    print(f"  best_version/v2_safe_validation.txt")


if __name__ == "__main__":
    main()
