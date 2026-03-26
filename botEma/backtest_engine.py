#!/usr/bin/env python3
"""
Moteur de backtest parametrise - Aucune dependance MT5.
Charge les donnees une fois, execute N variantes rapidement.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategy_core import (
    EMA_FAST, SMA_SLOW, TradeType, TradingSession,
    compute_indicators, get_h1_data_at_time,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ============================================================================
# DATACLASSES
# ============================================================================
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
class StrategyParams:
    name: str = "default"
    risk_percent: float = 10.0
    symbols: List[str] = field(default_factory=lambda: ["DJ30.", "NAS100.", "SP500."])
    initial_balance: float = 10000.0
    # Sessions
    blocked_sessions: List[str] = field(default_factory=lambda: ["US"])
    session_rr: Dict[str, float] = field(default_factory=lambda: {"EUROPE": 2.5, "ASIA": 2.0})
    rr_default: float = 2.0
    # Filters
    one_symbol_at_a_time: bool = False
    use_h1_trend_filter: bool = True
    h1_bars_required: int = 2
    use_preferred_symbol: bool = False
    preferred_symbol_by_day: Dict[int, str] = field(default_factory=dict)
    # Timing
    cooldown_after_loss: int = 2
    max_trade_duration_minutes: int = 210
    # SL
    atr_sl_multiplier: float = 1.5
    # Filtres supplementaires
    blocked_days: List[int] = field(default_factory=list)
    allowed_hours: Optional[List[int]] = None  # None = toutes, sinon liste d'heures
    # Entry
    use_next_bar_open: bool = True
    max_sl_pct: float = 0.05
    # Spread + slippage (en points d'indice, deduit de chaque trade)
    spread_points: float = 0.0  # global fallback (0 = utiliser per-symbol)
    spread_per_symbol: Dict[str, float] = field(default_factory=lambda: {
        "DJ30.": 4.0,     # ~4 pts spread+slippage DJ30
        "NAS100.": 2.0,   # ~2 pts spread+slippage NAS100
        "SP500.": 0.7,    # ~0.7 pts spread+slippage SP500
    })


@dataclass
class TradeResult:
    symbol: str
    trade_type: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    profit: float
    exit_reason: str
    # Detailed
    session: str
    day_of_week: int
    hour: int
    month: str
    duration_minutes: float
    r_multiple: float
    rr_ratio_used: float
    risk_amount: float
    balance_after: float
    # Context
    atr_at_entry: float
    sl_distance_pts: float
    sl_distance_pct: float


@dataclass
class BacktestResult:
    params: StrategyParams
    trades: List[TradeResult]
    equity_curve: List[float]
    final_balance: float
    # Aggregates (computed after)
    total_trades: int = 0
    win_rate: float = 0.0
    net_profit: float = 0.0
    return_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_r_multiple: float = 0.0
    max_consecutive_losses: int = 0


# ============================================================================
# DATA LOADING
# ============================================================================
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


def load_symbols_info(data_dir: str = DATA_DIR) -> Dict[str, SymbolInfo]:
    path = os.path.join(data_dir, "symbols_info.json")
    if not os.path.exists(path):
        return {
            "DJ30.":   SymbolInfo("DJ30.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
            "NAS100.": SymbolInfo("NAS100.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
            "SP500.":  SymbolInfo("SP500.", 0.01, 0.01, 1.0, 0.01, 0.1, 125.0, 0.1),
        }
    with open(path) as f:
        raw = json.load(f)
    return {sym: SymbolInfo(**info) for sym, info in raw.items()}


def load_all_data(symbols: List[str], data_dir: str = DATA_DIR,
                  months_back: int = 0) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """Charge M5 + H1, calcule les indicateurs. A appeler UNE SEULE FOIS."""
    m5_data = {}
    h1_data = {}
    for symbol in symbols:
        safe = symbol.replace('.', '_')
        df_m5 = load_csv(os.path.join(data_dir, f"{safe}_M5.csv"))
        if df_m5 is None:
            continue
        if months_back > 0:
            cutoff = df_m5.index[-1] - timedelta(days=months_back * 30)
            df_m5 = df_m5[df_m5.index >= cutoff]
        compute_indicators(df_m5)
        m5_data[symbol] = df_m5

        df_h1 = load_csv(os.path.join(data_dir, f"{safe}_H1.csv"))
        if df_h1 is not None:
            if months_back > 0:
                cutoff_h1 = df_h1.index[-1] - timedelta(days=months_back * 30)
                df_h1 = df_h1[df_h1.index >= cutoff_h1]
            h1_data[symbol] = df_h1
    return m5_data, h1_data


# ============================================================================
# PURE FUNCTIONS (no globals)
# ============================================================================
def get_session(dt: datetime) -> str:
    h = dt.hour
    if 0 <= h < 8:
        return "ASIA"
    elif 8 <= h < 14:
        return "EUROPE"
    elif 14 <= h < 21:
        return "US"
    return "OFF"


def check_crossover_at(df: pd.DataFrame, bar_idx: int, trade_type: TradeType) -> bool:
    """Detecte le croisement EMA20/SMA50 a l'index donne (sans copier le DF)."""
    if bar_idx < 1:
        return False
    ema_col = f'EMA_{EMA_FAST}'
    sma_col = f'SMA_{SMA_SLOW}'
    ema_cur = df[ema_col].iat[bar_idx]
    sma_cur = df[sma_col].iat[bar_idx]
    ema_prev = df[ema_col].iat[bar_idx - 1]
    sma_prev = df[sma_col].iat[bar_idx - 1]

    if trade_type == TradeType.LONG:
        return ema_prev < sma_prev and ema_cur > sma_cur
    else:
        return ema_prev > sma_prev and ema_cur < sma_cur


def check_h1_trend(df_h1: Optional[pd.DataFrame], trade_type: TradeType,
                   h1_bars: int = 2) -> bool:
    if df_h1 is None or len(df_h1) < h1_bars:
        return False
    prices = df_h1.iloc[-h1_bars:]['close'].values
    if h1_bars == 2:
        if trade_type == TradeType.LONG:
            return prices[1] > prices[0]
        else:
            return prices[1] < prices[0]
    else:
        if trade_type == TradeType.LONG:
            if prices[-1] < prices[0]:
                return False
            return sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1]) >= 2
        else:
            if prices[-1] > prices[0]:
                return False
            return sum(1 for i in range(1, len(prices)) if prices[i] < prices[i-1]) >= 2


def calc_sl_at(df: pd.DataFrame, bar_idx: int, trade_type: TradeType, atr_sl_mult: float) -> float:
    """Calcul SL a l'index donne (sans copier le DF)."""
    price = df['close'].iat[bar_idx]
    if 'ATR' in df.columns:
        atr = df['ATR'].iat[bar_idx]
        if not pd.isna(atr) and atr > 0:
            if trade_type == TradeType.LONG:
                return price - (atr * atr_sl_mult)
            else:
                return price + (atr * atr_sl_mult)
    start = max(0, bar_idx - 9)
    if trade_type == TradeType.LONG:
        return df['low'].iloc[start:bar_idx + 1].min() * 0.999
    else:
        return df['high'].iloc[start:bar_idx + 1].max() * 1.001


def calc_profit(si: SymbolInfo, entry: float, exit_p: float,
                lots: float, ttype: TradeType) -> float:
    diff = (exit_p - entry) if ttype == TradeType.LONG else (entry - exit_p)
    if si.trade_tick_size > 0 and si.trade_tick_value > 0:
        return (diff / si.trade_tick_size) * si.trade_tick_value * lots
    if si.trade_contract_size > 0:
        return (diff * si.trade_contract_size * lots) / entry
    return diff * si.point * lots


def calc_lot_size(si: SymbolInfo, balance: float, risk_pct: float,
                  entry: float, sl: float) -> float:
    risk_amount = balance * (risk_pct / 100.0)
    stop_dist = abs(entry - sl)
    if stop_dist <= 0:
        return 0
    if si.trade_tick_size > 0 and si.trade_tick_value > 0:
        risk_per_lot = (stop_dist / si.trade_tick_size) * si.trade_tick_value
    elif si.trade_contract_size > 0:
        risk_per_lot = (stop_dist * si.trade_contract_size) / entry
    else:
        risk_per_lot = stop_dist * si.point
    if risk_per_lot <= 0:
        return 0
    lots = risk_amount / risk_per_lot
    lots = max(lots, si.volume_min)
    lots = min(lots, si.volume_max)
    if si.volume_step > 0:
        lots = (lots // si.volume_step) * si.volume_step
    return round(lots, 2)


# ============================================================================
# BACKTEST ENGINE
# ============================================================================
def run_backtest(params: StrategyParams,
                 m5_data: Dict[str, pd.DataFrame],
                 h1_data: Dict[str, pd.DataFrame],
                 symbols_info: Dict[str, SymbolInfo],
                 silent: bool = True) -> BacktestResult:
    """Execute un backtest complet avec les parametres donnes."""

    balance = params.initial_balance
    open_trades: Dict[str, List[dict]] = {}
    closed: List[TradeResult] = []
    equity_curve = [balance]
    last_bar_time: Dict[str, datetime] = {}
    last_loss_time: Optional[datetime] = None

    # Build event timeline
    events = []
    for sym in params.symbols:
        if sym not in m5_data:
            continue
        d = m5_data[sym]
        for i in range(200, len(d)):
            ts = d.index[i]
            if hasattr(ts, 'to_pydatetime'):
                ts = ts.to_pydatetime()
            events.append((ts, sym, i))
    events.sort(key=lambda x: x[0])

    if not events:
        return BacktestResult(params=params, trades=[], equity_curve=[balance],
                              final_balance=balance)

    for current_time, symbol, bar_idx in events:
        df = m5_data[symbol]
        bar = df.iloc[bar_idx]
        si = symbols_info.get(symbol)
        if si is None:
            continue

        # --- Manage open trades: SL/TP/TIME ---
        if symbol in open_trades and open_trades[symbol]:
            to_close = []
            for ti, t in enumerate(open_trades[symbol]):
                hit = None
                if t['type'] == TradeType.LONG:
                    if bar['low'] <= t['sl']:
                        hit = ('SL', t['sl'])
                    elif bar['high'] >= t['tp']:
                        hit = ('TP', t['tp'])
                elif t['type'] == TradeType.SHORT:
                    if bar['high'] >= t['sl']:
                        hit = ('SL', t['sl'])
                    elif bar['low'] <= t['tp']:
                        hit = ('TP', t['tp'])

                if hit is None and params.max_trade_duration_minutes > 0:
                    elapsed = (current_time - t['entry_time']).total_seconds() / 60
                    if elapsed >= params.max_trade_duration_minutes:
                        hit = ('TIME', float(bar['close']))

                if hit:
                    reason, exit_price = hit
                    profit = calc_profit(si, t['entry'], exit_price, t['lots'], t['type'])
                    # Deduct spread + slippage cost
                    sp = params.spread_per_symbol.get(symbol, params.spread_points)
                    if sp > 0 and si.trade_tick_size > 0:
                        spread_cost = (sp / si.trade_tick_size) * si.trade_tick_value * t['lots']
                        profit -= spread_cost
                    balance += profit
                    if profit < 0:
                        last_loss_time = current_time

                    entry_session = get_session(t['entry_time'])
                    duration = (current_time - t['entry_time']).total_seconds() / 60
                    risk_amt = t['risk_amount']
                    r_mult = profit / risk_amt if risk_amt > 0 else 0

                    closed.append(TradeResult(
                        symbol=symbol, trade_type=t['type'].value,
                        entry_time=t['entry_time'], exit_time=current_time,
                        entry_price=t['entry'], exit_price=exit_price,
                        stop_loss=t['sl'], take_profit=t['tp'],
                        lot_size=t['lots'], profit=profit,
                        exit_reason=reason,
                        session=entry_session, day_of_week=t['entry_time'].weekday(),
                        hour=t['entry_time'].hour,
                        month=t['entry_time'].strftime('%Y-%m'),
                        duration_minutes=duration, r_multiple=r_mult,
                        rr_ratio_used=t['rr'], risk_amount=risk_amt,
                        balance_after=balance,
                        atr_at_entry=t['atr'],
                        sl_distance_pts=t['sl_dist'],
                        sl_distance_pct=t['sl_pct'],
                    ))
                    to_close.append(ti)

            for ti in reversed(to_close):
                open_trades[symbol].pop(ti)
            if not open_trades[symbol]:
                del open_trades[symbol]

        # --- Filters ---
        if symbol in last_bar_time and current_time <= last_bar_time[symbol]:
            continue
        last_bar_time[symbol] = current_time

        session = get_session(current_time)
        if session == "OFF" or session in params.blocked_sessions:
            equity_curve.append(balance)
            continue

        weekday = current_time.weekday()
        if weekday in params.blocked_days:
            equity_curve.append(balance)
            continue

        if params.allowed_hours is not None and current_time.hour not in params.allowed_hours:
            equity_curve.append(balance)
            continue

        if params.use_preferred_symbol and params.preferred_symbol_by_day:
            pref = params.preferred_symbol_by_day.get(weekday)
            if pref is not None and symbol != pref:
                equity_curve.append(balance)
                continue

        if params.cooldown_after_loss > 0 and last_loss_time is not None:
            if (current_time - last_loss_time).total_seconds() / 300 < params.cooldown_after_loss:
                equity_curve.append(balance)
                continue

        def has_other():
            if not params.one_symbol_at_a_time:
                return False
            for s, tl in open_trades.items():
                if s != symbol and tl:
                    return True
            return False

        # H1 data
        df_h1_f = None
        if params.use_h1_trend_filter and symbol in h1_data:
            df_h1_f = get_h1_data_at_time(h1_data[symbol], current_time)

        if bar_idx < 200:
            equity_curve.append(balance)
            continue

        # RR for this session
        rr = params.session_rr.get(session, params.rr_default)

        # --- Signal detection (optimise: pas de slice du DataFrame) ---
        for ttype in [TradeType.LONG, TradeType.SHORT]:
            if not check_crossover_at(df, bar_idx, ttype):
                continue
            if params.use_h1_trend_filter and not check_h1_trend(df_h1_f, ttype, params.h1_bars_required):
                continue

            # Entry price
            if params.use_next_bar_open and bar_idx + 1 < len(df):
                entry = float(df['open'].iat[bar_idx + 1])
            else:
                entry = float(bar['close'])

            sl = calc_sl_at(df, bar_idx, ttype, params.atr_sl_multiplier)

            if ttype == TradeType.LONG:
                sl_dist = entry - sl
                if sl <= 0 or sl >= entry:
                    continue
                tp = entry + sl_dist * rr
            else:
                sl_dist = sl - entry
                if sl <= 0 or sl <= entry:
                    continue
                tp = entry - sl_dist * rr

            sl_pct = abs(sl_dist) / entry if entry > 0 else 0
            if sl_pct > params.max_sl_pct:
                continue

            if has_other():
                continue

            lots = calc_lot_size(si, balance, params.risk_percent, entry, sl)
            if lots <= 0:
                continue

            risk_amount = balance * (params.risk_percent / 100.0)
            atr_val = 0.0
            if 'ATR' in df.columns:
                a = df['ATR'].iat[bar_idx]
                if not pd.isna(a):
                    atr_val = a

            open_trades.setdefault(symbol, []).append({
                'type': ttype, 'entry': entry, 'sl': sl, 'tp': tp,
                'lots': lots, 'entry_time': current_time, 'rr': rr,
                'risk_amount': risk_amount, 'atr': atr_val,
                'sl_dist': abs(sl_dist), 'sl_pct': sl_pct,
            })

        equity_curve.append(balance)

    # Close remaining open trades
    for symbol, trades_list in list(open_trades.items()):
        df = m5_data[symbol]
        si = symbols_info.get(symbol)
        if si is None:
            continue
        last_bar = df.iloc[-1]
        for t in trades_list:
            exit_p = float(last_bar['close'])
            profit = calc_profit(si, t['entry'], exit_p, t['lots'], t['type'])
            # Deduct spread + slippage cost
            sp = params.spread_per_symbol.get(symbol, params.spread_points)
            if sp > 0 and si.trade_tick_size > 0:
                spread_cost = (sp / si.trade_tick_size) * si.trade_tick_value * t['lots']
                profit -= spread_cost
            balance += profit
            duration = (df.index[-1] - t['entry_time']).total_seconds() / 60
            risk_amt = t['risk_amount']
            r_mult = profit / risk_amt if risk_amt > 0 else 0
            closed.append(TradeResult(
                symbol=symbol, trade_type=t['type'].value,
                entry_time=t['entry_time'], exit_time=df.index[-1],
                entry_price=t['entry'], exit_price=exit_p,
                stop_loss=t['sl'], take_profit=t['tp'],
                lot_size=t['lots'], profit=profit,
                exit_reason="END",
                session=get_session(t['entry_time']),
                day_of_week=t['entry_time'].weekday(),
                hour=t['entry_time'].hour,
                month=t['entry_time'].strftime('%Y-%m'),
                duration_minutes=duration, r_multiple=r_mult,
                rr_ratio_used=t['rr'], risk_amount=risk_amt,
                balance_after=balance,
                atr_at_entry=t['atr'],
                sl_distance_pts=t['sl_dist'],
                sl_distance_pct=t['sl_pct'],
            ))

    result = BacktestResult(params=params, trades=closed,
                            equity_curve=equity_curve, final_balance=balance)
    _compute_aggregates(result)
    return result


def _compute_aggregates(r: BacktestResult):
    trades = r.trades
    r.total_trades = len(trades)
    if not trades:
        return
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit <= 0]
    r.win_rate = len(wins) / len(trades) * 100
    r.net_profit = r.final_balance - r.params.initial_balance
    r.return_pct = (r.net_profit / r.params.initial_balance) * 100
    total_win = sum(t.profit for t in wins)
    total_loss = abs(sum(t.profit for t in losses))
    r.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
    r.avg_win = total_win / len(wins) if wins else 0
    r.avg_loss = total_loss / len(losses) if losses else 0
    r.best_trade = max(t.profit for t in trades)
    r.worst_trade = min(t.profit for t in trades)
    r.avg_r_multiple = sum(t.r_multiple for t in trades) / len(trades)

    # Max drawdown
    peak = r.equity_curve[0]
    max_dd = 0
    for eq in r.equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    r.max_drawdown = max_dd
    r.max_drawdown_pct = (max_dd / peak * 100) if peak > 0 else 0

    # Max consecutive losses
    streak = 0
    max_streak = 0
    for t in trades:
        if t.profit <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    r.max_consecutive_losses = max_streak
