# Opt-in identity and metadata binding v2

Legacy `Main <root> --output <new-path>` still emits schema 2.0 / java-source-v1.
New `Main <root> --identity-v2 --output <new-path>` emits schema 3.0 /
java-source-v2, and refuses the repository's default index.json and existing outputs.
No old index or request is migrated in place.

The new identity is `java-source-v2:<owner>#<identity_signature>`. Parameter
erasure uses lexical method/class type-variable scopes, nearest declaration first;
bounds are resolved in their declaring scope, not in an inner shadowing scope.
Unbounded variables erase to java.lang.Object, intersection bounds use the leftmost
bound, and arrays/varargs retain all dimensions. Named types use source declarations,
explicit imports, known inherited source member types and unambiguous JDK imports.
JDK resolution uses the platform loader without initialization and records the
runtime in metadata. Unknown/ambiguous types and cyclic erasure fail closed.
This is a conservative source resolver, not a general external dependency symbol
solver or Java compiler. Unsupported external inherited member types stay unavailable.

Display `signature`, method name, original source parameters, seven retrieval
features and SourceMap behavior remain unchanged. A separate identity_signature
does not participate in scoring. Colliding erased declarations still invalidate
every member of their conflict group; no ordinal/path suffix is added.

V2 evidence IDs include the `index-schema-3.0` domain and v2 method identity in
Jackson's original array encoding. The index SHA in its sidecar binds the complete
index, avoiding a circular self-hash. V1 evidence IDs and validator are unchanged.

`metadata_experiment_v2.build_metadata_request` requires externally pinned index
and metadata hashes and the actual source root. It rechecks safe paths, source
hashes, UTF16 inclusive ranges, exact slice hashes, evidence containment and IDs.
An AVAILABLE position is not an evidence PASS. File blockers are kept separately;
opaque Unicode files can supply only producer-checked, escape-free method slices.
BOM, dangerous escapes, wrong ranges and wrong hashes remain closed. Every catalog
record is retained or the whole case is rejected; bodyless/unmapped methods are
not filtered. The source-binding report remains FAIL/NOT_INDEPENDENTLY_ATTESTED.
It is explicitly a v2 report, never a relabeled v1 Evidence Validator report.

Use `metadata_ranking_runner.rank_metadata_v2` with the independently pinned
source-binding request as reference. It shares the existing scorer and tie order;
legacy rank_metadata/bm25_baseline entry points retain v1 behavior. The pure
`brd_rerank_strategy_v2.build_pair` consumes a complete v2 BM25 ranking, selects the
unchanged first 20, and binds original metadata/source contexts. C and BRD differ
only at /messages/0/content. Frozen brd-v1 text, HTTP parameters and the exact
ranking/reason output schema stay unchanged. This module has no transport.

Old BRD/live entry points intentionally reject v2 identities. A future authorized
live task needs an explicitly versioned transport adapter and fresh authorization;
this patch grants no API execution. Changed producer/runner code requires new
code-hash bindings. Old manifests, source snapshots, inputs and protocol modules
remain intact; reproduce old byte-for-byte code hashes from their pinned revision,
never substitute v2 code or hashes into an old frozen experiment.
