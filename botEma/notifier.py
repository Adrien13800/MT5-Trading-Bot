"""
Module de notification Telegram partage entre les bots de trading.

Usage simple:
    from notifier import send_telegram
    send_telegram(token, chat_id, "Hello <b>World</b>")

Usage avec classe:
    from notifier import Notifier
    n = Notifier(token, chat_id)
    n.bot_started("Mon Bot", "Config...")
    n.trade_buy("EURUSD", 1.1234, 0.01, 110.0)
"""

import urllib.request
import urllib.parse
import ssl
import re

# Contexte SSL permissif (certains serveurs Windows ont des CA obsoletes)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def send_telegram(token: str, chat_id: str, message: str, timeout: int = 10) -> bool:
    """Envoie un message Telegram en HTML. Fallback en texte brut si le HTML est invalide."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Premiere tentative : HTML
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx)
        return True
    except Exception:
        pass

    # Fallback : strip HTML et renvoyer en texte brut
    plain = re.sub(r"<[^>]+>", "", message)
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": plain,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx)
        return True
    except Exception:
        return False


class Notifier:
    """Wrapper haut niveau pour les notifications Telegram."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def notify(self, message: str) -> bool:
        return send_telegram(self.token, self.chat_id, message)

    def bot_started(self, bot_name: str, details: str = "") -> bool:
        msg = f"<b>{bot_name} demarre</b>"
        if details:
            msg += f"\n{details}"
        return self.notify(msg)

    def bot_stopped(self, bot_name: str, details: str = "") -> bool:
        msg = f"<b>{bot_name} arrete</b>"
        if details:
            msg += f"\n{details}"
        return self.notify(msg)

    def trade_buy(self, symbol: str, price: float, qty: float, amount: float) -> bool:
        return self.notify(
            f"<b>ACHAT {symbol}</b>\n"
            f"Prix: {price:.6f}\n"
            f"Qty: {qty} | ~${amount:.2f}"
        )

    def trade_sell(self, symbol: str, price: float, qty: float, amount: float) -> bool:
        return self.notify(
            f"<b>VENTE {symbol}</b>\n"
            f"Prix: {price:.6f}\n"
            f"Qty: {qty} | ~${amount:.2f}"
        )

    def stop_triggered(self, symbol: str, details: str = "") -> bool:
        msg = f"<b>STOP {symbol}</b>"
        if details:
            msg += f"\n{details}"
        return self.notify(msg)

    def error(self, message: str) -> bool:
        return self.notify(f"<b>ERREUR</b>\n{message}")
