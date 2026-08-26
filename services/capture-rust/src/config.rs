//! Runtime configuration: the instrument registry on disk and the environment.
//!
//! `config/instruments.yaml` is the single source of truth for what K2
//! subscribes to and for the native -> canonical mapping. A native symbol that
//! is not in the file is a hard error, never a guess: guessing is what produced
//! `XDG/USD` and `DOGE/USD` as two instruments in v2.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{Context, Result, bail};
use serde::Deserialize;

/// The venues this binary knows how to talk to. One process serves one venue.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
#[clap(rename_all = "lowercase")]
pub enum Exchange {
    Kraken,
    Binance,
    Coinbase,
}

impl Exchange {
    /// The lowercase venue identifier that goes in `Trade.exchange`, in the
    /// topic name, and in every metric label.
    pub fn as_str(self) -> &'static str {
        match self {
            Exchange::Kraken => "kraken",
            Exchange::Binance => "binance",
            Exchange::Coinbase => "coinbase",
        }
    }

    /// Public WebSocket endpoint, overridable with `K2_WS_URL` so a test can
    /// point the binary at a local recorder or a staging endpoint.
    pub fn default_ws_url(self) -> &'static str {
        match self {
            Exchange::Kraken => "wss://ws.kraken.com/v2",
            Exchange::Binance => "wss://stream.binance.com:9443/stream",
            Exchange::Coinbase => "wss://advanced-trade-ws.coinbase.com",
        }
    }
}

impl std::fmt::Display for Exchange {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// One row of `config/instruments.yaml`.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct Instrument {
    /// Exactly the string the exchange uses on the wire, byte for byte.
    pub native: String,
    /// `BASE/QUOTE`, uppercase. The Kafka key and the lake join key.
    pub canonical: String,
    /// Per-instrument L2 depth override; unset means the venue default.
    #[serde(default)]
    pub book_depth: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct InstrumentsFile {
    version: u32,
    instruments: BTreeMap<String, Vec<Instrument>>,
}

/// The instruments one exchange subscribes to, in file order.
///
/// Order is preserved and the lookup is a `BTreeMap` rather than a `HashMap`
/// because subscribe frames and the snapshot sampler both iterate this, and
/// replay must reproduce the live ordering exactly.
#[derive(Debug, Clone)]
pub struct Instruments {
    instruments: Vec<Instrument>,
    by_native: BTreeMap<String, String>,
}

impl Instruments {
    pub fn load(path: &Path, exchange: Exchange) -> Result<Self> {
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("reading instrument registry {}", path.display()))?;
        Self::parse(&text, exchange)
    }

    pub fn parse(yaml: &str, exchange: Exchange) -> Result<Self> {
        let file: InstrumentsFile =
            serde_yaml::from_str(yaml).context("parsing the instrument registry")?;
        if file.version != 2 {
            bail!(
                "instrument registry is version {}, this build reads version 2 \
                 (v1 put the native -> canonical mapping in code, not data)",
                file.version
            );
        }
        let instruments = file
            .instruments
            .get(exchange.as_str())
            .with_context(|| format!("registry has no `{exchange}` section"))?
            .clone();
        if instruments.is_empty() {
            bail!("registry lists no instruments for {exchange}");
        }
        Self::from_list(instruments)
    }

    fn from_list(instruments: Vec<Instrument>) -> Result<Self> {
        let mut by_native = BTreeMap::new();
        for i in &instruments {
            if by_native
                .insert(i.native.clone(), i.canonical.clone())
                .is_some()
            {
                bail!("native symbol {} is listed twice", i.native);
            }
        }
        Ok(Self {
            instruments,
            by_native,
        })
    }

    /// Keep only the listed native symbols - the fixture recorder trims a
    /// 20 second capture to two instruments so the committed file stays small.
    pub fn retain_native(&mut self, keep: &[String]) -> Result<()> {
        for want in keep {
            if !self.by_native.contains_key(want) {
                bail!("--symbols asked for {want}, which the registry does not list");
            }
        }
        self.instruments.retain(|i| keep.contains(&i.native));
        self.by_native.retain(|k, _| keep.contains(k));
        Ok(())
    }

    pub fn natives(&self) -> Vec<String> {
        self.instruments.iter().map(|i| i.native.clone()).collect()
    }

    pub fn iter(&self) -> impl Iterator<Item = &Instrument> {
        self.instruments.iter()
    }

    /// Native -> canonical. `None` means the venue sent us an instrument we did
    /// not subscribe to, which is a bug in the subscribe frame, not a symbol to
    /// invent a mapping for.
    pub fn canonical(&self, native: &str) -> Option<&str> {
        self.by_native.get(native).map(String::as_str)
    }

    pub fn len(&self) -> usize {
        self.instruments.len()
    }

    pub fn is_empty(&self) -> bool {
        self.instruments.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const YAML: &str = r#"
version: 2
instruments:
  kraken:
    - { native: XBT/USD, canonical: BTC/USD }
    - { native: XDG/USD, canonical: DOGE/USD }
  binance:
    - { native: BTCUSDT, canonical: BTC/USDT }
"#;

    #[test]
    fn maps_native_to_canonical_without_guessing() {
        let i = Instruments::parse(YAML, Exchange::Kraken).unwrap();
        assert_eq!(i.len(), 2);
        assert_eq!(i.canonical("XBT/USD"), Some("BTC/USD"));
        assert_eq!(i.canonical("XDG/USD"), Some("DOGE/USD"));
        assert_eq!(i.canonical("DOGE/USD"), None, "canonical is not a native");
        assert_eq!(i.natives(), vec!["XBT/USD", "XDG/USD"]);
    }

    #[test]
    fn rejects_a_v1_shaped_file() {
        let v1 = "version: 1\ninstruments:\n  kraken: []\n";
        let err = Instruments::parse(v1, Exchange::Kraken)
            .unwrap_err()
            .to_string();
        assert!(err.contains("version 1"), "{err}");
    }

    #[test]
    fn rejects_an_unknown_exchange_section() {
        let err = Instruments::parse(YAML, Exchange::Coinbase)
            .unwrap_err()
            .to_string();
        assert!(err.contains("coinbase"), "{err}");
    }

    #[test]
    fn rejects_a_duplicate_native() {
        let dup = "version: 2\ninstruments:\n  kraken:\n    - { native: XBT/USD, canonical: BTC/USD }\n    - { native: XBT/USD, canonical: XBT/USD }\n";
        let err = Instruments::parse(dup, Exchange::Kraken)
            .unwrap_err()
            .to_string();
        assert!(err.contains("twice"), "{err}");
    }

    #[test]
    fn retain_native_trims_and_rejects_unknowns() {
        let mut i = Instruments::parse(YAML, Exchange::Kraken).unwrap();
        i.retain_native(&["XBT/USD".to_string()]).unwrap();
        assert_eq!(i.natives(), vec!["XBT/USD"]);
        assert!(i.retain_native(&["NOPE/USD".to_string()]).is_err());
    }

    /// The registry that actually ships must load for all three venues; this is
    /// the test that fails if someone edits `config/instruments.yaml` into a
    /// shape the capture tier cannot read.
    #[test]
    fn the_repo_registry_loads() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml");
        for (ex, want) in [
            (Exchange::Binance, 12),
            (Exchange::Kraken, 11),
            (Exchange::Coinbase, 11),
        ] {
            let i = Instruments::load(&path, ex).unwrap();
            assert_eq!(i.len(), want, "{ex} instrument count");
        }
        // Kraken's natives are the WS v2 spellings and nothing translates them
        // any more (ADR-019 retired the v1 handlers that forced the alias). The
        // v1 spelling must not resolve: if it did, something is aliasing again.
        let kraken = Instruments::load(&path, Exchange::Kraken).unwrap();
        assert_eq!(kraken.canonical("BTC/USD"), Some("BTC/USD"));
        assert_eq!(kraken.canonical("DOGE/USD"), Some("DOGE/USD"));
        assert_eq!(
            kraken.canonical("XBT/USD"),
            None,
            "v1 spelling still listed"
        );
        assert_eq!(
            kraken.canonical("XDG/USD"),
            None,
            "v1 spelling still listed"
        );
    }
}
