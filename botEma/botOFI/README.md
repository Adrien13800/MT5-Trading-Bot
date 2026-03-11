# Bot OFI — Sniper Elite (VT Markets)

Analyse Order Flow Imbalance (Binance Futures) → signaux Redis → exécution MT5 sur **VT Markets**.

## Contenu du dossier

| Fichier | Rôle |
|--------|------|
| `config_ofi.json` | Paramètres centralisés (Redis, OFI, MT5). À adapter selon l’environnement. |
| `bridge.py` | Bridge Python asyncio : écoute Redis, envoie les ordres MT5, gère le Break-Even. |
| `src/main.rs` | Analyseur Rust : WebSocket Binance depth5, OFI multi-niveaux, publication Redis. |
| `trade_history.log` | Créé à l’exécution : log des signaux, fills, slippage, fermetures (FLIP). |
| `system_analytics.log` | Log exhaustif JSONL (Rust + Python) : carnets, OFI, λ, décisions, spread, slippage, diagnostics — pour analyse Excel/Python. |

## Configuration

- **Broker** : VT Markets (symbol MT5 typiquement `BTCUSD`).
- Éditer `config_ofi.json` pour symbol, lots, SL/TP, seuils OFI, volume minimum (BTC), Redis.

## Lancer l’analyseur (Rust, ex. Ubuntu)

```bash
cd botOFI
cargo build --release
./target/release/ofi-analyzer
```

Placez `config_ofi.json` à la racine du projet (à côté de `Cargo.toml`). Vous pouvez aussi définir `OFI_CONFIG=/chemin/vers/config_ofi.json`.

**Si vous copiez-collez `src/main.rs`** vers le serveur et que la compilation affiche `expected item, found 0` à la ligne 1 : un préfixe `0~` a été ajouté au fichier. Depuis la racine du projet, exécutez :
```bash
bash fix_src_main.sh
```
ou à la main : `sed -i '1s/^0~//' src/main.rs`
Mieux : transférez le fichier par **scp** ou **git** depuis votre machine pour éviter le copier-coller.

## Lancer le bridge (Python, ex. Windows + MT5)

```bash
cd botOFI
pip install -r requirements.txt
python bridge.py
```

MT5 doit être installé et connecté au compte VT Markets.

## Logique (mode Sniper / absorption)

- **OFI** : pondéré sur 5 niveaux (poids 50%, 25%, 15%, 7%, 3%).
- **Fenêtre glissante volume** (Rust) : la liquidité est validée sur les `min_volume_window_ticks` derniers ticks (moyenne). Évite de trader sur un carnet vide ou un pic isolé.
- **Price Impact λ** (Rust) : λ = |OFI| / (ΔP + ε). Si OFI fort mais ΔP quasi nul → absorption. Signal **uniquement** si OFI &gt; seuil **et** λ ≥ `min_tick_intensity` (`absorption_sensitivity` = ε).
- **Confirmation** (Rust) : signal publié seulement si la condition reste vraie pendant `confirm_ticks` ticks consécutifs.
- **Heures de trading** (Rust) : `trading_hours_utc` — aucun signal en dehors de la plage.
- **Filtre spread** (Python) : si `symbol_info.spread` &gt; `max_allowed_spread` (points), l’ordre est **avorté** pour limiter le drawdown lié au spread.
- **Filtre ATR** (Python) : pas d’ouverture si volatilité insuffisante (config : `atr_min_ratio`).
- **Cooldown après ouverture** (Python) : pas de nouveau trade dans le même sens avant `cooldown_after_open_sec`.
- **Auto-Flip** : fermeture des positions opposées avant ouverture.
- **Break-Even** : déplacement du SL à l’entrée + marge après seuil de points.
- **Logs** : signaux, fills, slippage, fermetures (FLIP ; SL/TP par MT5).

## system_analytics.log (analyse technique)

Format **JSONL** (une ligne JSON par événement). Champs principaux :

- **Rust**  
  - `type: "tick"` : `bids` / `asks` (5 niveaux [prix, volume]), `ofi_raw`, `ofi_normalized`, `total_vol`, `avg_vol`, `delta_p`, `price_impact`, `latency_ns`.  
  - `type: "decision"` : `reason` (volume_window_insufficient, absorption_intensity_below_min, outside_trading_hours, cooldown_opposite, hysteresis_not_reached), `diagnostic` (commentaire sur le paramètre).  
  - `type: "signal_sent"` : côté et métriques au moment de l’envoi.
- **Python**  
  - `type: "signal_received"` : réception du signal.  
  - `type: "order_attempt"` : `spread`, `price_requested`, `latency_signal_to_order_ms`.  
  - `type: "execution"` : `spread_at_order`, `price_requested`, `price_executed`, `slippage`, `latency_signal_to_fill_ms`.  
  - `type: "close"` : `reason: "FLIP"`, `price_open`, `price_close`, `pnl_theoretical`.  
  - `type: "diagnostic"` : raison du skip (spread_too_high, atr_filter, trend_filter, etc.) et message explicatif.

Import : `import json; [json.loads(l) for l in open("system_analytics.log")]` ou lecture ligne à ligne dans Excel/Pandas.
