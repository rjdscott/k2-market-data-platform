# `scripts/parity/` — v2 / v3 trade parity

The evidence generator for retiring the Kotlin feed handlers.

[ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) makes parity a **gate, not a
report**: the Kotlin tier is the only capture implementation that has ever run in
production here, so retiring it removes the comparison baseline, and the comparison
has to be made before the baseline goes away. `compare_trades.py` is what makes that
comparison, and its markdown table is what gets pasted into the retirement PR.

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
completed (registry unreachable, empty window, unknown schema id, broker unreachable).

`--v2-only` exists to prove the plumbing — Avro decode, `offsets_for_times`, the
window cut — before the Rust tier is producing anything worth comparing. Run it
first; if it prints sensible per-symbol counts, a red table later is a real finding
rather than a broken script.

The script reads with a throwaway consumer group and `enable.auto.commit=false`, so
it cannot move a real consumer's offsets. It is safe to run against the live stack.

### Reaching the broker from the host

**The default `localhost:19092` does not currently work from the host, and that is a
`docker-compose.yml` gap, not a bug in this script.** Redpanda is started with an
external listener on `19092` advertised as `localhost:19092`, but only `9092` (the
*internal* listener, advertised as `redpanda:9092`) appears in the service's `ports:`
list. Verified 2026-08-26:

```
$ python3 -c "import socket; socket.create_connection(('127.0.0.1',19092),2)"
ConnectionRefusedError: [Errno 111] Connection refused

$ # against localhost:9092 instead — bootstrap succeeds, the broker does not
%3|FAIL|rdkafka#producer-1| [thrd:redpanda:9092/0]: Failed to resolve 'redpanda:9092'
```

Bootstrap metadata comes back fine on `9092`; the *broker address inside it* is
`redpanda:9092`, which does not resolve on the host, so the consume then fails. Two
ways round it, in preference order:

1. **Publish the external listener.** Add `- "19092:19092"` to the `redpanda`
   service's `ports:` in `docker-compose.yml`. The listener already exists and is
   already advertised as `localhost:19092`; nothing else changes. This is the fix,
   and it is why the default here is `19092` rather than `9092`.
2. **Run the script inside the compose network**, which is how the numbers below were
   produced:

   ```bash
   docker run --rm -v "$PWD":/w -w /w \
     --network k2-market-data-platform_k2-net python:3.12-slim \
     sh -c "pip install -q 'confluent-kafka[avro]==2.15.0' && \
            python scripts/parity/compare_trades.py --exchange kraken \
              --brokers redpanda:9092 --registry http://redpanda:8081 \
              --window-start ... --window-end ..."
   ```

Adding `redpanda` to `/etc/hosts` also works and is not recommended: it makes the
script's behaviour depend on machine-local state that nothing in the repo declares.

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
- it ends at least a minute in the past, so neither producer is still writing into it;
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
how many trades there were to within the stated tolerance, on which trades they were,
and — with no tolerance at all — on the price and quantity of every trade both tiers
saw.

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

### The tolerance, and why it is not zero

Per symbol: `max(2, 0.1% of count)` on the count delta and on each side's
only-in-this-tier count. `px/qty mismatch` has **no** tolerance and must be 0.

The window is cut on each topic's own record timestamps, and the two producers stamp
a trade at different points in their pipelines. A trade landing at 11:59:59.998 on one
topic can land at 12:00:00.001 on the other, so the two windows do not contain
literally the same trades at the edges. Two edges × three exchanges makes a handful of
boundary trades expected and meaningless. The floor of 2 absorbs that on a quiet
symbol; the 0.1% scales it on a busy one; neither is large enough to hide a producer
that is actually dropping messages.

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

- the ID comparison is **skipped**, and the `px/qty mismatch` column prints `n/a`;
- the two sides are compared as a **multiset of `(price, qty, exchange_ts)`**, with
  `only-v2` / `only-v3` becoming multiset differences;
- the timestamp in that key is truncated to **milliseconds**, because v2 cannot
  represent anything finer — the v2 schema stores millis and Kraken publishes micros.
  Comparing at microsecond granularity would fail on every trade for a reason that has
  nothing to do with parity.

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

Observed on a 2-minute two-sided Kraken window, 2026-08-26T12:20:59Z → 12:22:59Z,
with both tiers up for the whole of it (214 records consumed on each side):

| symbol | v2 | v3 | Δ | only-v2 | only-v3 | px/qty mismatch | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| ADA/USD | 28 | 28 | +0 | 0 | 0 | n/a | PASS |
| BTC/USD | 98 | 98 | +0 | 0 | 0 | n/a | PASS |
| DOGE/USD | 0 | 4 | -4 | 0 | 4 | n/a | **FAIL** |
| XDG/USD | 4 | 0 | +4 | 4 | 0 | n/a | **FAIL** |
| … | | | | | | | |

Eight of ten symbols matched with `only-v2` and `only-v3` both exactly 0 — the
multiset path agreeing trade-for-trade, not merely on counts. The two red rows are
the same four Dogecoin trades counted under two different canonical names. **This is
a 2-minute sample used to validate the tool, not parity evidence** — the retirement
PR needs the 2-hour labelled window.

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
- the Kraken `n/a` column explained by the synthesised-ID paragraph above;
- a link back to this README for what PASS does not mean.

If the tables are green, the ADR's retirement trigger is met and
`git mv services/feed-handler-kotlin legacy/v2-kotlin/` can land in the same PR. If
any are red, ADR-019 is explicit: *"Kotlin stays until it does."*

---

## Tests

`tests/test_parity.py` covers the normalisation and verdict logic with hand-built
records — no Kafka, no registry, no running stack. Run with the repo's usual
invocation:

```bash
uv run --no-project --with pytest --with prefect pytest tests/test_parity.py -q
```

It is picked up by `make test-python` along with the rest of `tests/`.
