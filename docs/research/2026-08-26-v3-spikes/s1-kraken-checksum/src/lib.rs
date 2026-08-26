/// Kraken spot WS v2 book checksum.
/// https://docs.kraken.com/api/docs/guides/spot-ws-book-v2
pub fn checksum(asks: &[(f64, f64)], bids: &[(f64, f64)], price_precision: usize, qty_precision: usize) -> u32 {
    let mut h = crc32fast::Hasher::new();
    h.update(level_str(&asks[..asks.len().min(10)], price_precision, qty_precision).as_bytes());
    h.update(level_str(&bids[..bids.len().min(10)], price_precision, qty_precision).as_bytes());
    h.finalize()
}

/// Per level: price then qty, each fixed to its precision, decimal point removed,
/// leading zeros stripped. Caller supplies asks low->high, bids high->low.
pub fn level_str(levels: &[(f64, f64)], price_precision: usize, qty_precision: usize) -> String {
    let mut out = String::new();
    for &(px, qty) in levels {
        out.push_str(&digits(px, price_precision));
        out.push_str(&digits(qty, qty_precision));
    }
    out
}

fn digits(v: f64, precision: usize) -> String {
    let s = format!("{:.*}", precision, v);
    let s: String = s.chars().filter(|c| *c != '.').collect();
    let t = s.trim_start_matches('0');
    if t.is_empty() { "0".to_string() } else { t.to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Doc worked example: BTC/USD, price_precision=1, qty_precision=8.
    // Levels reconstructed from the doc's published concatenation (see ASKS_STR/BIDS_STR).
    const ASKS: [(f64, f64); 10] = [
        (45285.2, 0.00100000),
        (45286.4, 1.54571953),
        (45286.6, 1.54571109),
        (45289.6, 1.54560911),
        (45290.2, 0.15890660),
        (45291.8, 1.54553491),
        (45294.7, 0.04454749),
        (45296.1, 0.35380000),
        (45297.5, 0.09945542),
        (45299.5, 0.18772827),
    ];
    const BIDS: [(f64, f64); 10] = [
        (45283.5, 0.10000000),
        (45283.4, 1.54582015),
        (45282.1, 0.10000000),
        (45281.0, 0.10000000),
        (45280.3, 1.54592586),
        (45279.0, 0.07990000),
        (45277.6, 0.03310103),
        (45277.5, 0.30000000),
        (45277.3, 1.54602737),
        (45276.6, 0.15445238),
    ];

    const ASKS_STR: &str = "45285210000045286415457195345286615457110945289615456091145290215890660452918154553491452947445474945296135380000452975994554245299518772827";
    const BIDS_STR: &str = "452835100000004528341545820154528211000000045281010000000452803154592586452790799000045277633101034527753000000045277315460273745276615445238";

    #[test]
    fn doc_example() {
        assert_eq!(checksum(&ASKS, &BIDS, 1, 8), 3310070434);
    }

    #[test]
    fn doc_example_string_reconstruction() {
        // proves the reconstructed levels are the doc's levels
        assert_eq!(super::level_str(&ASKS, 1, 8), ASKS_STR);
        assert_eq!(super::level_str(&BIDS, 1, 8), BIDS_STR);
    }
}
