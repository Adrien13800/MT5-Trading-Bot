#!/usr/bin/env python3
"""
Backtest standalone - Lit les CSV exportés depuis MT5.
Aucune dépendance à MetaTrader5. Tourne sur Mac/Linux/Windows.

Usage:
    python backtest_csv.py
    python backtest_csv.py --months 8
    python backtest_csv.py --data-dir ./data
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_core import (
    EMA_FAST, SMA_SLOW, RISK_REWARD_RATIO_FLAT, RISK_REWARD_RATIO_TRENDING,
    USE_ATR_FILTER, USE_ATR_SL, ATR_SL_MULTIPLIER,
    ALLOW_LONG, ALLOW_SHORT, USE_H1_TREND_FILTER,
    TradeType, TradingSession, MarketCondition, MarketTrend,
    compute_indicators,
    get_trading_session, is_valid_trading_session,
    is_sma50_flat, get_risk_reward_ratio,
    check_atr_filter,
    check_h1_trend,
    check_long_signal, check_short_signal,
    calculate_sl_long, calculate_sl_short, calculate_tp,
    get_h1_data_at_time,
    get_market_condition, get_market_trend,
    COOLDOWN_AFTER_LOSS, MAX_TRADE_DURATION_MINUTES,
    BLOCKED_SESSIONS, SESSION_RR,
)

# ============================================================================
# CONFIG - Compte VT Markets
# ============================================================================
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SYMBOLS = ["DJ30.", "NAS100.", "SP500."]
INITIAL_BALANCE = 10000.0
RISK_PERCENT = 10
MAX_DAILY_LOSS = -9999.0

USE_DAILY_PREFERRED_SYMBOL = False
ONE_SYMBOL_AT_A_TIME = False
PREFERRED_SYMBOL_BY_DAY = {}
USE_NEXT_BAR_OPEN_FOR_ENTRY = True
USE_DAILY_LOSS_IN_BACKTEST = False


@dataclass
class SymbolInfo:
    name: str
    trade_tick_value: float
    trade_tick_size: float
    trade_contract_size: float
    point: float
    volume_min: float
    volume_max: float
    volume_step: float


@dataclass
class SimulatedTrade:
    symbol: str
    type: TradeType
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    entry_time: datetime
    entry_bar_index: int
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_bar_index: Optional[int] = None
    exit_reason: str = "OPEN"
    profit: float = 0.0
    risk_reward_ratio: float = 0.0
    session: Optional[TradingSession] = None
    market_condition: Optional[MarketCondition] = None
    market_trend: Optional[MarketTrend] = None
    sma_slope: float = 0.0
    atr_value: float = 0.0
    day_of_week: int = 0


def load_csv(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.sort_index(inplace=True)
    return df


def load_symbols_info(data_dir: str) -> Dict[str, SymbolInfo]:
    path = os.path.join(data_dir, "symbols_info.json")
    if not os.path.exists(path):
        print(f"ATTENTION: {path} non trouve. Valeurs par defaut.")
        defaults = {
            "DJ30.":   SymbolInfo("DJ30.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
            "NAS100.": SymbolInfo("NAS100.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
            "SP500.":  SymbolInfo("SP500.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
        }
        return defaults
    with open(path) as f:
        raw = json.load(f)
    result = {}
    for sym, info in raw.items():
        result[sym] = SymbolInfo(
            name=info["name"],
            trade_tick_value=info["trade_tick_value"],
            trade_tick_size=info["trade_tick_size"],
            trade_contract_size=info["trade_contract_size"],
            point=info["point"],
            volume_min=info["volume_min"],
            volume_max=info["volume_max"],
            volume_step=info["volume_step"],
        )
    return result


def calculate_profit(si: SymbolInfo, entry_price: float, exit_price: float,
                     lot_size: float, trade_type: TradeType) -> float:
    if trade_type == TradeType.LONG:
        price_diff = exit_price - entry_price
    else:
        price_diff = entry_price - exit_price

    tick_value = si.trade_tick_value
    tick_size = si.trade_tick_size

    if tick_size > 0 and tick_value > 0:
        ticks = price_diff / tick_size
        return ticks * tick_value * lot_size
    else:
        contract_size = si.trade_contract_size
        if contract_size > 0:
            return (price_diff * contract_size * lot_size) / entry_price
        return price_diff * si.point * lot_size


def calculate_lot_size(si: SymbolInfo, balance: float, risk_percent: float,
                       entry_price: float, stop_loss: float) -> float:
    risk_amount = balance * (risk_percent / 100.0)
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return 0

    tick_value = si.trade_tick_value
    tick_size = si.trade_tick_size

    if tick_size > 0 and tick_value > 0:
        ticks_in_stop = stop_distance / tick_size
        risk_per_lot = ticks_in_stop * tick_value
    else:
        contract_size = si.trade_contract_size
        if contract_size > 0:
            risk_per_lot = (stop_distance * contract_size) / entry_price
        else:
            risk_per_lot = stop_distance * si.point

    if risk_per_lot <= 0:
        return 0

    lot_size = risk_amount / risk_per_lot
    lot_size = max(lot_size, si.volume_min)
    lot_size = min(lot_size, si.volume_max)

    if si.volume_step > 0:
        lot_size = (lot_size // si.volume_step) * si.volume_step

    return round(lot_size, 2)


def classify_trade(trade: SimulatedTrade, df: pd.DataFrame):
    trade.session = get_trading_session(trade.entry_time)
    trade.market_condition = get_market_condition(df)
    trade.market_trend = get_market_trend(df)
    if len(df) >= 10:
        sma_vals = df[f'SMA_{SMA_SLOW}'].iloc[-10:]
        if not sma_vals.isna().any() and sma_vals.iloc[0] > 0:
            trade.sma_slope = (sma_vals.iloc[-1] - sma_vals.iloc[0]) / sma_vals.iloc[0]
    if 'ATR' in df.columns and len(df) > 0:
        atr = df['ATR'].iloc[-1]
        trade.atr_value = atr if not pd.isna(atr) else 0.0
    trade.day_of_week = trade.entry_time.weekday()


def run_backtest(data_dir: str, months_back: int = 0):
    print("=" * 70)
    print("BACKTEST STANDALONE (CSV) - Strategie EMA 20 / SMA 50")
    print(f"   Timeframe M5 | LONG={'ON' if ALLOW_LONG else 'OFF'} SHORT={'ON' if ALLOW_SHORT else 'OFF'}")
    print(f"   Balance initiale: {INITIAL_BALANCE:.2f}")
    print(f"   Risque/trade: {RISK_PERCENT}%")
    if SESSION_RR:
        rr_parts = ", ".join(f"{s.value}=1:{r}" for s, r in SESSION_RR.items())
        print(f"   R:R par session: {rr_parts}")
    if BLOCKED_SESSIONS:
        print(f"   Sessions bloquees: {', '.join(s.value for s in BLOCKED_SESSIONS)}")
    print(f"   Cooldown: {COOLDOWN_AFTER_LOSS} barres apres perte")
    print(f"   Time Exit: {MAX_TRADE_DURATION_MINUTES} min" if MAX_TRADE_DURATION_MINUTES > 0 else "   Time Exit: OFF")
    print(f"   Data dir: {data_dir}")
    print("=" * 70)

    symbols_info = load_symbols_info(data_dir)

    historical_data: Dict[str, pd.DataFrame] = {}
    h1_data: Dict[str, pd.DataFrame] = {}

    for symbol in SYMBOLS:
        safe_name = symbol.replace('.', '_')
        m5_path = os.path.join(data_dir, f"{safe_name}_M5.csv")
        df_m5 = load_csv(m5_path)
        if df_m5 is None:
            print(f"  SKIP {symbol}: fichier {m5_path} non trouve")
            continue

        if months_back > 0:
            cutoff = df_m5.index[-1] - timedelta(days=months_back * 30)
            df_m5 = df_m5[df_m5.index >= cutoff]

        required = ['open', 'high', 'low', 'close']
        if not all(c in df_m5.columns for c in required):
            print(f"  SKIP {symbol}: colonnes manquantes")
            continue

        compute_indicators(df_m5)
        historical_data[symbol] = df_m5
        period = (df_m5.index[-1] - df_m5.index[0]).days
        print(f"  OK {symbol}: {len(df_m5)} barres M5 ({period} jours)")

        if USE_H1_TREND_FILTER:
            h1_path = os.path.join(data_dir, f"{safe_name}_H1.csv")
            df_h1 = load_csv(h1_path)
            if df_h1 is not None:
                if months_back > 0:
                    cutoff_h1 = df_h1.index[-1] - timedelta(days=months_back * 30)
                    df_h1 = df_h1[df_h1.index >= cutoff_h1]
                h1_data[symbol] = df_h1
                print(f"       {len(df_h1)} barres H1")

    if not historical_data:
        print("\nERREUR: Aucune donnee chargee. Lance export_mt5_data.py sur Windows d'abord.")
        sys.exit(1)

    events = []
    for sym, df in historical_data.items():
        for i in range(SMA_SLOW + 10, len(df)):
            ts = df.index[i]
            if hasattr(ts, 'to_pydatetime'):
                ts = ts.to_pydatetime()
            events.append((ts, sym, i))
    events.sort(key=lambda x: x[0])

    total_bars = len(events)
    print(f"\n  Timeline: {events[0][0]} -> {events[-1][0]} ({total_bars} barres)")
    print("\nLancement du backtest...")

    balance = INITIAL_BALANCE
    equity = INITIAL_BALANCE
    open_trades: Dict[str, List[SimulatedTrade]] = {}
    closed_trades: List[SimulatedTrade] = []
    last_bar_time: Dict[str, datetime] = {}
    last_loss_time: Optional[datetime] = None
    last_trade_by_symbol_type: Dict[Tuple[str, TradeType], datetime] = {}
    equity_curve = [INITIAL_BALANCE]
    daily_start_balance = None
    trading_stopped_daily = False
    last_trading_date = None
    symbol_stats: Dict[str, Dict] = {s: {'signals_detected': 0, 'trades_opened': 0, 'signals_blocked': 0} for s in historical_data}

    processed = 0
    for current_bar_time, symbol, bar_index in events:
        df = historical_data[symbol]
        market_data = df.iloc[:bar_index + 1]
        if len(market_data) < SMA_SLOW + 10:
            continue
        current_bar = df.iloc[bar_index]
        si = symbols_info.get(symbol)
        if si is None:
            continue

        # Check SL/TP des trades ouverts
        if symbol in open_trades and open_trades[symbol]:
            trades_to_close = []
            for ti, trade in enumerate(open_trades[symbol]):
                should_close = False

                if trade.type == TradeType.LONG:
                    if current_bar['low'] <= trade.stop_loss:
                        trade.exit_price = trade.stop_loss
                        trade.exit_reason = "SL"
                        should_close = True
                    elif current_bar['high'] >= trade.take_profit:
                        trade.exit_price = trade.take_profit
                        trade.exit_reason = "TP"
                        should_close = True
                elif trade.type == TradeType.SHORT:
                    if current_bar['high'] >= trade.stop_loss:
                        trade.exit_price = trade.stop_loss
                        trade.exit_reason = "SL"
                        should_close = True
                    elif current_bar['low'] <= trade.take_profit:
                        trade.exit_price = trade.take_profit
                        trade.exit_reason = "TP"
                        should_close = True

                if not should_close and MAX_TRADE_DURATION_MINUTES > 0:
                    elapsed = (df.index[bar_index] - trade.entry_time).total_seconds() / 60
                    if elapsed >= MAX_TRADE_DURATION_MINUTES:
                        trade.exit_price = current_bar['close']
                        trade.exit_reason = "TIME"
                        should_close = True

                if should_close:
                    trade.exit_time = df.index[bar_index]
                    trade.exit_bar_index = bar_index
                    profit = calculate_profit(si, trade.entry_price, trade.exit_price,
                                              trade.lot_size, trade.type)
                    trade.profit = profit
                    balance += profit
                    equity = balance
                    if profit < 0:
                        last_loss_time = df.index[bar_index]
                    closed_trades.append(trade)
                    trades_to_close.append(ti)

            for ti in reversed(trades_to_close):
                open_trades[symbol].pop(ti)
            if not open_trades[symbol]:
                del open_trades[symbol]

        # Daily loss
        current_date = current_bar_time.date() if hasattr(current_bar_time, 'date') else current_bar_time
        if USE_DAILY_LOSS_IN_BACKTEST:
            if last_trading_date is None or current_date > last_trading_date:
                daily_start_balance = balance
                trading_stopped_daily = False
                last_trading_date = current_date
            if trading_stopped_daily:
                continue
            if daily_start_balance is not None and (balance - daily_start_balance) <= MAX_DAILY_LOSS:
                trading_stopped_daily = True
                continue

        if symbol in last_bar_time and current_bar_time <= last_bar_time[symbol]:
            continue
        last_bar_time[symbol] = current_bar_time

        if USE_DAILY_PREFERRED_SYMBOL and PREFERRED_SYMBOL_BY_DAY:
            weekday = current_bar_time.weekday() if hasattr(current_bar_time, 'weekday') else 0
            preferred = PREFERRED_SYMBOL_BY_DAY.get(weekday)
            if preferred is not None and symbol != preferred:
                continue

        def has_other_positions():
            if not ONE_SYMBOL_AT_A_TIME:
                return False
            for s, tl in open_trades.items():
                if s != symbol and tl:
                    return True
            return False

        if COOLDOWN_AFTER_LOSS > 0 and last_loss_time is not None:
            elapsed_bars = (current_bar_time - last_loss_time).total_seconds() / 300
            if elapsed_bars < COOLDOWN_AFTER_LOSS:
                continue

        df_h1_filtered = None
        if USE_H1_TREND_FILTER and symbol in h1_data:
            df_h1_filtered = get_h1_data_at_time(h1_data[symbol], current_bar_time)

        # LONG
        if ALLOW_LONG:
            long_signal = check_long_signal(market_data, df_h1_filtered, symbol)
            if long_signal:
                symbol_stats[symbol]['signals_detected'] += 1

                if USE_NEXT_BAR_OPEN_FOR_ENTRY and bar_index + 1 < len(df):
                    entry_price = float(df.iloc[bar_index + 1]['open'])
                else:
                    entry_price = current_bar['close']

                stop_loss = calculate_sl_long(market_data, 10)
                stop_distance = entry_price - stop_loss
                sl_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0
                rr = get_risk_reward_ratio(market_data)
                take_profit = entry_price + (stop_distance * rr)
                lot_size = calculate_lot_size(si, balance, RISK_PERCENT, entry_price, stop_loss)

                blocked = (
                    lot_size <= 0 or
                    stop_loss <= 0 or stop_loss >= entry_price or
                    sl_pct > 0.05 or
                    has_other_positions()
                )
                if blocked:
                    symbol_stats[symbol]['signals_blocked'] += 1
                else:
                    symbol_stats[symbol]['trades_opened'] += 1
                    trade = SimulatedTrade(
                        symbol=symbol, type=TradeType.LONG,
                        entry_price=entry_price, stop_loss=stop_loss,
                        take_profit=take_profit, lot_size=lot_size,
                        entry_time=current_bar_time, entry_bar_index=bar_index,
                        risk_reward_ratio=rr
                    )
                    classify_trade(trade, market_data)
                    open_trades.setdefault(symbol, []).append(trade)
                    last_trade_by_symbol_type[(symbol, TradeType.LONG)] = current_bar_time

        # SHORT
        if ALLOW_SHORT:
            short_signal = check_short_signal(market_data, df_h1_filtered, symbol)
            if short_signal:
                symbol_stats[symbol]['signals_detected'] += 1

                if USE_NEXT_BAR_OPEN_FOR_ENTRY and bar_index + 1 < len(df):
                    entry_price = float(df.iloc[bar_index + 1]['open'])
                else:
                    entry_price = current_bar['close']

                stop_loss = calculate_sl_short(market_data, 10)
                stop_distance = stop_loss - entry_price
                sl_pct = abs(stop_loss - entry_price) / entry_price if entry_price > 0 else 0
                rr = get_risk_reward_ratio(market_data)
                take_profit = entry_price - (stop_distance * rr)
                lot_size = calculate_lot_size(si, balance, RISK_PERCENT, entry_price, stop_loss)

                blocked = (
                    lot_size <= 0 or
                    stop_loss <= 0 or stop_loss <= entry_price or
                    sl_pct > 0.05 or
                    has_other_positions()
                )
                if blocked:
                    symbol_stats[symbol]['signals_blocked'] += 1
                else:
                    symbol_stats[symbol]['trades_opened'] += 1
                    trade = SimulatedTrade(
                        symbol=symbol, type=TradeType.SHORT,
                        entry_price=entry_price, stop_loss=stop_loss,
                        take_profit=take_profit, lot_size=lot_size,
                        entry_time=current_bar_time, entry_bar_index=bar_index,
                        risk_reward_ratio=rr
                    )
                    classify_trade(trade, market_data)
                    open_trades.setdefault(symbol, []).append(trade)
                    last_trade_by_symbol_type[(symbol, TradeType.SHORT)] = current_bar_time

        equity_curve.append(equity)
        processed += 1
        if processed % 10000 == 0:
            print(f"   {processed}/{total_bars} barres...")

    # Fermer trades ouverts
    for symbol, trades_list in list(open_trades.items()):
        df = historical_data[symbol]
        si = symbols_info.get(symbol)
        if si is None:
            continue
        last_bar = df.iloc[-1]
        for trade in trades_list:
            trade.exit_price = last_bar['close']
            trade.exit_time = df.index[-1]
            trade.exit_bar_index = len(df) - 1
            trade.exit_reason = "END"
            profit = calculate_profit(si, trade.entry_price, trade.exit_price,
                                      trade.lot_size, trade.type)
            trade.profit = profit
            balance += profit
            closed_trades.append(trade)

    # ====================================================================
    # RESULTATS
    # ====================================================================
    print("\n" + "=" * 70)
    print("RESULTATS DU BACKTEST")
    print("=" * 70)

    net_profit = balance - INITIAL_BALANCE
    return_pct = (net_profit / INITIAL_BALANCE) * 100

    print(f"\nBalance:")
    print(f"   Initiale: {INITIAL_BALANCE:.2f}")
    print(f"   Finale:   {balance:.2f}")
    print(f"   Profit:   {net_profit:+.2f} ({return_pct:+.2f}%)")

    total = len(closed_trades)
    wins = [t for t in closed_trades if t.profit > 0]
    losses = [t for t in closed_trades if t.profit < 0]
    win_rate = (len(wins) / total * 100) if total > 0 else 0

    print(f"\nTrades:")
    print(f"   Total:    {total}")
    print(f"   Gagnants: {len(wins)} ({win_rate:.1f}%)")
    print(f"   Perdants: {len(losses)} ({100 - win_rate:.1f}%)")

    if wins:
        total_profit = sum(t.profit for t in wins)
        total_loss = abs(sum(t.profit for t in losses)) if losses else 0
        pf = total_profit / total_loss if total_loss > 0 else float('inf')
        print(f"\nPerformance:")
        print(f"   Profit total:    {total_profit:.2f}")
        print(f"   Perte totale:    {total_loss:.2f}")
        print(f"   Profit Factor:   {pf:.2f}")
        print(f"   Gain moyen:      {total_profit / len(wins):.2f}")
        if losses:
            print(f"   Perte moyenne:   {total_loss / len(losses):.2f}")
        print(f"   Plus gros gain:  {max(t.profit for t in wins):.2f}")
        if losses:
            print(f"   Plus grosse perte: {min(t.profit for t in losses):.2f}")

    if equity_curve:
        peak = equity_curve[0]
        max_dd = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        dd_pct = (max_dd / peak * 100) if peak > 0 else 0
        print(f"\nRisque:")
        print(f"   Drawdown max: {max_dd:.2f} ({dd_pct:.2f}%)")

    print(f"\nPar session:")
    session_trades = defaultdict(list)
    for t in closed_trades:
        if t.session:
            session_trades[t.session.value].append(t)
    for session_name in ["ASIA", "EUROPE", "US"]:
        trades = session_trades.get(session_name, [])
        if trades:
            s_wins = len([t for t in trades if t.profit > 0])
            s_pnl = sum(t.profit for t in trades)
            s_wr = s_wins / len(trades) * 100
            print(f"   {session_name:8s}: {len(trades):3d} trades | WR {s_wr:5.1f}% | PnL {s_pnl:+8.2f}")

    print(f"\nPar symbole:")
    for sym in SYMBOLS:
        trades = [t for t in closed_trades if t.symbol == sym]
        if trades:
            s_wins = len([t for t in trades if t.profit > 0])
            s_pnl = sum(t.profit for t in trades)
            s_wr = s_wins / len(trades) * 100
            stats = symbol_stats.get(sym, {})
            print(f"   {sym:14s}: {len(trades):3d} trades | WR {s_wr:5.1f}% | PnL {s_pnl:+8.2f} | signaux={stats.get('signals_detected', 0)} bloques={stats.get('signals_blocked', 0)}")

    print(f"\nPar jour de la semaine:")
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    for d in range(5):
        trades = [t for t in closed_trades if t.day_of_week == d]
        if trades:
            d_wins = len([t for t in trades if t.profit > 0])
            d_pnl = sum(t.profit for t in trades)
            d_wr = d_wins / len(trades) * 100
            print(f"   {days[d]:10s}: {len(trades):3d} trades | WR {d_wr:5.1f}% | PnL {d_pnl:+8.2f}")

    print(f"\nPar direction:")
    for tt in [TradeType.LONG, TradeType.SHORT]:
        trades = [t for t in closed_trades if t.type == tt]
        if trades:
            t_wins = len([t for t in trades if t.profit > 0])
            t_pnl = sum(t.profit for t in trades)
            t_wr = t_wins / len(trades) * 100
            print(f"   {tt.value:6s}: {len(trades):3d} trades | WR {t_wr:5.1f}% | PnL {t_pnl:+8.2f}")

    print(f"\nPar raison de sortie:")
    for reason in ["TP", "SL", "TIME", "END"]:
        trades = [t for t in closed_trades if t.exit_reason == reason]
        if trades:
            r_pnl = sum(t.profit for t in trades)
            print(f"   {reason:5s}: {len(trades):3d} trades | PnL {r_pnl:+8.2f}")

    print("\n" + "=" * 70)

    if closed_trades:
        trades_data = []
        for t in closed_trades:
            trades_data.append({
                'symbol': t.symbol,
                'type': t.type.value,
                'entry_time': t.entry_time,
                'exit_time': t.exit_time,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'stop_loss': t.stop_loss,
                'take_profit': t.take_profit,
                'lot_size': t.lot_size,
                'profit': t.profit,
                'exit_reason': t.exit_reason,
                'rr_ratio': t.risk_reward_ratio,
                'session': t.session.value if t.session else '',
                'day_of_week': t.day_of_week,
            })
        df_trades = pd.DataFrame(trades_data)
        out_path = os.path.join(data_dir, "backtest_results.csv")
        df_trades.to_csv(out_path, index=False)
        print(f"\nTrades exportes: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Backtest standalone (CSV)")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Dossier contenant les CSV")
    parser.add_argument("--months", type=int, default=0, help="Limiter aux N derniers mois (0=tout)")
    args = parser.parse_args()

    run_backtest(args.data_dir, months_back=args.months)


if __name__ == "__main__":
    main()
