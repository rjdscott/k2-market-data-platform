# Fixtures: synthetic Kraken AssetPairs response

`asset-pairs-synthetic.json` is hand-written and synthetic: the shape of the response of Kraken's
public `AssetPairs` REST endpoint as its public documentation describes it (`error`, and `result`
mapping each pair code to its attributes), with invented assets (ALPHA, BRAV, CHARLIE) and round
numbers. The quote codes USD, EUR and USDT, and the venue's Z prefix on fiat codes, are spellings,
not data. Nothing is copied from a real response. No third-party data is committed to this repo.

It holds a pair whose code equals its REST alternative name (ALPHAUSD), one whose code carries the
venue's asset prefixes (XBRAVZEUR, base XBRAV, quote ZEUR) and is `cancel_only`, and one without
leverage or position limits (CHARLIEUSDT).

XXBTZUSD is the one pair named for a real asset, with invented attributes and round numbers: its
websocket name XBT/USD carries the venue's older code for bitcoin, which the security master's
seeds/code_exceptions.csv maps to BTC, so its venue spelling is BTC/USD, the symbol the trade
frames of services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl carry. The run path
(scripts/verify/run-path.sh) lands this response and resolves those trades through it.
