//! Decimal text -> `i64` at a fixed scale of 1e-8.
//!
//! The v3 wire contract (`schemas/README.md`) carries every price and quantity
//! as an `i64` holding `round(decimal * 1e8)`. This module is the only place a
//! decimal ever becomes a number, and it never goes through `f64`.
//!
//! **Why no `f64`.** Two reasons, and the second is the one that bites.
//! Precision: `f64` has 15-16 significant decimal digits, and a price like
//! `45285.20000000` plus a quantity like `0.18772827` already sits close enough
//! to that edge that `(x * 1e8) as i64` can land one unit low. Determinism
//! (`docs/research/2026-08-26-v3-requirements-clarification.md` Q1): replay must
//! produce byte-identical output to the live run, and float rounding is a
//! property of the code path, not of the input - a compiler that fuses a
//! multiply-add changes the answer without changing the source.
//!
//! The input is the **raw JSON text** of the number, not a parsed value.
//! `serde_json::value::RawValue` hands back exactly the bytes the exchange
//! sent, so `78980.2` arrives here as the five characters it was on the wire.

/// Fixed-point scale for every price and quantity on the v3 wire: 1e-8.
pub const SCALE: i64 = 100_000_000;

/// Number of decimal places `SCALE` represents.
pub const SCALE_DP: u32 = 8;

/// Why a decimal string could not become an exact `i64` at 1e-8.
///
/// Every variant drops the record it came from. None of them round: a rounded
/// price is a wrong price that looks right forever, where a dropped record and
/// a counter that ticks is a bug someone fixes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecimalError {
    /// More than 8 decimal places - cannot be held at 1e-8 without rounding.
    TooManyDecimals,
    /// Scientific notation (`1e-8`, `5.1E-05`). Rejected on purpose: no venue
    /// K2 captures emits it (1489 consecutive Kraken v2 frames sampled
    /// 2026-08-26 contained zero), so accepting it would add a second, subtler
    /// parse path - exponent sign, exponent overflow, digit shifting - for
    /// input that never arrives. If a venue starts emitting it, this error is
    /// the alert, and the fix is a shift on the digit string, still no `f64`.
    Scientific,
    /// Not a decimal number: empty, a sign, a stray character, a bare `.`, a
    /// leading `.5` or a trailing `123.`. The last two are not valid JSON
    /// numbers either, so seeing one means our own slicing is wrong.
    Malformed,
    /// The scaled value does not fit in `i64`. At 1e-8 the ceiling is ~9.2e10,
    /// which is ~2 million times the highest price any captured venue quotes.
    Overflow,
    /// Negative. Prices and quantities on this path are never negative; a minus
    /// sign means we are parsing the wrong field.
    Negative,
}

/// Parse a decimal string to `i64` units of 1e-8.
///
/// ```text
/// "45285.2"    -> 4_528_520_000_000
/// "0.00000001" -> 1
/// "0"          -> 0
/// ```
///
/// Accepts only `<digits>` or `<digits>.<digits>` with at most 8 fractional
/// digits. Everything else is a [`DecimalError`]; see its variants for why each
/// rejection is a rejection rather than a rounding.
pub fn parse_fixed(s: &str) -> Result<i64, DecimalError> {
    let b = s.as_bytes();
    if b.is_empty() {
        return Err(DecimalError::Malformed);
    }
    if b[0] == b'-' {
        return Err(DecimalError::Negative);
    }

    let mut units: i64 = 0;
    let mut frac_digits: u32 = 0;
    let mut seen_point = false;
    let mut seen_digit_before_point = false;
    let mut seen_digit_after_point = false;

    for &c in b {
        match c {
            b'0'..=b'9' => {
                if seen_point {
                    frac_digits += 1;
                    if frac_digits > SCALE_DP {
                        return Err(DecimalError::TooManyDecimals);
                    }
                    seen_digit_after_point = true;
                } else {
                    seen_digit_before_point = true;
                }
                units = units
                    .checked_mul(10)
                    .and_then(|v| v.checked_add(i64::from(c - b'0')))
                    .ok_or(DecimalError::Overflow)?;
            }
            b'.' if !seen_point => {
                if !seen_digit_before_point {
                    return Err(DecimalError::Malformed); // ".5"
                }
                seen_point = true;
            }
            b'e' | b'E' => return Err(DecimalError::Scientific),
            _ => return Err(DecimalError::Malformed),
        }
    }

    if seen_point && !seen_digit_after_point {
        return Err(DecimalError::Malformed); // "123."
    }
    if !seen_digit_before_point {
        return Err(DecimalError::Malformed); // "" or "."
    }

    // Left-shift the digits we have to the 1e-8 scale. Exact: `frac_digits <= 8`
    // is enforced above, so this multiplies rather than divides and can only
    // lose to overflow, never to rounding.
    let shift = SCALE_DP - frac_digits;
    let mult = 10i64.pow(shift);
    units.checked_mul(mult).ok_or(DecimalError::Overflow)
}

/// Render `units` (1e-8 fixed point) as the exchange would print it at
/// `precision` decimal places, with the decimal point removed and leading zeros
/// stripped - the digit string Kraken's book checksum is computed over.
///
/// **Why this is integer division and not formatting.** Kraken's documented
/// algorithm is "format to the pair's precision, remove the `.`, strip leading
/// zeros". Removing the point from a fixed-precision rendering *is* the integer
/// `units / 10^(8 - precision)`, and `i64::to_string` emits no leading zeros, so
/// the two steps collapse into one exact division. `45285.2` at precision 1:
/// `4_528_520_000_000 / 10^7 = 452_852`, which is the doc example's `452852`.
///
/// Truncates if `units` is not a multiple of the venue's increment; that cannot
/// happen for a well-formed feed, and if it does the checksum mismatch is
/// exactly the signal we want rather than a silently corrected book.
pub fn checksum_digits(units: i64, precision: u32) -> String {
    debug_assert!(precision <= SCALE_DP, "precision beyond the 1e-8 scale");
    let divisor = 10i64.pow(SCALE_DP.saturating_sub(precision));
    (units / divisor).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use DecimalError::*;

    #[test]
    fn table() {
        let cases: &[(&str, Result<i64, DecimalError>)] = &[
            // The contract's own worked example (schemas/README.md).
            ("45285.2", Ok(4_528_520_000_000)),
            ("0.00184", Ok(184_000)),
            // Boundaries of the scale.
            ("0.00000001", Ok(1)),
            ("0", Ok(0)),
            ("0.0", Ok(0)),
            ("1", Ok(SCALE)),
            ("78980.2", Ok(7_898_020_000_000)),
            ("0.18772827", Ok(18_772_827)),
            // Exactly 8 dp is fine; 9 is not.
            ("1.12345678", Ok(112_345_678)),
            ("1.123456789", Err(TooManyDecimals)),
            ("0.000000001", Err(TooManyDecimals)),
            // Scientific notation: rejected and documented, not rounded.
            ("1e-8", Err(Scientific)),
            ("5.1e-05", Err(Scientific)),
            ("1E8", Err(Scientific)),
            // Negative: not a thing on this path.
            ("-1", Err(Negative)),
            ("-0.5", Err(Negative)),
            // Malformed edges.
            ("123.", Err(Malformed)),
            (".5", Err(Malformed)),
            (".", Err(Malformed)),
            ("", Err(Malformed)),
            ("1.2.3", Err(Malformed)),
            ("1 000", Err(Malformed)),
            ("+1", Err(Malformed)),
            ("abc", Err(Malformed)),
            ("NaN", Err(Malformed)),
            // Overflow: i64 at 1e-8 tops out near 9.2e10.
            ("92233720369", Err(Overflow)),
            ("99999999999999999999", Err(Overflow)),
        ];
        for (input, want) in cases {
            assert_eq!(parse_fixed(input), *want, "parse_fixed({input:?})");
        }
    }

    #[test]
    fn checksum_digits_matches_kraken_doc_formatting() {
        // From the S1 doc example: price precision 1, qty precision 8.
        assert_eq!(checksum_digits(4_528_520_000_000, 1), "452852");
        assert_eq!(checksum_digits(100_000, 8), "100000");
        assert_eq!(checksum_digits(18_772_827, 8), "18772827");
        assert_eq!(checksum_digits(0, 8), "0");
    }

    #[test]
    fn round_trip_through_the_scale_is_exact() {
        for units in [1i64, 184_000, 4_528_520_000_000, 99_999_999] {
            let text = format!("{}.{:08}", units / SCALE, units % SCALE);
            assert_eq!(parse_fixed(&text), Ok(units), "{text}");
        }
    }
}
