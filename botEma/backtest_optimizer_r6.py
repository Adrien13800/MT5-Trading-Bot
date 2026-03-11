#!/usr/bin/env python3
"""
backtest_optimizer_r6.py - Round 6: Filtres session/jour + params adaptatifs

Constats R5 analytics:
  - US session: WR 32%, -4301$ => gros drag sur performance
  - EUROPE: WR 44%, +8738$ / ASIA: WR 43.4%, +5874$
  - Mercredi: -506$, Vendredi: +72$ (quasi neutres)
  - TRENDING: WR 12.5%, -961$ / RANGING: WR 39.7%, +11272$

Axes R6:
  1. Bloquer session US (ou reduire)
  2. Bloquer jours faibles (mer/ven)
  3. Params adaptatifs par session (RR, time exit)
  4. Combos session + jour
"""

import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'backtest'))

from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Optional
import pandas as pd
import numpy as np

import MetaTrader5 as mt5
import strategy_core
from strategy_core import TradeType, TradingSession
from backtest_optimizer_r4 import (
    load_config, connect_mt5, load_data, get_symbol_info,
    calc_lot, calc_profit, SimTrade, check_cross_signal_custom
)


def get_session(dt):
    """Retourne la session pour un datetime donne."""
    h = dt.hour
    if 0 <= h < 8:
        return "ASIA"
    elif 8 <= h < 14:
        return "EUROPE"
    elif 14 <= h < 21:
        return "US"
    return "OFF"


def run_variant_r6(m5_data, h1_data, cfg,
                   # Base params
                   rr_flat=2.0, rr_trending=2.0,
                   atr_sl_mult=1.5,
                   max_trade_minutes=210,
                   cooldown_bars=2,
                   h1_bars=2, use_h1=True,
                   # R6: Session filter
                   blocked_sessions=None,  # e.g. ["US"]
                   # R6: Day filter
                   blocked_days=None,  # e.g. [2, 4] for Wed, Fri
                   # R6: Session-specific RR override
                   session_rr=None,  # e.g. {"EUROPE": 2.5, "ASIA": 2.0, "US": 1.5}
                   # R6: Session-specific time exit override
                   session_time_exit=None,  # e.g. {"EUROPE": 300, "ASIA": 210, "US": 150}
                   # R6: Limit US to EUROPE+ASIA hours only (no block, just narrow window)
                   us_early_cutoff=None,  # e.g. 17 = stop new entries after 17h
                   ):

    if blocked_sessions is None:
        blocked_sessions = []
    if blocked_days is None:
        blocked_days = []

    symbols = cfg['symbols']
    use_daily_pref = cfg.get('use_daily_preferred_symbol', True)
    one_at_a_time = cfg.get('one_symbol_at_a_time', True)
    pref_by_day = cfg.get('preferred_symbol_by_day') or {}
    risk_pct = cfg.get('risk_percent', 1.0)
    balance = cfg.get('initial_balance', 10000.0)

    orig_rr_f = strategy_core.RISK_REWARD_RATIO_FLAT
    orig_rr_t = strategy_core.RISK_REWARD_RATIO_TRENDING
    orig_atr = strategy_core.ATR_SL_MULTIPLIER
    strategy_core.RISK_REWARD_RATIO_FLAT = rr_flat
    strategy_core.RISK_REWARD_RATIO_TRENDING = rr_trending
    strategy_core.ATR_SL_MULTIPLIER = atr_sl_mult

    events = []
    for sym in symbols:
        if sym not in m5_data:
            continue
        d = m5_data[sym]
        for i in range(50, len(d)):
            ts = d.index[i]
            if hasattr(ts, 'to_pydatetime'):
                ts = ts.to_pydatetime()
            events.append((ts, sym, i))
    events.sort(key=lambda x: x[0])

    open_trades: Dict[str, List[SimTrade]] = {}
    closed_trades: List[SimTrade] = []
    last_bar_time: Dict[str, datetime] = {}
    last_loss_time = None

    for current_bar_time, symbol, bar_index in events:
        df = m5_data[symbol]
        market_data = df.iloc[:bar_index + 1]
        current_bar = df.iloc[bar_index]

        # Trade management: SL/TP + Time exit (use session-specific time exit if set)
        if symbol in open_trades and open_trades[symbol]:
            to_close = []
            for idx, trade in enumerate(open_trades[symbol]):
                hit = None
                if trade.trade_type == TradeType.LONG:
                    if current_bar['low'] <= trade.stop_loss:
                        hit = ('SL', trade.stop_loss)
                    elif current_bar['high'] >= trade.take_profit:
                        hit = ('TP', trade.take_profit)
                else:
                    if current_bar['high'] >= trade.stop_loss:
                        hit = ('SL', trade.stop_loss)
                    elif current_bar['low'] <= trade.take_profit:
                        hit = ('TP', trade.take_profit)

                # Time exit (session-specific or global)
                if hit is None:
                    te = max_trade_minutes
                    if session_time_exit:
                        entry_session = get_session(trade.entry_time)
                        te = session_time_exit.get(entry_session, max_trade_minutes)
                    if te > 0:
                        elapsed = (current_bar_time - trade.entry_time).total_seconds() / 60
                        if elapsed >= te:
                            hit = ('TIME', float(current_bar['close']))

                if hit:
                    trade.exit_reason, trade.exit_price = hit
                    trade.exit_time = df.index[bar_index]
                    trade.profit = calc_profit(symbol, trade.entry_price, trade.exit_price,
                                               trade.lot_size, trade.trade_type)
                    balance += trade.profit
                    if trade.profit < 0:
                        last_loss_time = current_bar_time
                    closed_trades.append(trade)
                    to_close.append(idx)
            for idx in reversed(to_close):
                open_trades[symbol].pop(idx)
            if symbol in open_trades and not open_trades[symbol]:
                del open_trades[symbol]

        # Signal detection
        if symbol in last_bar_time and current_bar_time <= last_bar_time[symbol]:
            continue
        last_bar_time[symbol] = current_bar_time

        # R6: Block session
        session = get_session(current_bar_time)
        if session in blocked_sessions:
            continue

        # R6: US early cutoff
        if us_early_cutoff and session == "US" and current_bar_time.hour >= us_early_cutoff:
            continue

        # R6: Block day
        weekday = current_bar_time.weekday()
        if weekday in blocked_days:
            continue

        # Daily preferred symbol
        if use_daily_pref and pref_by_day:
            if pref_by_day.get(weekday) is not None and symbol != pref_by_day.get(weekday):
                continue

        # Cooldown
        if cooldown_bars > 0 and last_loss_time is not None:
            if (current_bar_time - last_loss_time).total_seconds() / 300 < cooldown_bars:
                continue

        def has_other_open():
            if not one_at_a_time:
                return False
            for s, tl in open_trades.items():
                if s != symbol and tl:
                    return True
            return False

        # H1 data
        df_h1_f = strategy_core.get_h1_data_at_time(
            h1_data.get(symbol, pd.DataFrame()), current_bar_time
        ) if symbol in h1_data else None

        # R6: Session-specific RR
        if session_rr and session in session_rr:
            strategy_core.RISK_REWARD_RATIO_FLAT = session_rr[session]
            strategy_core.RISK_REWARD_RATIO_TRENDING = session_rr[session]
        else:
            strategy_core.RISK_REWARD_RATIO_FLAT = rr_flat
            strategy_core.RISK_REWARD_RATIO_TRENDING = rr_trending

        for ttype, sl_fn, tp_sign in [
            (TradeType.LONG, strategy_core.calculate_sl_long, 1),
            (TradeType.SHORT, strategy_core.calculate_sl_short, -1),
        ]:
            signal = check_cross_signal_custom(
                market_data, df_h1_f, symbol, ttype,
                h1_bars=h1_bars, use_h1=use_h1,
            )
            if not signal:
                continue

            if bar_index + 1 < len(df):
                entry_price = float(df.iloc[bar_index + 1]['open'])
            else:
                entry_price = float(current_bar['close'])

            stop_loss = sl_fn(market_data, 10)

            if ttype == TradeType.LONG:
                stop_dist = entry_price - stop_loss
                if stop_loss <= 0 or stop_loss >= entry_price:
                    continue
            else:
                stop_dist = stop_loss - entry_price
                if stop_loss <= 0 or stop_loss <= entry_price:
                    continue

            sl_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0
            if sl_pct > 0.05:
                continue

            rr = strategy_core.get_risk_reward_ratio(market_data)
            take_profit = entry_price + (stop_dist * rr * tp_sign)
            lot = calc_lot(symbol, entry_price, stop_loss, balance, risk_pct)

            if lot <= 0 or has_other_open():
                continue

            trade = SimTrade(
                symbol=symbol, trade_type=ttype,
                entry_time=current_bar_time,
                entry_price=entry_price, stop_loss=stop_loss,
                take_profit=take_profit, lot_size=lot,
            )
            open_trades.setdefault(symbol, []).append(trade)

    strategy_core.RISK_REWARD_RATIO_FLAT = orig_rr_f
    strategy_core.RISK_REWARD_RATIO_TRENDING = orig_rr_t
    strategy_core.ATR_SL_MULTIPLIER = orig_atr

    return closed_trades, balance


def compute_stats(trades, initial, final):
    closed = [t for t in trades if t.exit_reason in ('SL', 'TP', 'TIME')]
    total = len(closed)
    if total == 0:
        return {'trades': 0, 'wr': 0, 'net': 0, 'pf': 0, 'rendement': 0,
                'avg_win': 0, 'avg_loss': 0, 'max_dd': 0, 'time_exits': 0}
    wins = [t for t in closed if t.profit > 0]
    losses = [t for t in closed if t.profit < 0]
    time_exits = [t for t in closed if t.exit_reason == 'TIME']
    tw = sum(t.profit for t in wins)
    tl = abs(sum(t.profit for t in losses))
    equity, peak, max_dd = initial, initial, 0
    for t in sorted(closed, key=lambda x: x.entry_time):
        equity += t.profit
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return {
        'trades': total, 'wins': len(wins), 'losses': len(losses),
        'wr': len(wins) / total * 100, 'net': final - initial,
        'pf': tw / tl if tl > 0 else 999,
        'rendement': (final - initial) / initial * 100,
        'avg_win': tw / len(wins) if wins else 0,
        'avg_loss': tl / len(losses) if losses else 0,
        'max_dd': max_dd,
        'time_exits': len(time_exits),
    }


def main():
    cfg = load_config()
    print("=" * 70)
    print("OPTIMISEUR R6 - Filtres session/jour + params adaptatifs")
    print("=" * 70)
    connect_mt5(cfg)
    print("\nChargement des donnees...")
    m5_data, h1_data = load_data(cfg['symbols'])
    initial = cfg.get('initial_balance', 10000.0)

    # Base R5 = Cd2 + Time42b (210 min)
    BASE = {}  # defaults in run_variant_r6

    variants = [
        # === REF ===
        ("BASE R5 (Cd2+Time42b)", {}),

        # === CAT 1: Block sessions ===
        ("Block US", {"blocked_sessions": ["US"]}),
        ("Block US+OFF", {"blocked_sessions": ["US", "OFF"]}),
        ("EUROPE only", {"blocked_sessions": ["US", "ASIA", "OFF"]}),
        ("ASIA only", {"blocked_sessions": ["US", "EUROPE", "OFF"]}),
        ("EUROPE+ASIA only", {"blocked_sessions": ["US", "OFF"]}),

        # === CAT 2: US cutoff (trade US mais arreter tot) ===
        ("US cutoff 16h", {"us_early_cutoff": 16}),
        ("US cutoff 17h", {"us_early_cutoff": 17}),
        ("US cutoff 18h", {"us_early_cutoff": 18}),

        # === CAT 3: Block days ===
        ("Block mercredi", {"blocked_days": [2]}),
        ("Block vendredi", {"blocked_days": [4]}),
        ("Block mer+ven", {"blocked_days": [2, 4]}),
        ("Block mer+ven+lundi", {"blocked_days": [0, 2, 4]}),

        # === CAT 4: Session + Day combos ===
        ("Block US + Block mer", {"blocked_sessions": ["US"], "blocked_days": [2]}),
        ("Block US + Block ven", {"blocked_sessions": ["US"], "blocked_days": [4]}),
        ("Block US + Block mer+ven", {"blocked_sessions": ["US"], "blocked_days": [2, 4]}),
        ("EU+ASIA + Block mer", {"blocked_sessions": ["US", "OFF"], "blocked_days": [2]}),
        ("EU+ASIA + Block mer+ven", {"blocked_sessions": ["US", "OFF"], "blocked_days": [2, 4]}),

        # === CAT 5: Session-specific RR ===
        ("RR EU=2.5 ASIA=2 US=1.5", {"session_rr": {"EUROPE": 2.5, "ASIA": 2.0, "US": 1.5}}),
        ("RR EU=2.5 ASIA=2.5 US=1.5", {"session_rr": {"EUROPE": 2.5, "ASIA": 2.5, "US": 1.5}}),
        ("RR EU=3 ASIA=2 US=1.5", {"session_rr": {"EUROPE": 3.0, "ASIA": 2.0, "US": 1.5}}),
        ("RR EU=2 ASIA=2 US=1.5", {"session_rr": {"EUROPE": 2.0, "ASIA": 2.0, "US": 1.5}}),

        # === CAT 6: Session-specific time exit ===
        ("TE EU=300 ASIA=210 US=150", {"session_time_exit": {"EUROPE": 300, "ASIA": 210, "US": 150}}),
        ("TE EU=300 ASIA=300 US=150", {"session_time_exit": {"EUROPE": 300, "ASIA": 300, "US": 150}}),
        ("TE EU=210 ASIA=210 US=120", {"session_time_exit": {"EUROPE": 210, "ASIA": 210, "US": 120}}),

        # === CAT 7: Block US + adjusted params ===
        ("Block US + RR EU=2.5 ASIA=2", {"blocked_sessions": ["US"], "session_rr": {"EUROPE": 2.5, "ASIA": 2.0}}),
        ("Block US + RR EU=3 ASIA=2", {"blocked_sessions": ["US"], "session_rr": {"EUROPE": 3.0, "ASIA": 2.0}}),
        ("Block US + TE EU=300 ASIA=210", {"blocked_sessions": ["US"], "session_time_exit": {"EUROPE": 300, "ASIA": 210}}),
        ("Block US + TE EU=300 ASIA=300", {"blocked_sessions": ["US"], "session_time_exit": {"EUROPE": 300, "ASIA": 300}}),

        # === CAT 8: Triple combos ===
        ("Block US+mer + RR EU=2.5", {"blocked_sessions": ["US"], "blocked_days": [2], "session_rr": {"EUROPE": 2.5, "ASIA": 2.0}}),
        ("Block US+ven + RR EU=2.5", {"blocked_sessions": ["US"], "blocked_days": [4], "session_rr": {"EUROPE": 2.5, "ASIA": 2.0}}),
        ("Block US + Blk mer + TE300", {"blocked_sessions": ["US"], "blocked_days": [2], "session_time_exit": {"EUROPE": 300, "ASIA": 300}}),
        ("Block US+mer+ven + RR EU=2.5", {"blocked_sessions": ["US"], "blocked_days": [2, 4], "session_rr": {"EUROPE": 2.5, "ASIA": 2.0}}),
        ("US cut17 + Block mer", {"us_early_cutoff": 17, "blocked_days": [2]}),
        ("US cut17 + RR EU=2.5 US=1.5", {"us_early_cutoff": 17, "session_rr": {"EUROPE": 2.5, "ASIA": 2.0, "US": 1.5}}),
    ]

    results = []
    n = len(variants)
    print(f"\nTest de {n} variantes...\n")

    for i, (name, kwargs) in enumerate(variants, 1):
        print(f"  [{i:2d}/{n}] {name}...", end=" ", flush=True)
        trades, final = run_variant_r6(m5_data, h1_data, cfg, **kwargs)
        stats = compute_stats(trades, initial, final)
        stats['name'] = name
        results.append(stats)
        print(f"{stats['trades']} trades | WR {stats['wr']:.1f}% | PF {stats['pf']:.2f} | "
              f"Rdt {stats['rendement']:.1f}% | DD {stats['max_dd']:.0f} | "
              f"TIME {stats['time_exits']}")

    # === CLASSEMENT PAR RENDEMENT ===
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
