# mdcompare input stability and report publication

Callers must provide immutable baseline and incoming snapshots, including local
resources, prepared images, OCR blocks and provenance files. mdcompare does not
advance a baseline, download resources or mutate these inputs.

Each document comparison uses one cached byte view of each file it reads. The
cache is released between documents to avoid retaining the full corpus in memory.
File identity, size and nanosecond modification/change timestamps are checked
before and after reads, on repeated reads and again across the full comparison.
Observed changes produce UNKNOWN and INPUT_CHANGED for affected documents;
a changed manifest is a global MANIFEST_CHANGED failure because identity pairing
can no longer be trusted. These metadata checks detect ordinary changes; they
cannot defeat arbitrary concurrent rewrites. Snapshot immutability remains the
caller's responsibility.

Only regular input files are read. --max-file-bytes bounds each document or
evidence read (default 16 MiB); oversize inputs produce UNKNOWN with
FILE_BUDGET_EXCEEDED rather than comparing a truncated prefix. Manifest parsing
has its own fixed 16 MiB input limit, with global failure for unreadable, unstable
or invalid manifests. Explicit duplicate-JSON-field, read-budget and observed
read-time drift codes are preserved rather than flattened into a generic parse
error; these remain global exit-1 failures with no input values or paths logged.
Reading or comparing one document unsuccessfully does not
remove it from the report or discard other documents' results. A later resource
or OCR read failure does not overwrite an already determined text dimension.

Report paths must not alias each other, the manifests, Markdown inputs or any
declared resource/OCR/provenance file. Protection covers resolved paths, symlinks
and existing hardlinks, including declared evidence that was never read. File
reference fields use path, file, artifact, prepared_image or a name ending in
_path or _file; unsupported evidence cannot authorize a skip.

All requested reports are staged privately before any final path is replaced.
Each file is published by atomic replacement with mode 0600. Caught replacement
failures restore earlier reports from temporary backups and remove newly
published files. Temporary staging files are cleaned up. Separate final paths
are not a crash-proof multi-file filesystem transaction; supply a single JSON
report as the authoritative automation result and check the process exit code.

The optional human report includes identity, action, reason codes and dimension
states. --include-evidence permits bounded text differences in both JSON and the
human report. Ordinary stdout/stderr contain no body evidence, private file
paths, credentials or complete resource URLs.
