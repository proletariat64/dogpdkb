# Historical enriched Markdown

`mdcompare compare` reports differences involving enriched snapshots as
observed changes, not verified differences between raw sources. These rows
have text status `UNKNOWN` and reason `HISTORICAL_RAW_UNAVAILABLE`. Submit
both original Markdown files as `raw` for strict raw-to-raw comparison.
The retired v1 OCR reconstruction format is not used.

`mdcompare knowledge` and `mdcompare semantic` independently compare the
authored body and exclude marked generated OCR content. This does not
reconstruct original Markdown or verify old OCR evidence.
