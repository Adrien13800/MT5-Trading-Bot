//! SNIPER ELITE - Analyseur OFI multi-niveaux (Binance Futures -> Redis)
//! Broker cible : VT Markets (execution via bridge Python/MT5)

use chrono::{Timelike, Utc};
use std::collections::VecDeque;
use futures_util::StreamExt;
use redis::AsyncCommands;
use serde::Deserialize;
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, Instant};
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use url::Url;

/// Chemins cherchés (dans l'ordre). Utilisez OFI_CONFIG=/chemin/vers/config_ofi.json pour forcer.
const CONFIG_PATHS: [&str; 5] = [
    "config_ofi.json",           // racine projet (cargo run)
    "../config_ofi.json",        // si lancé depuis src/
    "botOFI/config_ofi.json",
    "../botOFI/config_ofi.json",
    "./config_ofi.json",
];

#[derive(Debug, Clone, Deserialize)]
struct Config {
    #[serde(default)]
    analytics: Option<AnalyticsConfig>,
    redis: RedisConfig,
    ofi: OfiConfig,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
struct AnalyticsConfig {
    /// Ne logger qu'un tick sur N (1 = tous, 30 = ~2/sec si 60 ticks/sec).
    #[serde(default = "default_log_tick_every_n")]
    log_tick_every_n: u32,
    /// Logger les décisions "volume_window_insufficient" (réservé, non utilisé par Sniper).
    #[serde(default)]
    log_decision_volume_insufficient: bool,
}

fn default_log_tick_every_n() -> u32 {
    1
}

#[derive(Debug, Clone, Deserialize)]
struct RedisConfig {
    host: String,
    port: u16,
    channel: String,
}

#[derive(Debug, Clone, Deserialize)]
struct TradingHoursUtc {
    start: u8,
    end: u8,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
struct OfiConfig {
    #[serde(default)]
    weights: Vec<f64>,
    threshold_buy: f64,
    threshold_sell: f64,
    #[serde(default = "default_threshold_buy_hyst")]
    threshold_buy_hysteresis: f64,
    #[serde(default = "default_threshold_sell_hyst")]
    threshold_sell_hysteresis: f64,
    min_total_volume_btc: f64,
    #[serde(default = "default_volume_window")]
    min_volume_window_ticks: usize,
    cooldown_ms: u64,
    #[serde(default = "default_cooldown_opposite")]
    cooldown_opposite_sec: u64,
    #[serde(default = "default_confirm_ticks")]
    confirm_ticks: u32,
    trading_hours_utc: Option<TradingHoursUtc>,
    #[serde(default = "default_absorption_sensitivity")]
    absorption_sensitivity: f64,
    #[serde(default = "default_min_tick_intensity")]
    min_tick_intensity: f64,
    /// Sniper Confirmé: seuil depth_imbalance pour entrée BUY (défaut 0.75).
    #[serde(default = "default_depth_imbalance_buy")]
    depth_imbalance_buy_threshold: f64,
    /// Sniper Confirmé: seuil depth_imbalance pour sortie CLOSE (défaut -0.50).
    #[serde(default = "default_depth_imbalance_close")]
    depth_imbalance_close_threshold: f64,
    /// Nombre de ticks pour la fenêtre CVD (défaut 20).
    #[serde(default = "default_cvd_ticks")]
    cvd_ticks: usize,
    /// Nombre de ticks consécutifs avec DI > seuil et CVD > 0 avant d'envoyer BUY (défaut 3 = entrées plus sélectives).
    #[serde(default = "default_sniper_confirm_ticks")]
    sniper_confirm_ticks: u32,
    /// CVD minimum pour autoriser un BUY (défaut 0). Augmenter (ex: 15) pour filtrer les tendances faibles.
    #[serde(default)]
    min_cvd_for_buy: f64,
    /// Seuil depth_imbalance pour entrée SELL (défaut -0.90 : carnet très vendeur).
    #[serde(default = "default_depth_imbalance_sell")]
    depth_imbalance_sell_threshold: f64,
    /// Seuil depth_imbalance pour sortie d'une position SELL (défaut 0.50).
    #[serde(default = "default_depth_imbalance_close_sell")]
    depth_imbalance_close_sell_threshold: f64,
    /// CVD minimum (en valeur absolue, côté négatif) pour autoriser un SELL (défaut 0).
    #[serde(default)]
    min_cvd_for_sell: f64,
}

fn default_depth_imbalance_sell() -> f64 {
    -0.90
}
fn default_depth_imbalance_close_sell() -> f64 {
    0.50
}
fn default_depth_imbalance_buy() -> f64 {
    0.90
}
fn default_sniper_confirm_ticks() -> u32 {
    3
}
fn default_depth_imbalance_close() -> f64 {
    -0.50
}
fn default_cvd_ticks() -> usize {
    20
}

fn default_threshold_buy_hyst() -> f64 {
    0.90
}
fn default_threshold_sell_hyst() -> f64 {
    -0.90
}
fn default_cooldown_opposite() -> u64 {
    15
}

fn default_volume_window() -> usize {
    1
}
fn default_absorption_sensitivity() -> f64 {
    0.01
}
fn default_min_tick_intensity() -> f64 {
    5.0
}

fn default_confirm_ticks() -> u32 {
    3
}

fn in_trading_hours(hours: &TradingHoursUtc) -> bool {
    let hour = Utc::now().hour() as u8;
    if hours.start <= hours.end {
        hour >= hours.start && hour < hours.end
    } else {
        hour >= hours.start || hour < hours.end
    }
}

fn load_config() -> Result<Config, Box<dyn std::error::Error>> {
    if let Ok(path) = std::env::var("OFI_CONFIG") {
        let s = fs::read_to_string(&path)?;
        return serde_json::from_str(&s).map_err(|e| e.into());
    }
    for path in CONFIG_PATHS {
        if Path::new(path).exists() {
            let s = fs::read_to_string(path)?;
            return serde_json::from_str(&s).map_err(|e| e.into());
        }
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| Path::new(".").into());
    Err(format!(
        "config_ofi.json introuvable. CWD: {:?}. Placez le fichier à la racine du projet (ex: {}/config_ofi.json) ou définissez OFI_CONFIG=/chemin/vers/config_ofi.json",
        cwd,
        cwd.display()
    ).into())
}

fn redis_url(cfg: &RedisConfig) -> String {
    format!("redis://{}:{}/", cfg.host, cfg.port)
}

async fn run_analyzer(config: Config) -> Result<(), Box<dyn std::error::Error>> {
    let redis_url = redis_url(&config.redis);
    let client = redis::Client::open(redis_url.as_str())?;
    let mut con = client.get_async_connection().await?;

    let ws_url = "wss://fstream.binance.com/ws/btcusdt@depth5@100ms";
    let url = Url::parse(ws_url)?;
    let (mut ws_stream, _) = connect_async(url).await?;

    let th_buy_di = config.ofi.depth_imbalance_buy_threshold;
    let th_close_di = config.ofi.depth_imbalance_close_threshold;
    let th_sell_di = config.ofi.depth_imbalance_sell_threshold;
    let th_close_sell = config.ofi.depth_imbalance_close_sell_threshold;
    let cvd_window = config.ofi.cvd_ticks.max(1);
    let confirm_ticks = config.ofi.sniper_confirm_ticks.max(1);
    let min_cvd_buy = config.ofi.min_cvd_for_buy;
    let min_cvd_sell = config.ofi.min_cvd_for_sell;
    let cooldown = Duration::from_millis(config.ofi.cooldown_ms);
    let channel = config.redis.channel.as_str();
    let trading_hours = config.ofi.trading_hours_utc.as_ref();

    #[derive(Clone, Copy, PartialEq)]
    enum PositionSide {
        Long,
        Short,
    }

    println!(
        "🚀 SNIPER Confirmé | BUY DI>{:.2} close<{:.2} | SELL DI<{:.2} close>{:.2} | CVD {} tks | confirm {} | Heures: {:?}",
        th_buy_di, th_close_di, th_sell_di, th_close_sell, cvd_window, confirm_ticks,
        trading_hours.map(|h| format!("{}-{}h", h.start, h.end))
    );

    let mut position_side: Option<PositionSide> = None;
    let mut buy_confirm_count: u32 = 0;
    let mut sell_confirm_count: u32 = 0;
    let mut cvd_deltas: VecDeque<f64> = VecDeque::with_capacity(cvd_window + 2);

    let log_path = "system_analytics.log";
    let mut log_file = OpenOptions::new().create(true).append(true).open(log_path).ok();

    let log_tick_every_n = config.analytics.as_ref().map(|a| a.log_tick_every_n.max(1)).unwrap_or(1);
    let mut tick_counter: u32 = 0;

    while let Some(msg) = ws_stream.next().await {
        tick_counter = tick_counter.wrapping_add(1);
        let should_log_tick = log_tick_every_n <= 1 || (tick_counter % log_tick_every_n == 0);
        let tick_start = Instant::now();
        let ts_ns = Utc::now().timestamp_nanos_opt().unwrap_or(0);

        let Ok(Message::Text(text)) = msg else {
            continue;
        };
        let v: Value = match serde_json::from_str(&text) {
            Ok(x) => x,
            Err(_) => continue,
        };
        let (Some(bids), Some(asks)) = (v["b"].as_array(), v["a"].as_array()) else {
            continue;
        };
        if bids.len() < 5 || asks.len() < 5 {
            continue;
        }

        let mut total_bid_q: f64 = 0.0;
        let mut total_ask_q: f64 = 0.0;
        let mut bid_levels: Vec<[f64; 2]> = Vec::with_capacity(5);
        let mut ask_levels: Vec<[f64; 2]> = Vec::with_capacity(5);

        for i in 0..5 {
            let bid_p: f64 = bids[i].get(0).and_then(|x| x.as_str()).unwrap_or("0").parse().unwrap_or(0.0);
            let bid_q: f64 = bids[i].get(1).and_then(|x| x.as_str()).unwrap_or("0").parse().unwrap_or(0.0);
            let ask_p: f64 = asks[i].get(0).and_then(|x| x.as_str()).unwrap_or("0").parse().unwrap_or(0.0);
            let ask_q: f64 = asks[i].get(1).and_then(|x| x.as_str()).unwrap_or("0").parse().unwrap_or(0.0);
            bid_levels.push([bid_p, bid_q]);
            ask_levels.push([ask_p, ask_q]);
            total_bid_q += bid_q;
            total_ask_q += ask_q;
        }

        let depth_imbalance = {
            let sum = total_bid_q + total_ask_q;
            if sum > 0.0 {
                (total_bid_q - total_ask_q) / sum
            } else {
                0.0
            }
        };

        let delta = total_bid_q - total_ask_q;
        cvd_deltas.push_back(delta);
        if cvd_deltas.len() > cvd_window {
            cvd_deltas.pop_front();
        }
        let current_cvd: f64 = cvd_deltas.iter().sum();

        let _mid_price = (bid_levels[0][0] + ask_levels[0][0]) * 0.5;
        let latency_ns = tick_start.elapsed().as_nanos() as u64;

        if let Some(ref mut f) = log_file {
            if should_log_tick {
                let _ = writeln!(f, "{}", serde_json::json!({
                    "ts_ns": ts_ns,
                    "source": "rust",
                    "type": "tick",
                    "bids": bid_levels,
                    "asks": ask_levels,
                    "depth_imbalance": depth_imbalance,
                    "current_cvd": current_cvd,
                    "total_bid_q": total_bid_q,
                    "total_ask_q": total_ask_q,
                    "latency_ns": latency_ns
                }));
                let _ = f.flush();
            }
        }

        if let Some(ref th) = trading_hours {
            if !in_trading_hours(th) {
                if let Some(ref mut f) = log_file {
                    let _ = writeln!(f, "{}", serde_json::json!({
                        "ts_ns": ts_ns,
                        "source": "rust",
                        "type": "decision",
                        "reason": "outside_trading_hours",
                        "depth_imbalance": depth_imbalance,
                        "current_cvd": current_cvd
                    }));
                    let _ = f.flush();
                }
                print!("\r OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1}   (hors session)   ", depth_imbalance, cvd_window, current_cvd);
                std::io::stdout().flush().ok();
                continue;
            }
        }

        let no_position = position_side.is_none();
        let buy_conditions_ok = no_position && depth_imbalance > th_buy_di && current_cvd > min_cvd_buy;
        if buy_conditions_ok {
            buy_confirm_count += 1;
            sell_confirm_count = 0;
            if buy_confirm_count >= confirm_ticks {
                let ts_sent = Utc::now().timestamp_nanos_opt().unwrap_or(0);
                if let Some(ref mut f) = log_file {
                    let _ = writeln!(f, "{}", serde_json::json!({
                        "ts_ns": ts_sent,
                        "source": "rust",
                        "type": "signal_sent",
                        "side": "BUY",
                        "depth_imbalance": depth_imbalance,
                        "current_cvd": current_cvd
                    }));
                    let _ = f.flush();
                }
                println!("\n🔥 SNIPER BUY | OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1} (confirm {} tks)", depth_imbalance, cvd_window, current_cvd, confirm_ticks);
                let _: () = con.publish(channel, "BUY").await?;
                position_side = Some(PositionSide::Long);
                buy_confirm_count = 0;
                sleep(cooldown).await;
            }
        } else {
            buy_confirm_count = 0;
        }

        let sell_conditions_ok = no_position && depth_imbalance < th_sell_di && current_cvd < -min_cvd_sell;
        if sell_conditions_ok {
            sell_confirm_count += 1;
            buy_confirm_count = 0;
            if sell_confirm_count >= confirm_ticks {
                let ts_sent = Utc::now().timestamp_nanos_opt().unwrap_or(0);
                if let Some(ref mut f) = log_file {
                    let _ = writeln!(f, "{}", serde_json::json!({
                        "ts_ns": ts_sent,
                        "source": "rust",
                        "type": "signal_sent",
                        "side": "SELL",
                        "depth_imbalance": depth_imbalance,
                        "current_cvd": current_cvd
                    }));
                    let _ = f.flush();
                }
                println!("\n❄️ SNIPER SELL | OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1} (confirm {} tks)", depth_imbalance, cvd_window, current_cvd, confirm_ticks);
                let _: () = con.publish(channel, "SELL").await?;
                position_side = Some(PositionSide::Short);
                sell_confirm_count = 0;
                sleep(cooldown).await;
            }
        } else {
            sell_confirm_count = 0;
        }

        if position_side == Some(PositionSide::Long) && depth_imbalance < th_close_di {
            let ts_sent = Utc::now().timestamp_nanos_opt().unwrap_or(0);
            if let Some(ref mut f) = log_file {
                let _ = writeln!(f, "{}", serde_json::json!({
                    "ts_ns": ts_sent,
                    "source": "rust",
                    "type": "signal_sent",
                    "side": "CLOSE",
                    "depth_imbalance": depth_imbalance,
                    "current_cvd": current_cvd
                }));
                let _ = f.flush();
            }
            println!("\n🔻 SNIPER CLOSE (long) | OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1}", depth_imbalance, cvd_window, current_cvd);
            let _: () = con.publish(channel, "CLOSE").await?;
            position_side = None;
            sleep(cooldown).await;
        }

        if position_side == Some(PositionSide::Short) && depth_imbalance > th_close_sell {
            let ts_sent = Utc::now().timestamp_nanos_opt().unwrap_or(0);
            if let Some(ref mut f) = log_file {
                let _ = writeln!(f, "{}", serde_json::json!({
                    "ts_ns": ts_sent,
                    "source": "rust",
                    "type": "signal_sent",
                    "side": "CLOSE",
                    "depth_imbalance": depth_imbalance,
                    "current_cvd": current_cvd
                }));
                let _ = f.flush();
            }
            println!("\n🔻 SNIPER CLOSE (short) | OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1}", depth_imbalance, cvd_window, current_cvd);
            let _: () = con.publish(channel, "CLOSE").await?;
            position_side = None;
            sleep(cooldown).await;
        }

        print!("\r OFI [5 Levels]: {:.3} | CVD [{} tks]: {:.1}   ", depth_imbalance, cvd_window, current_cvd);
        std::io::stdout().flush().ok();
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config()?;
    let mut delay_ms: u64 = 1000;
    const MAX_DELAY_MS: u64 = 60_000;

    loop {
        match run_analyzer(config.clone()).await {
            Ok(()) => {
                eprintln!("WebSocket fermé normalement, reconnexion...");
            }
            Err(e) => {
                eprintln!("Erreur: {} — reconnexion dans {} ms", e, delay_ms);
            }
        }
        sleep(Duration::from_millis(delay_ms)).await;
        delay_ms = (delay_ms * 2).min(MAX_DELAY_MS);
    }
}
