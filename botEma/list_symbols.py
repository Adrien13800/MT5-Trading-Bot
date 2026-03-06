"""Liste les symboles disponibles sur le terminal MT5 connecté."""
import MetaTrader5 as mt5
import sys

try:
    import config
    acct = None
    if len(sys.argv) > 1:
        name = sys.argv[1]
        acct = config.ACCOUNTS.get(name)
        if not acct:
            print(f"Compte '{name}' introuvable. Disponibles: {list(config.ACCOUNTS.keys())}")
            sys.exit(1)
except ImportError:
    print("config.py introuvable")
    sys.exit(1)

path = (acct or {}).get("MT5_TERMINAL_PATH") or getattr(config, "MT5_TERMINAL_PATH", None)
init_kwargs = {"path": path} if path else {}
if not mt5.initialize(**init_kwargs):
    print(f"Erreur init MT5: {mt5.last_error()}")
    sys.exit(1)

if acct:
    if not mt5.login(login=acct["MT5_LOGIN"], password=acct["MT5_PASSWORD"], server=acct["MT5_SERVER"]):
        print(f"Erreur login: {mt5.last_error()}")
        sys.exit(1)

keywords = ["DJ", "DOW", "US30", "NAS", "US100", "SP", "US500", "NDX", "SPX"]
all_symbols = mt5.symbols_get()
print(f"\nTotal symboles sur ce serveur: {len(all_symbols)}\n")
print("Symboles correspondants (indices US):")
print("-" * 50)
for sym in sorted(all_symbols, key=lambda s: s.name):
    name_upper = sym.name.upper()
    if any(kw in name_upper for kw in keywords):
        print(f"  {sym.name}")

mt5.shutdown()
