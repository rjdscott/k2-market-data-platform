# Fixtures: synthetic ISO 4217 list

`iso4217-synthetic.xml` is synthetic: the structure of the ISO 4217 "list one" XML
(`ISO_4217` / `CcyTbl` / `CcyNtry`) with invented countries. The currency codes, numeric
codes and minor units are the real identifiers (codes are identifiers, not data); nothing
else is copied. No third-party data is committed to this repo. SIX Group, the ISO 4217
maintenance agency, publishes the real list.

It holds one code shared by two countries (EUR), one territory with no currency, one
"N.A." minor unit (XDR), one fund (CLF) and one numeric code with leading zeros (ALL, 008).
