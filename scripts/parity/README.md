# `scripts/parity/` — v2 / v3 trade parity

The evidence generator for retiring the Kotlin feed handlers.

[ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) makes parity a **gate, not a
report**: the Kotlin tier is the only capture implementation that has ever run in
production here, so retiring it removes the comparison baseline, and the comparison
has to be made before the baseline goes away. `compare_trades.py` is what makes that
comparison, and its markdown table is what gets pasted into the retirement PR.

**The retirement has landed** (2026-08-26, [ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md)
Accepted), so nothing produces `market.crypto.trades.<ex>` any more: a run over a
window *after* the retirement finds an empty v2 side and exits 1. A window that
predates it and is still inside Redpanda's retention still compares. The
2-hour labelled window's own tables are not reproduced here — they go in
ADR-019's Outcome, which carries a `«WINDOW3»` placeholder until they are pasted
in. Everything below is the tool and the method, not the evidence.

---

## Running it

```bash
uv run --no-project --with "confluent-kafka[avro]==2.15.0" \
  python scripts/parity/compare_trades.py \
    --exchange kraken \
    --window-start 2026-08-26T10:00:00Z \
    --window-end   2026-08-26T12:00:00Z
```

| flag | default | |
|---|---|---|
| `--exchange` | required | `binance` \| `kraken` \| `coinbase` |
| `--window-start` / `--window-end` | required | ISO 8601. A naive timestamp is read as UTC. |
| `--brokers` | `localhost:19092` | see *Reaching the broker* below |
| `--registry` | `http://localhost:8081` | |
| `--json` | off | machine-readable instead of markdown |
| `--v2-only` | off | consume only the v2 topic and print per-symbol counts. No verdict. |

Exit `0` = every symbol PASS. Exit `1` = any symbol FAIL, or the run could not be
completed (registry unreachable, empty window, unknown schema id, broker unreachable),
or the read was truncated. **`--v2-only` exits 1 on a truncated read too**: its counts
are a lower bound on the topic, so exiting 0 would report "the plumbing works over
this window" for a window it never finished reading.

`--v2-only` exists to prove the plumbing — Avro decode, `offsets_for_times`, the
window cut — before the Rust tier is producing anything worth comparing. Run it
first; if it prints sensible per-symbol counts, a red table later is a real finding
rather than a broken script. It reads the same frozen v2 topic, so the same window
constraint applies.

The script reads with a throwaway consumer group and `enable.auto.commit=false`, so
it cannot move a real consumer's offsets. It is safe to run against the live stack.

### Reaching the broker from the host

The defaults work as they stand. `docker-compose.yml:58` publishes the external
listener (`- "19092:19092"`) and `:67` advertises it as `localhost:19092`, so the
address inside the bootstrap metadata resolves from the host. Verified 2026-08-26:

```
$ docker port k2-redpanda | grep 19092
19092/tcp -> 0.0.0.0:19092

$ python3 -c "import socket; socket.create_connection(('127.0.0.1',19092),3)"
(no error — connection open)
```

Do **not** point `--brokers` at `localhost:9092`. That is the *internal* listener,
advertised as `redpanda:9092`: bootstrap metadata comes back, the broker address
inside it does not resolve on the host, and the consume fails after the connection
appears to succeed.

---

## How the window is chosen

**Two hours, labelled.** ADR-018 and the original Phase C plan both said 24 h; the
maintainer's decision of 2026-08-26 (Q6) set the burn-in and parity window at 2 h,
with the 24-hour continuous run kept as a Phase F+ revisit trigger. Both
[ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) and
[the Phase C plan](../../docs/plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md)
record it.

The window is a **closed interval on the Kafka record timestamp of each topic,
independently**. Pick it so that:

- both producers were up and healthy for the whole of it — a capture container that
  restarts mid-window shows up as a large one-sided deficit, which is the tool
  working correctly and is not parity evidence;
- it ends at least a minute in the past, so neither producer is still writing into it.
  **This is enforced, not advised**: `--window-end` less than 60 s old is a hard
  error. The two topics are read one after the other, so a window still being
  written into hands v3 every record produced during the v2 read and v2 none of
  them — a systematic one-sided deficit that is an artefact of this tool;
- it is inside the topics' retention (v2 topics are the live ones; v3 `trades.*` keep
  7 days).

The window and its length are printed in the header of every table, and the header
says *"a labelled sample, not a soak"* on purpose. A 2-hour window cannot observe
Binance's 24-hour connection-lifetime reconnect, an exchange maintenance window, or
any diurnal volume peak — ADR-019 states that consequence rather than leaving it to
be discovered later, and the table carries its window in the header for the same
reason.

---

## What PASS means — and what it does not

**PASS means:** over this window, for every canonical symbol, the two tiers agree on
how many **distinct trades** there were to within the stated tolerance, on which
trades they were, and — with no tolerance at all — on the price, quantity and **taker
side** of every trade both tiers saw, and on when it happened to within v2's
millisecond resolution.

### Counts are unique trade IDs, not records

Venues re-send trades, and a record count reads that as one tier inventing them.
Measured on the 2026-08-26T14:15Z→16:15Z window, Coinbase re-sent trade `69662829`
— exchange `time` `15:31:09.261488Z` — in raw frame `sequence_num` 1017853 at
`15:52:58.890Z`, 21 minutes stale, on the **same connection** (`conn_id`
`8e980ee6-…`, zero `k2_capture_reconnects_total`). Both tiers wrote it to their topic
again: `market.crypto.v3.trades.coinbase` p1@4345 and p1@4784/4785,
`market.crypto.trades.coinbase` p3@51157 and p3@52885. Over that window 2,533 v3 and
1,538 v2 Coinbase IDs arrived more than once, and every copy was byte-identical to
its first.

So the `v2` / `v3` / `Δ` columns count **unique `trade_id`s**. Repeat records get
their own `dup-v2` / `dup-v3` columns and are **never folded into Δ** — a re-send is
a fact about the feed, not a divergence between the tiers.

What *is* a divergence is a repeat that disagrees with its own first copy: same ID,
different price, quantity, side or timestamp means one tier mangled one of the
copies. That is counted separately and **fails the symbol**, with the symbol named
under the table. Deduplicating on the ID alone would have kept whichever copy
arrived first and printed a green row.

Side is in that list deliberately. Both tiers derive the Binance taker side by
inverting the same `is_buyer_maker` boolean (`TradeNormalizer.kt:27`,
`binance.rs:313`), and `trade.avsc` names that inversion as the hazard in the field's
own doc. A tier that flipped it would emit the same id, price, quantity and timestamp
for every trade — counts, the ID join and px/qty would all stay green. Side is the
only column that can see it, so it is compared at zero tolerance and the mismatch
column is named `px/qty/side mismatch`.

**PASS does not mean:**

- **Book parity.** v2 has no L2 book at all; there is nothing to compare it against.
  The book product is verified on its own terms — Kraken CRC32 `checksum_ok`,
  Coinbase `sequence_num` continuity, the `tests/replay.rs` fixtures — not here.
  Nothing in this directory says anything about the book.
- **Correctness of either tier.** It is an agreement test. Two tiers reading the same
  public feed and making the same mistake agree perfectly.
- **A soak.** See the window section. It is a sample.
- **Anything about `recv_ts_ns`, gaps, checksums or resyncs.** Those are v3-only
  properties with no v2 counterpart; they are gated by the capture tier's own metrics
  and alerts.
- **That either tier stayed connected for the whole window.** A tier that drops its
  WebSocket loses every trade until it is back, and that arrives as a one-sided
  `only-` deficit the tool cannot attribute to a cause. It is not a tool artefact —
  the trades really are missing from one topic — but it is a statement about
  *availability*, not about normalisation. **Read both tiers' logs before reading a
  red row as a defect.** On 2026-08-26T14:15Z→16:15Z the v2 Kotlin tier reconnected
  four times inside the window and v3 twice:

  | tier | exchange | lost at | back at | blind |
  |---|---|---|---|---:|
  | v2 | coinbase | 15:02:13.728 | 15:02:20.174 | 6.4 s |
  | v2 | kraken | 14:54:12.117 | 14:54:19.064 | 6.9 s |
  | v2 | kraken | 15:43:59.701 | 15:44:06.209 | 6.5 s |
  | v2 | kraken | 16:11:07.403 | 16:11:13.894 | 6.5 s |
  | v3 | kraken | 15:01:55.617 | 15:01:57.576 | 2.0 s |
  | v3 | kraken | 15:55:48.305 | 15:55:50.287 | 2.0 s |

  `docker logs k2-feed-handler-{coinbase,kraken}` and `docker logs k2-capture-kraken`.
  Every one of those windows is legible in the table: 415 of Coinbase's 451 `only-v3`
  IDs carry an exchange timestamp inside minute `15:02`, and Kraken's `only-v3`
  clusters at `14:54`, `15:44` and `16:11` — not at v3's `15:01` or `15:55`, where
  the two-second v3 gaps show up as the `only-v2` counts of 1–2 instead. v2's fixed
  5-second reconnect backoff is why its gaps are ~3× v3's.

### The tolerance, and why it is not zero

Per symbol: `max(2, 0.1% of count)` on the count delta, and on each side's
only-in-this-tier count **on the ID-join path** (binance, coinbase).
`px/qty/side mismatch` has **no** tolerance and must be 0. Neither does the
`exchange_ts` delta on matched IDs: v2 floors to milliseconds
(`Instant.parse(time).toEpochMilli()`, `TradeNormalizer.kt:117`) while v3 stores
microseconds, so a matched pair cannot legitimately differ by a full millisecond —
1,000 µs or more fails the symbol rather than being printed and waved away.

**Kraken is the exception, and it is a real one** — see the carve-out below.

The window is cut on each topic's own record timestamps, and the two producers stamp
a trade at different points in their pipelines. A trade landing at 11:59:59.998 on one
topic can land at 12:00:00.001 on the other, so the two windows do not contain
literally the same trades at the edges. Two edges × three exchanges makes a handful of
boundary trades expected and meaningless. The floor of 2 absorbs that on a quiet
symbol; the 0.1% scales it on a busy one; neither is large enough to hide a producer
that is actually dropping messages.

**The floor of 2 was a prediction; it has now been measured once.** `EDGE_ALLOWANCE
= 2` says a window edge can move at most one trade per edge per symbol. First
full-window check, 2026-08-26T14:15Z→16:15Z: Binance came back **12/12 symbols with
`only-v2` and `only-v3` both exactly 0** over 1,569,505 records on each side —
1.57 M trades and not one landed on opposite sides of a boundary. The edges moved
nothing at all, so on that evidence the floor of 2 is loose rather than tight.

That is one window on one venue, and it is the venue that stamps `exchange_ts` from
its own `E`/`T` fields on both tiers. Kraken and Coinbase could not test it: both had
a tier reconnect inside the window (see the availability table above), which swamps
any edge effect. **Revisit** when a window runs with zero reconnects on both tiers
for all three exchanges — if the edges still move nothing there, this constant can go
to 0 and the tool gets strictly sharper. If they routinely move more than 2, the fix
is this constant with the measurement written next to it, not a wider proportional
tolerance, which would also loosen the busy symbols where the floor never binds.

One thing the tolerance explicitly does **not** cover: a symbol that one tier saw and
the other did not see at all. That is never a window edge, so a symbol with a zero on
one side fails regardless of how small the counts are. Without that guard a symbol
with two trades in the window would sit on the floor of 2 and "pass" against a v3 tier
that was not running.

### Kraken is compared differently, on purpose

v2's Kraken trade IDs are **synthesised, not real**:
`"KRAKEN-${timestampMs}-${pair.hashCode()}"` (ADR-018 gap 5, ADR-019 *Consequences*).
Two trades in the same millisecond on the same pair get the same ID by construction,
so joining them against v3's real integer `trade_id` is not merely unavailable — it
would be meaningless, and a green result from it would be a lie. So for Kraken:

- the ID comparison is **skipped**, and the `px/qty/side mismatch` column prints `n/a`;
- the two sides are compared as a **multiset of `(price, qty, side, exchange_ts)`**,
  with `only-v2` / `only-v3` becoming multiset differences;
- the timestamp in that key is truncated to **milliseconds**, because v2 cannot
  represent anything finer — the v2 schema stores millis and Kraken publishes micros.
  Comparing at microsecond granularity would fail on every trade for a reason that has
  nothing to do with parity.

#### Only the v3 side can be deduplicated

The unique-ID rule above cuts one way on Kraken. v3 carries the venue's own integer
`trade_id`, so the v3 side **is** reduced to unique IDs before the multiset is built:
a replay on resubscribe would otherwise arrive as a v3 surplus and read as v2
dropping trades. The v2 side is **not**, and cannot be — `KRAKEN-<ms>-<hash>` repeats
are *different* trades, so deduplicating them would delete real ones. Measured
2026-08-26 over 2 h, BTC/USD: **9,093 v2 records carrying 3,923 distinct synthesised
IDs**; a dedupe would have thrown away 57% of the symbol.

So on the Kraken row, `v2` counts records, `v3` counts unique trades, and `dup-v2`
reports **ID collisions** rather than repeat records. The rendered header says so.
`dup-v3` there is a v3 replay detector: it read **0 across all 29,167 records** over
that window, spanning two involuntary reconnects (`15:01:55Z`, `15:55:48Z`), which is
the evidence that `"snapshot": false` on the `trade` subscription
(`services/capture-rust/src/exchanges/kraken.rs:176`) does what it says.

#### The carve-out this costs

With no ID join there is no `px/qty/side mismatch` column for Kraken, so a diverging
price, quantity or side has nowhere to appear except as one `only-v2` plus one
`only-v3` — arithmetically identical to a trade that fell off one edge of the window
and another that fell off the other. **The zero-tolerance guarantee above therefore
does not hold for Kraken**, and pretending otherwise is what a green table would be
hiding.

What it is gated at instead: Kraken's multiset differences are capped at the constant
**edge allowance of 2**, never at the `0.1%` slope. Window-edge effects are bounded by
how many trades land within the two tiers' stamping skew of the two boundaries — a
small constant — not by a fraction of the symbol's volume. Without that cap, 150
completely wrong prices on a 150,000-trade symbol sit inside 0.1% and PASS. With it,
three do not. One or two still do, and that is the residual: on Kraken, a divergence
of one or two trades is not distinguishable from a boundary effect by this tool.

The rendered table states this in its own header, not only here — the artefact that
gets pasted into the PR has to carry its own caveat.

This is exactly the exception ADR-019 wrote down in advance: *"Kraken parity is
asserted on counts and on v2's real integer `trade_id` being present and unique — not
on ID equality with a v1 identifier that was never real."*

### Known expected divergence: `XDG/USD` vs `DOGE/USD`

v2's Kotlin normaliser predates `config/instruments.yaml` and emits Kraken's native
`XDG/USD` as the canonical symbol; v3 resolves it through the registry to `DOGE/USD`.
The comparison keys on canonical symbol, so this shows up as one all-`only-v2` row and
one all-`only-v3` row. **This is a real v2 bug being surfaced, not noise** — it is the
one `config/instruments.yaml` names in its own header comment ("guessing is what
produced `XDG/USD` and `DOGE/USD` as two different instruments in v2"). The script
does not fold the two together: hiding a divergence to make a table green is the
opposite of what this directory is for. Explain it in the PR next to the table.

Since the retirement, `config/instruments.yaml` carries Kraken's WS v2 spellings
as the natives too (`BTC/USD`, `DOGE/USD`), so nothing aliases `XDG/USD`
anywhere — the divergence recorded here is what that change was made to end.

Observed on the 2-hour labelled window, 2026-08-26T14:15Z → 16:15Z:

| symbol | v2 | v3 | Δ | dup-v2 | dup-v3 | only-v2 | only-v3 | px/qty/side mismatch | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| DOGE/USD | 0 | 1,322 | -1,322 | 0 | 0 | 0 | 1,322 | n/a | **FAIL** |
| XDG/USD | 1,320 | 0 | +1,320 | 593 | 0 | 1,320 | 0 | n/a | **FAIL** |

The same ~1,321 Dogecoin trades under two canonical names. The 2-trade difference is
v2's `14:54` reconnect, and `dup-v2 = 593` is the synthesised-ID collision on the v2
side of it — neither is a second bug.

---

## Pasting it into the retirement PR

Run it once per exchange over the same labelled window and paste all three tables
under a heading that names the window:

```bash
for ex in binance kraken coinbase; do
  python scripts/parity/compare_trades.py --exchange "$ex" \
    --window-start 2026-08-26T10:00:00Z --window-end 2026-08-26T12:00:00Z
done
```

The output is already markdown; it does not need reformatting. The PR needs, next to
the tables:

- the window, stated as a **2-hour labelled sample** (the header line carries it);
- an explanation for **every** non-zero `only-v2` / `only-v3` — ADR-019's trigger is
  *"any divergence explained rather than tolerated"*, and a number inside the
  tolerance still wants a sentence;
- the Kraken `n/a` column explained by the synthesised-ID paragraph above, **and**
  its carve-out stated — a Kraken table cannot assert the zero-tolerance px/qty/side
  guarantee that binance and coinbase tables can;
- **both tiers' reconnect logs for the window.** A red row caused by a tier being
  disconnected is a different finding from a red row caused by a tier mis-normalising,
  and the table cannot tell them apart. `docker logs k2-feed-handler-<ex> | grep -i
  reconnect` and `docker logs k2-capture-<ex> | grep -i reconnect`. If either tier
  reconnected, say so, or run a cleaner window;
- a link back to this README for what PASS does not mean.

If the tables are green, the ADR's retirement trigger is met and
`git mv services/feed-handler-kotlin legacy/v2-kotlin/` can land in the same PR. If
any are red, ADR-019 is explicit: *"Kotlin stays until it does."*

That `git mv` landed on 2026-08-26 and the handlers are now at
[`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md). The tables it landed
against belong in ADR-019's Outcome, not here — that section holds a `«WINDOW3»`
placeholder until they are pasted in, and this line is not evidence that they
were green.

---

## Tests

`tests/test_parity.py` covers the normalisation and verdict logic with hand-built
records — no Kafka, no registry, no running stack. Run with the repo's usual
invocation:

```bash
uv run --no-project --with pytest --with prefect pytest tests/test_parity.py -q
```

It is picked up by `make test-python` along with the rest of `tests/`.
