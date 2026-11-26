# Golden files: generated test expectations, synthetic by construction

Each emitter's expected output for the good fixture set (`../fixtures/v2/good/`, every value in
it invented), compared by that emitter's test. Nothing here is data from a source: every file is
what k2gen compiles from those synthetic documents. This first line is the marker gate (i) of
`make check-docs` requires before it reads any file below it as a test expectation rather than a
data file (`scripts/check_docs.py`, `GOLDEN_MARK`); binary files and images still fail there.

- One directory per emitter module, `<emitter>/`, written only by the step that owns the module
  (`platform/k2gen/INTERFACES.md`), holding each file at its release-relative path:
  `golden/lake/compiled/lake/storage-plan.json` is what `emit/lake.py` returns for
  `compiled/lake/storage-plan.json`.
- A file is compared byte for byte, except one whose emitted form is a list of records to a
  reader, Trino's `groups.txt`: its golden is `trino_rules/groups.yaml`, the group-to-members map
  the test parses the emitted file into.
- A step regenerates its own directory only, and reviews the diff as it would code.
