# Runbook: Diagnose a book checksum failure, thin book depth, or precision loss

Covers the three ways the captured book or its numbers can be *wrong* while the
capture tier stays up: Kraken's CRC32 not matching, a book holding fewer levels than
the product promises, and a venue quoting finer than the fixed-point scale can carry.
For missing messages see [capture-sequence-gaps.md](./capture-sequence-gaps.md); for a
dead container see [capture-down.md](./capture-down.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Nothing on this page was measured by the 2026-08-26 chaos run**, and that is a
> stated gap rather than a pending one. The run
> ([`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv))
> injected five faults; none of them can produce a checksum mismatch, a depth
> degradation or a precision loss, because all three need chosen bytes pushed through
> the running binary. `capture-corrupt-frame.sh` printed SKIP for exactly this reason.
> Each cell below names the concrete thing that will fill it.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Kraken CRC32 book checksum mismatch | recovery automatic; **investigation < 30 min** | not yet verified — needs `k2-replay` (Phase G); `capture-corrupt-frame.sh` is a SKIP until then |
| 2 | Book depth degraded — fewer levels than top-20 | < 30 min to classify | not yet verified — needs a burn-in window's `k2_capture_book_levels_total` series (Phase F), not a fault injection |
| 3 | Precision loss — a value finer than 8 dp | **no restart; ADR required** | not yet verified — no injection possible; the counter's first tick in production is the measurement |

---

## 1. Kraken book checksum mismatch

**Symptom** — nothing visible in throughput. Book snapshots for one or more Kraken
symbols start carrying `checksum_ok = false`.

**Detection** — `CaptureChecksumFailure` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
increase(k2_capture_checksum_failures_total[10m]) > 0
```

Fires after `for: 5m`.

**Kraken only, by construction.** Kraken v2 publishes a CRC32 over the top 10 asks
then bids with every book update; Binance and Coinbase publish nothing comparable, so
their snapshots carry `checksum_ok = null` — "unanswerable", not "verified"
([ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md)). This alert can only fire
for `exchange="kraken"`; if it fires for another venue, the metric is mislabelled.

**Expected behaviour** — the policy is automatic and per symbol. On a mismatch the
adapter, in this order: emits **one** snapshot of the book as it actually stood,
stamped `checksum_ok = false`; drops that book; increments the counter; and
resubscribes **that symbol only**, leaving other symbols on the connection running.

The order matters and is the reason the marked snapshot exists at all. Dropping the
book first would leave nothing to emit — `snapshot()` returns `None` on an empty book —
so `checksum_ok` would in practice only ever be `true` or `null`, and a consumer
filtering `checksum_ok = false` to find bad windows would find nothing, ever. Between
the marked snapshot and the resync landing, that symbol emits **no** snapshots rather
than a plausible-looking lie. Pinned by
`services/capture-rust/src/exchanges/kraken.rs::a_bad_checksum_emits_a_marked_snapshot_then_resyncs`.

So the book fixes itself. What is not automatic is deciding which of two very
different things happened:

- **Transient** — a reordered or dropped update, self-corrected by the resync. One
  failure, no recurrence. Record and close.
- **Systematic** — the checksum *computation* is wrong, in which case some updates
  will pass and some will fail and the book is drifting undetected in between. Spike
  S1 established the mechanism: the checksum must be formatted from decimal strings or
  `i64` fixed-point units and **never** from `f64`, because an `f64` round-trip is
  lossy past 15 significant digits and desyncs the book *silently while the checksum
  still reports success*
  ([ADR-018 Appendix A](../adr/ADR-018-v3-lake-first-rust-capture.md#s1--kraken-v2-book-checksum)).

**Recovery**

```bash
# 1. Scope: how many, which symbols, and is it recurring or a one-off?   ✅ verified (v2)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=increase(k2_capture_checksum_failures_total[6h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.metric.symbol // "-") \(.value[1])"'

# 2. Did precision change under us? Kraken publishes price/qty precision on the
#    `instrument` channel at connect, and the checksum formatter is configured from
#    it (spike S2) - not from config/instruments.yaml.
docker logs k2-capture-kraken --tail 500 | grep -i -E 'instrument|precision|checksum'

# 3. What the emitted snapshots say
docker exec k2-redpanda rpk topic consume market.crypto.v3.book.kraken \
  --num 5 --offset end --use-schema-registry=value                      # ✅ verified (v2 topic)
#    expect checksum_ok true on healthy snapshots; exactly one false per mismatch,
#    then a gap in that symbol's snapshots until the resync lands

# 4. Correlate with resyncs and reconnects
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=increase(k2_capture_resyncs_total{exchange="kraken"}[6h])' | \
  jq -r '.data.result[] | .value[1]'
```

Classify:

| Pattern | Verdict | Action |
|---------|---------|--------|
| One `checksum_ok = false` snapshot, a short gap, then `true` again, no recurrence in 6 h | Transient | Record the window; close |
| Failures on **one** symbol, repeating | Suspect that symbol's precision — did `instrument` report a change? | Compare the logged precision against Kraken's current instrument data; if it changed, the formatter is right and the resubscribe picked it up |
| Failures across **many** symbols, repeating | Suspect the checksum computation | **Do not silence.** Run the S1 unit test (`cargo test checksum`) — it reproduces Kraken's published `3310070434` from the documented example. If that passes, the bug is in the live formatting path, not the algorithm |
| `checksum_ok = false` with no counter increment, or vice versa | Instrumentation bug | Open an issue; the metric and the field must agree |

**The narrow claim, stated so nobody over-reads it.** Kraken's checksum covers the top
**10** levels. A `checksum_ok = true` snapshot is verified for levels 1–10 and
unverified for 11–20; drift below level 10 is undetected by construction (ADR-027
Risks). Do not report "the book is verified" without that qualifier.

**Measured** — not yet, and **not by a chaos script either**.
`scripts/chaos/capture-corrupt-frame.sh` exits as a recorded SKIP on purpose: every
venue connection is TLS, so there is no seam to flip a byte in without terminating the
connection instead, and replaying chosen bytes through the running binary is precisely
what `k2-replay` (Phase G) is for. The script says so on every `make chaos` run rather
than quietly not covering the row. What stands in today is the unit test named above,
which proves the adapter on every commit but not the deployed container. Revisit when
`k2-replay` lands.

---

## 2. Book depth degraded

**Symptom** — the book holds fewer levels than the top-20 product promises; depth,
spread and imbalance queries return thinner results than expected.

**Detection** — `CaptureBookDepthDegraded` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
max_over_time(k2_capture_book_depth[10m]) < 20
```

Fires after `for: 10m`.

**Read the gauge before reading the number.** `k2_capture_book_depth` is **total
resting levels across both sides** — `book.rs` sets it to
`self.bids.len() + self.asks.len()` — so the threshold of 20 is 10 a side, against a
canonical top-20-*per-side* product (ADR-027). `max_over_time`, not `min_over_time`,
because a resync empties the book by design and one empty sample would hold a minimum
under the threshold for the ten minutes that follow.

**What this alert cannot see:** a deep book thinning without collapsing. Coinbase runs
full depth — 13,000-15,000 levels on liquid symbols, sampled over the 2026-08-26
12:38-13:07Z window — so a fall from 15,000 to 50 levels is a severe degradation that
never approaches this floor. Catching that needs a per-venue, per-symbol baseline;
Phase F's measured depth distributions in
[capacity-model.md](../architecture/capacity-model.md) are the trigger to revisit it.
Until then, step 3 below is the manual version of that check.

**Expected behaviour** — nothing self-heals, because this alert cannot tell on its own
whether anything is broken. `depth` is an emitted field precisely so a consumer can
distinguish *"the book only had 6 levels"* from *"we dropped 14"* (ADR-027) — and this
alert reports the number without making that call. **Two causes with opposite
responses:**

- **A genuinely thin instrument.** Correct data. The fix is an exclusion for that
  symbol, never a lower global threshold.
- **A book that thinned after a resync, a reconnect or an OOM.** A capture bug or a
  resource problem.

**Recovery**

```bash
# 1. Which symbols, and how thin                                       ✅ verified (v2)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=max_over_time(k2_capture_book_depth[1h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.metric.symbol) \(.value[1])"'

# 2. Did it thin at a resync/reconnect boundary? Same window on both.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=increase(k2_capture_resyncs_total[1h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'

# 3. Has it always been thin, or did it change? A symbol that has never held 20
#    levels is thin; one that used to is degraded. This is also the check the
#    alert cannot make for a deep venue: compare the week's peak with today's.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=max_over_time(k2_capture_book_depth[7d])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.metric.symbol) \(.value[1])"'

# 4. Coinbase only: the full-depth book is held in memory. Is it being trimmed
#    by memory pressure rather than by the market?
docker stats --no-stream k2-capture-coinbase
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_capture_book_levels_total' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1]) levels"'
```

- **Thin over 7 days** → the instrument is thin. Add a symbol exclusion to the alert,
  with a one-line comment saying which instrument and why, and leave the global
  threshold alone.
- **Thinned at a resync boundary** → the rebuild is not repopulating fully. Capture the
  logs and open an issue; this is a `book.rs` bug.
- **Coinbase near its memory limit** → see the bound below. `k2_capture_book_levels_total`
  approaching 80,000 is ADR-027's stated revisit trigger.

**The Coinbase memory bound, and why 512 MB.** Coinbase is the only venue where the
capture process holds a **complete** book — `level2` sends absolute quantities per
level with no top-N option, so top-20 is a truncation of a full `BTreeMap<i64,i64>`.
That map is sized from a measurement, not a guess: spike S5 saw the BTC-USD opening
snapshot at **5,195,904 bytes across 43,974 levels**, which is why the Coinbase
container gets 512 MB where Binance and Kraken get 256 MB
([ADR-018 Appendix A](../adr/ADR-018-v3-lake-first-rust-capture.md#s5--coinbase-level2-without-jwt)).
It is sized with headroom, not with proof — a market event that doubles resting depth
doubles the memory. An exit code `137` on `k2-capture-coinbase` is this bound being
hit; raise the limit in `docker-compose.yml` **and** update ADR-010's Outcome and the
budget comment in `docker-compose.yml`, as the project guardrails require.

**Measured** — not yet verified, and no chaos script can fill it: this is a steady-state
observation, not a fault. Phase F's burn-in samples `k2_capture_book_levels_total` per
exchange across the window and records the observed peak against the 512 MB limit in
[`capacity-model.md` §5](../architecture/capacity-model.md).

---

## 3. Precision loss

**Symptom** — none in throughput or availability. A counter moves.

**Detection** — `CapturePrecisionLoss` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
increase(k2_capture_precision_loss_total[1h]) > 0
```

Fires after `for: 5m`.

**Expected behaviour** — the value was **rejected, not rounded**, and the counter
incremented (ADR-020). A silently rounded price is a wrong price that looks right
forever and cannot be detected downstream; a rejected one plus a counter is a bug
someone fixes. That trade is the reason this alert exists.

The v3 wire contract carries prices and quantities as `int64` scaled by 1e-8, on the
assumption that no captured venue quotes finer than one satoshi — an assumption
grounded in measurement (spike S2 read `qty_precision 8`, `qty_increment 1e-08` off
Kraken's `instrument` channel), not in belief. **This counter is what keeps it an
assumption under observation.**

**Recovery — there is no restart for this one.**

```bash
# 1. Which exchange, and which field                                   ✅ verified (v2)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=increase(k2_capture_precision_loss_total[24h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.metric.field // "-") \(.value[1])"'

# 2. The offending values, as the exchange sent them. The raw topic holds the
#    verbatim frame, which is the whole point of keeping it.
docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.binance \
  --num 20 --offset end --use-schema-registry=value                    # ✅ verified (v2 topic)

# 3. Rate: a handful, or every message on one instrument?
docker logs k2-capture-binance --tail 500 | grep -i -E 'precision|reject'
```

Then:

- **A handful, one instrument** → a venue listed a new instrument with finer
  granularity. Confirm against the venue's instrument metadata. This is a **contract
  change**, and per ADR-020 it needs an ADR, not a patch: the scale is wrong for that
  venue and either the instrument is excluded or the contract moves.
- **Sustained across many instruments** → the scale is wrong for the venue outright.
  Same conclusion, larger blast radius. Every rejected record is a permanently missing
  row — record the window in the completeness audit while the decision is made.
- **Counter moving with no plausible source** → suspect the decimal parser, not the
  venue. `cargo test decimal` covers the conversion table.

**Do not "fix" this by rounding.** The rejection is the design. Widening the scale, or
accepting a rounded value, changes the meaning of every stored price and is an
ADR-level decision — [ADR-020](../adr/ADR-020-avro-fixed-point-contracts.md) names this
counter as its own revisit trigger.

**Measured** — not yet verified. This has no chaos script: it is induced by a unit
test over the decimal conversion table (`cargo test decimal`), and the counter's
behaviour in production is the measurement.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** 2026-08-26 (`make chaos`) — commands marked ✅ were run against the
running capture tier that day, and the chaos run confirmed that none of the three
failures on this page is injectable with the scripts that exist. No MTTR here is
measured. Re-stamp when `k2-replay` (Phase G) lands.
