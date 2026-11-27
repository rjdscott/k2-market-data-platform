# Fixtures: synthetic ECB reference rates

Every file here is synthetic: the exact format of the ECB euro foreign exchange reference
rates 90-day file (`eurofxref-hist-90d.xml`) with invented, round values and dates in 2030.
No third-party data is committed to this repo. The ECB publishes the real file.

- `ecb-90d-synthetic.xml`: three dates, five currencies, good.
- `ecb-90d-synthetic-corrupt.xml`: one negative rate and one date far in the future, so the
  `rate_positive` and `no_future_date` checks have something to fail. It parses.
- `ecb-90d-synthetic-truncated.xml`: cut mid-document, the parse-failure path.
