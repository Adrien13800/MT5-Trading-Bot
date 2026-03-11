"""
Bridge OFI → MT5 (VT Markets).
Écoute Redis (signaux BUY/SELL/CLOSE), exécute sur MT5, gère Break-Even et log des trades.
CLOSE = fermeture des positions BUY uniquement (Sniper Confirmé). Asyncio + ThreadPool.
"""

import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config (depuis config_ofi.json)
# ---------------------------------------------------------------------------
CONFIG_PATHS = [
    Path(__file__).parent / "config_ofi.json",
    Path("config_ofi.json"),
    Path("botOFI/config_ofi.json"),
]


def load_config():
    for p in CONFIG_PATHS:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("config_ofi.json introuvable")


# ---------------------------------------------------------------------------
# Logging pro → trade_history.log
# ---------------------------------------------------------------------------
def setup_logging():
    log_dir = Path(__file__).parent
    log_file = log_dir / "trade_history.log"
    logger = logging.getLogger("ofi_bridge")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        )
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(sh)
    return logger


LOG = setup_logging()

# Fichier analytics (JSONL) pour analyse technique des paramètres
ANALYTICS_LOG_PATH = Path(__file__).parent / "system_analytics.log"


def _analytics_log(record: dict) -> None:
    """Append one JSONL line to system_analytics.log (format structuré pour Excel/Python)."""
    try:
        with open(ANALYTICS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        LOG.warning("system_analytics.log write failed: %s", e)


# État pour le cooldown après ouverture (même sens)
_state = {"last_open_side": None, "last_open_time": 0.0}

# ---------------------------------------------------------------------------
# MT5 helpers (bloquants → à appeler dans executor)
# ---------------------------------------------------------------------------
def _get_tick(symbol: str, max_retries: int = 5):
    """Retourne le tick ou None après retries."""
    for _ in range(max_retries):
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None:
            return tick
        LOG.warning("symbol_info_tick(%s) = None, retry...", symbol)
        time.sleep(0.1)
    return None


def _get_symbol_filling(symbol: str):
    """
    Retourne le type_filling accepté par le broker pour ce symbole.
    Beaucoup de brokers (ex: VT Markets) n'acceptent qu'un seul mode (FOK ou IOC).
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        LOG.warning("symbol_info(%s) = None, fallback ORDER_FILLING_IOC", symbol)
        return mt5.ORDER_FILLING_IOC
    # filling_mode: 1=FOK only, 2=IOC only, 3=both
    mode = getattr(info, "filling_mode", 0)
    if mode == 1:
        return mt5.ORDER_FILLING_FOK
    if mode == 2:
        return mt5.ORDER_FILLING_IOC
    if mode == 3:
        return mt5.ORDER_FILLING_IOC  # préférer IOC pour exécution immédiate
    return mt5.ORDER_FILLING_IOC


def _timeframe_from_string(tf: str):
    """Retourne la constante MT5 pour le timeframe (ex: D1, H1)."""
    m = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
         "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
         "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1}
    return m.get(tf.upper(), mt5.TIMEFRAME_D1)


def _compute_atr(high, low, close, period: int = 14):
    """ATR(period) à partir des séries high, low, close (numpy ou list)."""
    n = len(close)
    if n < period + 1:
        return None
    tr_list = []
    for i in range(1, n):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_list.append(tr)
    # Premier ATR = moyenne des premiers `period` TR
    atr = sum(tr_list[:period]) / period
    atr_values = [atr]
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        atr_values.append(atr)
    return atr_values


def _check_trend_filter(symbol: str, signal: str, price: float, filters: dict) -> bool:
    """
    BUY autorisé seulement si prix > SMA(tendance).
    SELL autorisé seulement si prix < SMA(tendance).
    Si trend_ma_period non configuré, retourne True (pas de filtre).
    """
    period = filters.get("trend_ma_period")
    if period is None or period <= 0:
        return True
    tf_str = filters.get("trend_ma_timeframe", "M5")
    tf = _timeframe_from_string(tf_str)
    count = period + 5
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) < period:
            LOG.warning("Trend filter: pas assez de barres")
            return True
        closes = rates["close"]
        sma = sum(closes[-period:]) / period
        if signal == "BUY":
            if price <= sma:
                LOG.info(
                    "TREND SKIP | BUY | price=%.2f <= SMA%d=%.2f (pas de momentum haussier)",
                    price, period, sma,
                )
                return False
        else:
            if price >= sma:
                LOG.info(
                    "TREND SKIP | SELL | price=%.2f >= SMA%d=%.2f (pas de momentum baissier)",
                    price, period, sma,
                )
                return False
        return True
    except Exception as e:
        LOG.warning("Trend filter error: %s — autoriser entrée", e)
        return True


def _check_atr_filter(symbol: str, filters: dict) -> bool:
    """
    True si la volatilité est suffisante (ATR actuel >= ratio * moyenne ATR sur lookback).
    False = ne pas ouvrir (marché en range).
    """
    period = filters.get("atr_period", 14)
    tf_str = filters.get("atr_timeframe", "D1")
    lookback = filters.get("atr_lookback", 20)
    min_ratio = filters.get("atr_min_ratio", 0.7)
    tf = _timeframe_from_string(tf_str)
    count = period + lookback + 5
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) < count:
            LOG.warning("ATR filter: pas assez de barres (copy_rates_from_pos)")
            return True
        high = rates["high"]
        low = rates["low"]
        close = rates["close"]
        atr_values = _compute_atr(high, low, close, period)
        if not atr_values or len(atr_values) < lookback:
            return True
        current_atr = atr_values[-1]
        avg_atr = sum(atr_values[-lookback:]) / lookback
        if avg_atr <= 0:
            return True
        if current_atr < min_ratio * avg_atr:
            LOG.info(
                "ATR filter SKIP | ATR=%.2f < %.2f * avg(%.2f) (range)",
                current_atr, min_ratio, avg_atr,
            )
            return False
        return True
    except Exception as e:
        LOG.warning("ATR filter error: %s — autoriser entrée", e)
        return True


def sync_close_all(cfg: dict):
    """Ferme toutes les positions (BUY et SELL) du symbole/magic (signal CLOSE du moteur Rust)."""
    global _state
    mt5_cfg = cfg["mt5"]
    symbol = mt5_cfg["symbol"]
    magic = mt5_cfg["magic"]
    _analytics_log({
        "ts_ns": int(time.time() * 1e9),
        "source": "python",
        "type": "signal_received",
        "side": "CLOSE",
    })
    close_positions_by_type(symbol, mt5.POSITION_TYPE_BUY, magic)
    close_positions_by_type(symbol, mt5.POSITION_TYPE_SELL, magic)
    _state["last_open_side"] = None
    _state["last_open_time"] = 0.0
    LOG.info("CLOSE | Toutes positions (long + short) fermées (signal SNIPER) — prêt pour ré-entrée")


def close_positions_by_type(symbol: str, pos_type: int, magic: int):
    """Ferme toutes les positions du symbole du type donné (Auto-Flip)."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return
    for pos in positions:
        if pos.type != pos_type or pos.magic != magic:
            continue
        tick = _get_tick(symbol)
        if tick is None:
            LOG.error("Impossible d'obtenir le tick pour fermer position %s", pos.ticket)
            continue
        type_close = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price_close = tick.bid if type_close == mt5.ORDER_TYPE_SELL else tick.ask
        filling = _get_symbol_filling(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": type_close,
            "position": pos.ticket,
            "price": price_close,
            "magic": magic,
            "type_filling": filling,
        }
        try:
            r = mt5.order_send(request)
            if r and r.retcode != mt5.TRADE_RETCODE_DONE:
                LOG.error(
                    "Fermeture position %s | retcode=%s comment=%s",
                    pos.ticket, getattr(r, "retcode", None), getattr(r, "comment", ""),
                )
            else:
                exec_close = getattr(r, "price", None) or price_close
                pnl_theoretical = (exec_close - pos.price_open) * pos.volume if pos.type == mt5.POSITION_TYPE_BUY else (pos.price_open - exec_close) * pos.volume
                _analytics_log({
                    "ts_ns": int(time.time() * 1e9),
                    "source": "python",
                    "type": "close",
                    "reason": "FLIP",
                    "ticket": pos.ticket,
                    "position_type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "price_open": pos.price_open,
                    "price_close": round(exec_close, 5),
                    "volume": pos.volume,
                    "pnl_theoretical": round(pnl_theoretical, 5),
                    "diagnostic": "PnL théorique (sans frais/spread). Réel = historique MT5 moins commission/spread.",
                })
                LOG.info(
                    "CLOSE | ticket=%s | reason=FLIP | price=%s",
                    pos.ticket,
                    round(price_close, 2),
                )
        except Exception as e:
            LOG.exception("Erreur fermeture position %s: %s", pos.ticket, e)


def modify_sl(symbol: str, ticket: int, new_sl: float):
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        return
    pos = pos_list[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": symbol,
        "sl": round(new_sl, 2),
        "tp": pos.tp,
    }
    try:
        r = mt5.order_send(request)
        if r and r.retcode != mt5.TRADE_RETCODE_DONE:
            LOG.warning("Modify SL ticket %s: %s", ticket, r.comment)
    except Exception as e:
        LOG.exception("Erreur modify_sl %s: %s", ticket, e)


def sync_handle_signal(signal: str, cfg: dict):
    """Traite un signal BUY/SELL : Auto-Flip + ouverture. Bloquant, à appeler dans executor."""
    global _state
    ts_received = time.time()
    ts_ns = int(ts_received * 1e9)
    mt5_cfg = cfg["mt5"]
    filters = cfg.get("filters", {})
    symbol = mt5_cfg["symbol"]
    lot = mt5_cfg["lot_size"]
    sl_pt = mt5_cfg["sl_points"]
    tp_pt = mt5_cfg["tp_points"]
    magic = mt5_cfg["magic"]
    cooldown_sec = filters.get("cooldown_after_open_sec", 0)

    _analytics_log({"ts_ns": ts_ns, "source": "python", "type": "signal_received", "side": signal})

    cooldown_opposite_sec = filters.get("cooldown_opposite_sec", 0)
    if cooldown_opposite_sec > 0 and _state["last_open_side"] is not None and _state["last_open_side"] != signal:
        elapsed = time.time() - _state["last_open_time"]
        if elapsed < cooldown_opposite_sec:
            _analytics_log({
                "ts_ns": int(time.time() * 1e9),
                "source": "python",
                "type": "diagnostic",
                "reason": "cooldown_opposite",
                "side": signal,
                "diagnostic": "Signal opposé ignoré (elapsed < cooldown_opposite_sec). Paramètre trop restrictif ou whipsaw.",
                "elapsed_sec": round(elapsed, 2),
                "cooldown_opposite_sec": cooldown_opposite_sec,
            })
            LOG.info(
                "SKIP FILTRE | %s | cooldown_opposite (dernier %s il y a %.0fs)",
                signal, _state["last_open_side"], elapsed,
            )
            return

    if cooldown_sec > 0 and _state["last_open_side"] == signal:
        elapsed = time.time() - _state["last_open_time"]
        if elapsed < cooldown_sec:
            _analytics_log({
                "ts_ns": int(time.time() * 1e9),
                "source": "python",
                "type": "diagnostic",
                "reason": "cooldown_same_side",
                "side": signal,
                "diagnostic": "Même sens ignoré (cooldown_after_open_sec)",
                "elapsed_sec": round(elapsed, 2),
            })
            LOG.info(
                "SKIP FILTRE | %s | cooldown_même_sens (%.0fs < %ss)",
                signal, elapsed, cooldown_sec,
            )
            return

    tick = _get_tick(symbol)
    if tick is None:
        _analytics_log({
            "ts_ns": int(time.time() * 1e9),
            "source": "python",
            "type": "diagnostic",
            "reason": "tick_unavailable",
            "side": signal,
            "diagnostic": "symbol_info_tick retourne None après retries (MT5/symbole indisponible)",
        })
        LOG.error("SKIP | %s | tick None (MT5 ou symbole indisponible)", signal)
        return

    skip_filters = filters.get("disable_filters_for_testing", False)
    if not skip_filters and not _check_atr_filter(symbol, filters):
        _analytics_log({
            "ts_ns": int(time.time() * 1e9),
            "source": "python",
            "type": "diagnostic",
            "reason": "atr_filter",
            "side": signal,
            "diagnostic": "ATR < atr_min_ratio * moyenne (range).",
        })
        LOG.info("SKIP FILTRE | %s | ATR (volatilité trop faible)", signal)
        return

    price_for_trend = tick.ask if signal == "BUY" else tick.bid
    if not skip_filters and not _check_trend_filter(symbol, signal, price_for_trend, filters):
        _analytics_log({
            "ts_ns": int(time.time() * 1e9),
            "source": "python",
            "type": "diagnostic",
            "reason": "trend_filter",
            "side": signal,
            "price": price_for_trend,
            "diagnostic": "Prix contre tendance (BUY sous SMA / SELL au-dessus).",
        })
        LOG.info("SKIP FILTRE | %s | trend (prix contre SMA)", signal)
        return

    max_spread = filters.get("max_allowed_spread")
    if not skip_filters and max_spread is not None:
        info = mt5.symbol_info(symbol)
        if info is None:
            LOG.warning("symbol_info(%s) = None, skip spread check", symbol)
        else:
            current_spread = info.spread
            if current_spread > max_spread:
                _analytics_log({
                    "ts_ns": int(time.time() * 1e9),
                    "source": "python",
                    "type": "diagnostic",
                    "reason": "spread_too_high",
                    "side": signal,
                    "spread": current_spread,
                    "max_allowed_spread": max_spread,
                    "diagnostic": "Spread trop élevé.",
                })
                LOG.info(
                    "SKIP FILTRE | %s | spread=%s > max=%s",
                    signal, current_spread, max_spread,
                )
                return

    if signal == "BUY":
        close_positions_by_type(symbol, mt5.POSITION_TYPE_SELL, magic)
        price = tick.ask
        order_type = mt5.ORDER_TYPE_BUY
        sl = round(price - sl_pt, 2)
        tp = round(price + tp_pt, 2)
    elif signal == "SELL":
        close_positions_by_type(symbol, mt5.POSITION_TYPE_BUY, magic)
        price = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
        sl = round(price + sl_pt, 2)
        tp = round(price - tp_pt, 2)
    else:
        return

    info = mt5.symbol_info(symbol)
    spread_at_order = info.spread if info else None
    ts_before_send = time.time()
    latency_signal_to_order_ms = round((ts_before_send - ts_received) * 1000, 2)

    _analytics_log({
        "ts_ns": int(ts_before_send * 1e9),
        "source": "python",
        "type": "order_attempt",
        "side": signal,
        "spread": spread_at_order,
        "price_requested": price,
        "latency_signal_to_order_ms": latency_signal_to_order_ms,
    })

    filling = _get_symbol_filling(symbol)
    LOG.info(
        "ENVOI ORDRE | %s | price=%.2f lot=%s sl=%.2f tp=%.2f | filling=%s",
        signal, price, lot, sl, tp, "IOC" if filling == mt5.ORDER_FILLING_IOC else "FOK",
    )
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "magic": magic,
        "type_filling": filling,
    }
    try:
        r = mt5.order_send(request)
        ts_after_send = time.time()
        latency_total_ms = round((ts_after_send - ts_received) * 1000, 2)
        if r is None:
            err = mt5.last_error()
            _analytics_log({
                "ts_ns": int(ts_after_send * 1e9),
                "source": "python",
                "type": "diagnostic",
                "reason": "order_send_returned_none",
                "side": signal,
                "diagnostic": "mt5.order_send a retourné None (MT5 ou connexion).",
                "last_error": str(err) if err else None,
            })
            LOG.error("order_send returned None | last_error=%s", err)
            return
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            _analytics_log({
                "ts_ns": int(ts_after_send * 1e9),
                "source": "python",
                "type": "order_rejected",
                "side": signal,
                "retcode": r.retcode,
                "comment": getattr(r, "comment", ""),
                "spread": spread_at_order,
                "price_requested": price,
                "latency_ms": latency_total_ms,
                "diagnostic": "Ordre refusé par le broker (retcode/comment). Vérifier liquidité, marge, symbol.",
            })
            LOG.error(
                "ORDER REJETÉ | %s | retcode=%s comment=%s",
                signal, r.retcode, getattr(r, "comment", ""),
            )
            if r.retcode == 10027:
                LOG.error(
                    ">>> MT5 : ACTIVER le trading auto → bouton 'Algo Trading' (barre d'outils) "
                    "OU Outils > Options > Expert Advisors > cocher 'Autoriser le trading algorithmique'"
                )
            return
        exec_price = r.price if hasattr(r, "price") and r.price else price
        slippage = round(abs(exec_price - price), 5)
        ticket = getattr(r, "order", None) or getattr(r, "deal", None)
        _analytics_log({
            "ts_ns": int(ts_after_send * 1e9),
            "source": "python",
            "type": "execution",
            "side": signal,
            "ticket": ticket,
            "spread_at_order": spread_at_order,
            "price_requested": price,
            "price_executed": exec_price,
            "slippage": slippage,
            "latency_signal_to_order_ms": latency_signal_to_order_ms,
            "latency_signal_to_fill_ms": latency_total_ms,
            "diagnostic": "Slippage = coût immédiat vs spread. Comparer théorique (price_requested) vs réel (price_executed) pour impact broker.",
        })
        LOG.info(
            "FILL | ticket=%s | %s | price=%.2f | slippage=%.2f",
            ticket, signal, exec_price, slippage,
        )
        _state["last_open_side"] = signal
        _state["last_open_time"] = time.time()
    except Exception as e:
        _analytics_log({
            "ts_ns": int(time.time() * 1e9),
            "source": "python",
            "type": "diagnostic",
            "reason": "order_send_exception",
            "side": signal,
            "error": str(e),
            "diagnostic": "Exception lors de order_send (MT5, réseau, ou paramètres invalides).",
        })
        LOG.exception("Erreur order_send %s: %s", signal, e)


def sync_break_even_loop(cfg: dict):
    """Passe les SL en break-even si le profit dépasse le seuil. Bloquant."""
    mt5_cfg = cfg["mt5"]
    symbol = mt5_cfg["symbol"]
    be_threshold = mt5_cfg["be_threshold_points"]
    be_offset = mt5_cfg["be_offset_points"]
    magic = mt5_cfg["magic"]

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return
    tick = _get_tick(symbol)
    if tick is None:
        return
    for pos in positions:
        if pos.magic != magic:
            continue
        if pos.type == mt5.POSITION_TYPE_BUY:
            profit_pt = tick.bid - pos.price_open
            if profit_pt >= be_threshold and (pos.sl < pos.price_open or pos.sl == 0):
                new_sl = pos.price_open + be_offset
                modify_sl(symbol, pos.ticket, new_sl)
        else:
            profit_pt = pos.price_open - tick.ask
            if profit_pt >= be_threshold and (pos.sl > pos.price_open or pos.sl == 0):
                new_sl = pos.price_open - be_offset
                modify_sl(symbol, pos.ticket, new_sl)


# ---------------------------------------------------------------------------
# Async : écoute Redis + Break-Even en parallèle
# ---------------------------------------------------------------------------
def _sync_redis_listener_loop(cfg: dict):
    """Boucle bloquante d'écoute Redis (fallback si redis.asyncio absent)."""
    import redis
    r = redis.Redis(
        host=cfg["redis"]["host"],
        port=cfg["redis"]["port"],
        db=0,
        decode_responses=True,
    )
    channel = cfg["redis"]["channel"]
    host, port = cfg["redis"]["host"], cfg["redis"]["port"]
    pubsub = r.pubsub()
    pubsub.subscribe(channel)
    LOG.info("Écoute Redis | canal=%s | %s:%s", channel, host, port)
    while True:
        msg = pubsub.get_message()
        if msg and msg.get("type") == "message":
            signal = msg.get("data")
            LOG.info("REDIS RECU: %s", signal)
            if signal == "CLOSE":
                sync_close_all(cfg)
            elif signal in ("BUY", "SELL"):
                sync_handle_signal(signal, cfg)
        time.sleep(0.01)


async def listen_redis_and_execute(cfg: dict, executor: ThreadPoolExecutor):
    try:
        import redis.asyncio as aioredis
    except ImportError:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(executor, _sync_redis_listener_loop, cfg)
        return

    host, port = cfg["redis"]["host"], cfg["redis"]["port"]
    r = aioredis.from_url(
        f"redis://{host}:{port}/0",
        decode_responses=True,
    )
    channel = cfg["redis"]["channel"]
    LOG.info("Écoute Redis | canal=%s | %s:%s", channel, host, port)
    loop = asyncio.get_event_loop()
    async with r.pubsub() as pubsub:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            signal = message.get("data")
            LOG.info("REDIS RECU: %s", signal)
            if signal == "CLOSE":
                await loop.run_in_executor(executor, sync_close_all, cfg)
            elif signal in ("BUY", "SELL"):
                await loop.run_in_executor(
                    executor, sync_handle_signal, signal, cfg
                )


async def break_even_task(cfg: dict, executor: ThreadPoolExecutor, interval: float = 0.05):
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(interval)
        await loop.run_in_executor(executor, sync_break_even_loop, cfg)


async def main():
    cfg = load_config()
    symbol = cfg["mt5"]["symbol"]
    LOG.info("Bridge OFI démarré | Broker: %s | Symbol: %s", cfg.get("broker", "VT Markets"), symbol)

    if not mt5.initialize():
        LOG.error("MT5 initialize() a échoué")
        sys.exit(1)

    # Vérifier que le trading algorithmique est autorisé (sinon retcode 10027)
    ti = mt5.terminal_info()
    if ti is None:
        LOG.warning("MT5 terminal_info() = None — impossible de vérifier trade_allowed")
    elif not getattr(ti, "trade_allowed", True):
        LOG.error(
            "TRADING AUTO DÉSACTIVÉ (retcode 10027). "
            "ACTIVER : bouton 'Algo Trading' dans la barre d'outils OU Outils > Options > Expert Advisors > 'Autoriser le trading algorithmique'."
        )
    else:
        LOG.info("MT5 | Trading algorithmique autorisé (trade_allowed=OK)")

    # Activer le symbole dans la Market Watch (nécessaire pour trader)
    if not mt5.symbol_select(symbol, True):
        LOG.warning("symbol_select(%s, True) a échoué — vérifier le nom du symbole (ex: BTCUSD vs BTCUSDm)", symbol)
    else:
        info = mt5.symbol_info(symbol)
        if info:
            filling = "IOC" if _get_symbol_filling(symbol) == mt5.ORDER_FILLING_IOC else "FOK"
            LOG.info("Symbole %s activé | filling=%s | trade_mode=%s", symbol, filling, getattr(info, "trade_mode", "?"))

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        await asyncio.gather(
            listen_redis_and_execute(cfg, executor),
            break_even_task(cfg, executor),
        )
    except asyncio.CancelledError:
        pass
    finally:
        executor.shutdown(wait=True)
        mt5.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
