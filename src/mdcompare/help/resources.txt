# mdcompare resource evidence

Resource byte equality and OCR reuse are separate decisions. No URL is fetched by mdcompare. All local file references are resolved against the containing manifest and read through the bounded fixed-input reader.

## Manifest declarations

Each document declares `resources`, one object per logical resource. A declaration requires a unique, nonempty `resource_id`, `kind` (`image`, `attachment` or `media`) and the exact Markdown or HTML target in `url`. Stable resource IDs pair old and incoming resources even when the URL changes. Optional `path` names captured original resource bytes; it is not a prepared image or OCR artifact. Missing, duplicate, malformed or ambiguous declarations cannot establish equality.

```json
{
  "resource_id": "limits-diagram",
  "kind": "image",
  "url": "https://example.invalid/limits.png",
  "path": "captured/limits.png"
}
```

`path` is local evidence only. Capture and correspondence to the reference are the caller's responsibility; comparing a stale or incorrectly associated download cannot establish the current remote state. Supply immutable snapshots. The comparator does not identify the current remote object or authenticate capture claims.

Image occurrences, reference-style images, embedded HTML resources, recognized download links and declared resource links must map unambiguously to a declaration with matching target and kind. An empty list is not proof of no resources. Ordinary navigation links are compared as text and do not require downloads. Download links include common document/archive/image/media filename extensions, explicit HTML downloads and declarations supplied by the caller.

Resource extraction uses an internal LF parsing view for CommonMark CRLF and lone-CR line endings. Other Unicode separators remain ordinary characters, so they cannot manufacture a fenced-code boundary or hide a dependency. This does not normalize the independent text comparison or change its byte-equality result.

Only proven code is excluded from resource discovery: inline code requires exactly matching maximal backtick runs on one physical line, with escape parity respected for openers; top-level fences require valid opening info and a same-character closing run of at least the opening length, followed only by whitespace. A valid unclosed top-level fence consumes to EOF. Multiline spans, container-indented fences, and spans/fences in uncertain HTML-block context are conservatively left visible instead of being assumed code. Indentation alone is not used to exclude an image, because an indented list image may render normally. Even backslash runs preserve an active image marker; an odd escaped image marker may still leave an ordinary download link. Unsupported or ambiguous image syntax, unresolved image references, malformed embedded tags, CSS `url(...)`, unsupported resource-bearing HTML attributes, CSS style blocks and unsupported dependencies remain unknown. Ordinary HTML navigation and static tables do not require resource evidence. Unsupported `src`, `href`, background/style attributes, event handlers and embedded source documents are not silently treated as resource-free. This conservative extractor does not claim to implement every Markdown extension. Unknown extension text remains in the text comparison.

The explicit per-document limit is 10,000 resource declarations or occurrences. Exceeding it returns `RESOURCE_BUDGET_EXCEEDED`; file-byte limits also apply. Before code masking or resource regex matching, a cumulative per-document syntax work estimate sums each LF-separated line's character count times its opening-bracket, angle-bracket, backtick and tilde candidate count, with a limit of 20,000,000. This prevents malformed delimiters from bypassing the comparison budget through repeated scans; exceeding it also returns `RESOURCE_BUDGET_EXCEEDED`. No truncated resource view is used to declare equality.

## Immutable version evidence

Without local bytes, the narrow opt-in `s3-version-id-v1` contract recognizes a caller-captured, version-qualified AWS S3 object. It requires HTTPS virtual-hosted S3 URLs, a single non-null `versionId` query parameter and an `immutable` object:

```json
{
  "resource_id": "limits-diagram",
  "kind": "image",
  "url": "https://evidence.s3.amazonaws.com/limits.png?versionId=version-1",
  "immutable": {
    "contract": "s3-version-id-v1",
    "captured": true,
    "bucket": "evidence",
    "key": "limits.png",
    "version_id": "version-1"
  }
}
```

The caller explicitly attests that the captured evidence corresponds to the occurrence. The bucket, decoded object key and version ID must match the actual referenced URL exactly. Only equal identities prove equality; distinct versions without paired bytes stay unknown because two object versions might contain identical data. Mutable unqualified URLs, `null` versions, mismatched claims and unknown providers are rejected as proof. This route does not claim that a version is still available or that its OCR artifact is reusable. Supplied local reads take precedence; a read failure cannot be bypassed with version claims, and mixed one-sided byte evidence is not treated as a complete pair.

AWS documents that updates create distinct versions and a version-qualified GET selects a particular version; `null` versions are mutable when versioning is suspended. See [S3 versioning behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/versioning-workflows.html) and [retrieving object versions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/RetrievingObjectVersions.html). No AWS SDK or network request is used by this contract.

## Result and OCR extension contract

The resource dimension reports `status`, `reason_codes`, `items`, `processing_required` and `references_changed`. `items` is sorted by resource ID. Each item contains `resource_id`, `kind`, `status`, `basis`, `reason_codes`, and ordered zero-based `occurrences_before` / `occurrences_after`. Every occurrence is retained; equal resource bytes do not collapse repetitions or remove alt text, title, URL or order changes from the independent text comparison. `references_changed` compares the complete extracted use records, including locations and reference meaning, separately from byte equality.

Items use basis `local-bytes` or `s3-version-id-v1` only when supported evidence was used. Added resources are `NEW`; changed bytes are `CHANGED`; absent incoming resources have `operation: removed` and are excluded from `processing_required`. Confirmed removals do not require reading obsolete original-resource bytes; incoming raw references must still map completely. That list contains only incoming resource IDs whose bytes are known new or changed. An aggregate `UNKNOWN` preserves all known item changes and this processing list.

OCR verification uses item `resource_id` to resolve original declarations in each document. It must independently validate prepared JPEGs, preprocessing and OCR configuration, source correspondence, success and available artifacts. Original `path` alone is never enough. Additional OCR file references should use explicit `prepared_image`, `artifact`, `path` or `*_path` / `*_file` fields so input/output alias protection can inventory them even when a dependency is unreadable.

Code delimiter rules follow [CommonMark 0.31.2 code spans](https://spec.commonmark.org/0.31.2/#code-spans), [fenced code blocks](https://spec.commonmark.org/0.31.2/#fenced-code-blocks) and [backslash escapes](https://spec.commonmark.org/0.31.2/#backslash-escapes). The conservative unsupported cases may return UNKNOWN rather than silently omit a possible dependency.
