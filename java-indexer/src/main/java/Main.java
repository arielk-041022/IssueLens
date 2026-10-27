import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.github.javaparser.*;
import com.github.javaparser.ast.*;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.type.Type;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/** Incremental source index. PARSED is syntactic success, never verification. */
public class Main {
    private static final Set<String> SPRING_MAPPING_ANNOTATIONS = Set.of(
            "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping");
    static final ObjectMapper JSON = JsonMapper.builder()
            .enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
            .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS)
            .enable(SerializationFeature.INDENT_OUTPUT).build();

    public static void main(String[] args) throws IOException {
        if (args.length == 4 && args[1].equals("--identity-v2") && args[2].equals("--output")) {
            Path root = Paths.get(args[0]).toRealPath();
            Path output = Paths.get(args[3]).toAbsolutePath().normalize();
            if (output.equals(root.resolve("index.json"))) throw new IOException("v2 requires a new explicit output");
            requireNewPair(output);
            writeIndex(MethodIdentityV2.index(root), output);
            System.out.println("INDEX_V3_PAIR_WRITTEN " + output);
            return;
        }
        if (args.length == 0) { printUsage(); return; }
        if (args.length != 1 && !(args.length == 3 && args[1].equals("--output"))) {
            throw new IllegalArgumentException("Usage: Main <projectRoot> [--output <newIndexPath>]");
        }
        Path root = Paths.get(args[0]).toRealPath();
        Path output = args.length == 3 ? Paths.get(args[2]).toAbsolutePath().normalize() : root.resolve("index.json");
        if (args.length == 3 && (output.equals(root.resolve("index.json"))
                || (Files.exists(output) && Files.exists(root.resolve("index.json"))
                && Files.isSameFile(output, root.resolve("index.json"))))) {
            throw new IOException("Explicit output must not be the repository index.json");
        }
        requireNewPair(output);
        IndexResult result = indexProject(root);
        writeIndex(result, output);
        System.out.println("INDEX_PAIR_WRITTEN " + output);
        System.out.println(JSON.writeValueAsString(result.metadata));
    }

    static JavaParser parser() {
        return new JavaParser(new ParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.BLEEDING_EDGE)
                .setTabSize(1).setStoreTokens(true).setPreprocessUnicodeEscapes(false));
    }

    static IndexResult indexProject(Path projectRoot) throws IOException {
        Path root = projectRoot.toRealPath();
        Path sourceRoot = root.resolve("src/main/java");
        if (!Files.isDirectory(sourceRoot)) throw new IOException("Missing src/main/java: " + root);
        IndexResult result = new IndexResult();
        JavaParser parser = parser();
        try (Stream<Path> paths = Files.walk(sourceRoot)) {
            for (Path file : paths.filter(Files::isRegularFile)
                    .filter(p -> p.toString().endsWith(".java")).sorted().toList()) {
                if (!file.toRealPath().startsWith(root)) throw new IOException("Source outside repository: " + file);
                result.files.add(parseJavaFile(parser, root, file));
            }
        }
        detectIdentityConflicts(result);
        for (FileInfo f : result.files) {
            result.metadata.parse_counts.merge(f.parse_status, 1, Integer::sum);
            for (ClassInfo c : f.classes) for (MethodInfo m : c.methods) {
                result.metadata.methods_total++;
                if (!m.identity_status.equals("AVAILABLE")) result.metadata.identity_unavailable_methods++;
                if (!m.location_status.equals("AVAILABLE")) result.metadata.location_unavailable_methods++;
                result.metadata.evidence_occurrences += m.evidence_occurrences.size();
            }
        }
        result.metadata.files_total = result.files.size();
        return result;
    }

    static FileInfo parseJavaFile(JavaParser parser, Path root, Path javaFile) {
        FileInfo info = new FileInfo();
        info.file = root.relativize(javaFile).normalize().toString().replace('\\', '/');
        String source;
        try {
            byte[] bytes = Files.readAllBytes(javaFile);
            info.source_sha256 = sha256(bytes);
            source = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
        } catch (IOException e) {
            info.parse_status = "READ_ERROR";
            info.parse_problems.add(e.getClass().getSimpleName() + ": " + e.getMessage());
            return info;
        }
        ParseResult<CompilationUnit> parsed;
        try { parsed = parser.parse(source); }
        catch (RuntimeException e) {
            info.parse_status = "FAILED";
            info.parse_problems.add(e.getClass().getSimpleName() + ": " + e.getMessage());
            return info;
        }
        parsed.getProblems().forEach(p -> info.parse_problems.add(p.toString()));
        info.parse_status = parsed.getResult().isEmpty() ? "FAILED"
                : parsed.isSuccessful() ? "PARSED" : "PARTIAL";
        if (parsed.getResult().isEmpty()) return info;
        CompilationUnit cu = parsed.getResult().get();
        info.packageName = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
        SourceMap map = new SourceMap(source, cu);
        if (!map.reliable || map.unescapedSlicesOnly) info.limitations.add(map.reason);
        if (map.unescapedSlicesOnly) info.limitations.add("UNICODE_ESCAPE_SLICES_UNSUPPORTED");
        if (!cu.findAll(EnumDeclaration.class).isEmpty() || !cu.findAll(RecordDeclaration.class).isEmpty()
                || !cu.findAll(AnnotationDeclaration.class).isEmpty()) {
            info.limitations.add("ENUM_RECORD_ANNOTATION_DECLARATIONS_NOT_INDEXED_AS_CLASSES");
        }
        if (cu.findAll(ObjectCreationExpr.class).stream().anyMatch(x -> x.getAnonymousClassBody().isPresent())) {
            info.limitations.add("ANONYMOUS_CLASS_METHODS_NOT_INDEXED");
        }
        for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            ClassInfo ci = new ClassInfo();
            ci.name = cls.getNameAsString();
            for (MethodDeclaration method : cls.getMethods()) ci.methods.add(parseMethod(method, cls, info, map));
            info.classes.add(ci);
        }
        return info;
    }

    static MethodInfo parseMethod(MethodDeclaration method, ClassOrInterfaceDeclaration owner,
                                 FileInfo file, SourceMap map) {
        MethodInfo m = new MethodInfo();
        m.name = method.getNameAsString();
        // Preserve v1 semantics: annotation begin line, -1 when no range exists.
        m.line = method.getBegin().map(p -> p.line).orElse(-1);
        m.has_body = method.getBody().isPresent();
        m.signature = m.name + "(" + String.join(",", method.getParameters().stream().map(p -> {
            Type type = p.getType().clone();
            type.findAll(AnnotationExpr.class).forEach(Node::remove);
            type.walk(Node::removeComment);
            return type.toString().replaceAll("\\s+", "") + (p.isVarArgs() ? "[]" : "");
        }).toList()) + ")";
        List<String> typeNames = new ArrayList<>();
        boolean unsupportedOwner = false;
        for (Node node = owner; node != null; node = node.getParentNode().orElse(null)) {
            if (node instanceof TypeDeclaration<?> t) typeNames.add(t.getNameAsString());
            if (node instanceof CallableDeclaration<?> || node instanceof ObjectCreationExpr
                    || node instanceof EnumConstantDeclaration || node instanceof InitializerDeclaration) unsupportedOwner = true;
        }
        Collections.reverse(typeNames);
        m.qualified_class_name = (file.packageName.isEmpty() ? "" : file.packageName + ".") + String.join(".", typeNames);
        m.identity_status = !file.parse_status.equals("PARSED") ? "UNAVAILABLE_PARSE"
                : unsupportedOwner ? "UNSUPPORTED_LOCAL_TYPE" : "AVAILABLE";
        if (unsupportedOwner && !file.limitations.contains("LOCAL_TYPE_IDENTITY_UNAVAILABLE")) {
            file.limitations.add("LOCAL_TYPE_IDENTITY_UNAVAILABLE");
        }
        if (m.identity_status.equals("AVAILABLE")) m.method_id = "java-source-v1:" + m.qualified_class_name + "#" + m.signature;
        String methodSlice = file.parse_status.equals("PARSED") ? map.slice(method) : null;
        m.location_status = !file.parse_status.equals("PARSED") ? "UNAVAILABLE_PARSE"
                : methodSlice == null ? "UNAVAILABLE_MAPPING" : "AVAILABLE";
        if (methodSlice != null) {
            Range r = map.range(method);
            m.start_line = r.begin.line; m.start_column = r.begin.column;
            m.end_line = r.end.line; m.end_column = r.end.column;
        }
        // Retain v1 normalization and recursive traversal in the old fields.
        for (AnnotationExpr a : method.getAnnotations()) {
            if (SPRING_MAPPING_ANNOTATIONS.contains(a.getName().getIdentifier())) m.annotations.add(a.toString());
        }
        method.getBody().ifPresent(body -> {
            Set<String> strings = new LinkedHashSet<>();
            for (StringLiteralExpr s : body.findAll(StringLiteralExpr.class)) strings.add(s.asString());
            m.strings.addAll(strings);
            Set<String> calls = new LinkedHashSet<>();
            for (MethodCallExpr c : body.findAll(MethodCallExpr.class)) calls.add(c.getNameAsString());
            m.calls.addAll(calls);
        });
        if (m.method_id != null && methodSlice != null) {
            List<Node> nodes = new ArrayList<>(method.getAnnotations());
            method.getParameters().forEach(p -> nodes.addAll(p.getAnnotations()));
            method.getBody().ifPresent(body -> {
                nodes.addAll(body.findAll(StringLiteralExpr.class));
                nodes.addAll(body.findAll(MethodCallExpr.class));
            });
            for (Node n : nodes) {
                if (!belongsTo(n, method)) continue;
                String snippet = map.slice(n);
                if (snippet == null) { m.evidence_omissions++; continue; }
                Range r = map.range(n);
                EvidenceOccurrence e = new EvidenceOccurrence();
                e.evidence_type = n instanceof AnnotationExpr ? "ANNOTATION"
                        : n instanceof StringLiteralExpr ? "STRING_LITERAL" : "METHOD_CALL";
                e.file_path = file.file;
                e.start_line = r.begin.line; e.start_column = r.begin.column;
                e.end_line = r.end.line; e.end_column = r.end.column;
                e.source_snippet = snippet;
                try {
                    e.evidence_id = "sha256:" + sha256(JSON.writeValueAsBytes(List.of(file.file,
                            file.source_sha256, m.method_id, e.evidence_type,
                            e.start_line, e.start_column, e.end_line, e.end_column)));
                } catch (IOException ex) { throw new IllegalStateException(ex); }
                m.evidence_occurrences.add(e);
            }
            m.evidence_occurrences.sort(Comparator.comparingInt((EvidenceOccurrence e) -> e.start_line)
                    .thenComparingInt(e -> e.start_column).thenComparingInt(e -> e.end_line)
                    .thenComparingInt(e -> e.end_column).thenComparing(e -> e.evidence_type));
        }
        return m;
    }

    static boolean belongsTo(Node node, MethodDeclaration method) {
        for (Node p = node; p != null && p != method; p = p.getParentNode().orElse(null)) {
            if (p instanceof TypeDeclaration<?> || p instanceof CallableDeclaration<?>) return false;
            if (p instanceof ObjectCreationExpr o && o.getAnonymousClassBody().isPresent()) {
                // Anonymous class members are excluded; constructor arguments are not.
                if (o.getAnonymousClassBody().get().stream().anyMatch(b -> b == node || b.isAncestorOf(node))) return false;
            }
        }
        return method.isAncestorOf(node);
    }

    static void detectIdentityConflicts(IndexResult result) {
        Map<String, List<MethodInfo>> groups = new TreeMap<>();
        for (FileInfo f : result.files) for (ClassInfo c : f.classes) for (MethodInfo m : c.methods) {
            if (m.method_id != null) groups.computeIfAbsent(m.method_id, k -> new ArrayList<>()).add(m);
        }
        for (Map.Entry<String, List<MethodInfo>> entry : groups.entrySet()) {
            if (entry.getValue().size() < 2) continue;
            result.metadata.identity_conflict_groups++;
            result.metadata.identity_conflict_methods += entry.getValue().size();
            result.metadata.conflicting_method_ids.add(entry.getKey());
            for (MethodInfo m : entry.getValue()) {
                m.identity_status = "CONFLICT"; m.method_id = null; m.evidence_occurrences.clear();
            }
        }
    }

    /** Fail closed: global token fidelity AND per-node raw/token equality are required. */
    /** Raw lexical classification only: unknown contexts and even ineligible markers fail closed. */
    static Set<Integer> narrowUnicodeCharPositions(String source) {
        Set<Integer> markers = new HashSet<>();
        for (int at = source.indexOf("\\u"); at >= 0; at = source.indexOf("\\u", at + 1)) markers.add(at);
        if (markers.isEmpty()) return markers;
        Set<Integer> allowed = new HashSet<>();
        for (int i = 0; i < source.length();) {
            if (source.startsWith("//", i)) {
                i += 2;
                while (i < source.length() && source.charAt(i) != '\r' && source.charAt(i) != '\n') i++;
            } else if (source.startsWith("/*", i)) {
                int end = source.indexOf("*/", i + 2);
                if (end < 0) return null;
                i = end + 2;
            } else if (source.startsWith("\"\"\"", i)) {
                return null;
            } else if (source.charAt(i) == '\'' || source.charAt(i) == '"') {
                char quote = source.charAt(i);
                if (quote == '\'' && (source.startsWith("'\\u0000'", i) || source.startsWith("'\\uFFFD'", i))) {
                    allowed.add(i + 1); i += 8; continue;
                }
                boolean closed = false;
                i++;
                while (i < source.length() && source.charAt(i) != '\r' && source.charAt(i) != '\n') {
                    if (source.charAt(i) == '\\') i += 2;
                    else if (source.charAt(i) == quote) { i++; closed = true; break; }
                    else i++;
                }
                if (!closed) return null;
            } else i++;
        }
        return markers.equals(allowed) ? allowed : null;
    }

    static class SourceMap {
        final String source;
        final List<Integer> starts = new ArrayList<>();
        boolean reliable;
        boolean unescapedSlicesOnly;
        String reason = "SOURCE_TOKEN_MAPPING_UNAVAILABLE";
        SourceMap(String source, CompilationUnit cu) {
            this.source = source;
            starts.add(0);
            for (int i = 0; i < source.length(); i++) {
                char ch = source.charAt(i);
                if (ch == '\r') {
                    if (i + 1 < source.length() && source.charAt(i + 1) == '\n') i++;
                    starts.add(i + 1);
                } else if (ch == '\n') starts.add(i + 1);
            }
            Set<Integer> allowed = narrowUnicodeCharPositions(source);
            if (allowed == null) {
                reason = "UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED";
                // An unsupported escape need not invalidate unrelated methods. Only
                // opaque lexical contexts that cannot change token boundaries qualify;
                // global raw/token fidelity below is still mandatory, and escaped
                // slices themselves remain unavailable.
                if (!opaqueUnicodeEscapes(cu)) return;
                unescapedSlicesOnly = true;
            }
            if (source.startsWith("\ufeff")) { reason = "BOM_RAW_MAPPING_UNSUPPORTED"; return; }
            if (allowed != null && !allowed.isEmpty()) {
                Set<Integer> checked = new HashSet<>();
                for (CharLiteralExpr literal : cu.findAll(CharLiteralExpr.class)) {
                    Range r = range(literal);
                    if (r == null) continue;
                    String raw = raw(r);
                    if (!("'\\u0000'".equals(raw) || "'\\uFFFD'".equals(raw))) continue;
                    int at = starts.get(r.begin.line - 1) + r.begin.column;
                    char expected = raw.equals("'\\u0000'") ? (char) 0 : (char) 0xFFFD;
                    try {
                        if (literal.asChar() != expected || literal.getTokenRange().isEmpty()
                                || !raw.equals(literal.getTokenRange().get().toString())) return;
                    } catch (RuntimeException invalid) { return; }
                    checked.add(at);
                }
                if (!checked.equals(allowed)) return;
            }
            if (cu.getTokenRange().isEmpty()) return;
            StringBuilder tokens = new StringBuilder();
            for (JavaToken token : cu.getTokenRange().get()) {
                String text = token.getText();
                tokens.append(text);
                if (!text.isBlank() && (!token.hasRange() || !text.equals(raw(token.getRange().get())))) return;
            }
            reliable = source.equals(tokens.toString());
        }
        static boolean opaqueUnicodeEscapes(CompilationUnit cu) {
            if (cu.getTokenRange().isEmpty()) return false;
            for (JavaToken token : cu.getTokenRange().get()) {
                String text = token.getText();
                for (int at = text.indexOf("\\u"); at >= 0; at = text.indexOf("\\u", at + 2)) {
                    boolean comment = token.getCategory().isComment();
                    char quote = token.getKind() == GeneratedJavaParserConstants.CHARACTER_LITERAL ? '\''
                            : token.getKind() == GeneratedJavaParserConstants.STRING_LITERAL ? '"' : 0;
                    if ((!comment && quote == 0) || (at > 0 && text.charAt(at - 1) == '\\')
                            || at + 6 > text.length()) return false;
                    String hex = text.substring(at + 2, at + 6);
                    if (!hex.matches("[0-9a-fA-F]{4}")) return false;
                    char decoded = (char) Integer.parseInt(hex, 16);
                    // Reject line breaks, escape chaining, comment delimiters and
                    // literal-closing quotes. No source transformation is performed.
                    if (decoded == '\r' || decoded == '\n' || decoded == '\\'
                            || (comment ? decoded == '*' || decoded == '/' : decoded == quote)) return false;
                }
            }
            return true;
        }
        String raw(Range r) {
            if (r.begin.line < 1 || r.end.line > starts.size() || r.begin.column < 1 || r.end.column < 1) return null;
            int begin = starts.get(r.begin.line - 1) + r.begin.column - 1;
            int end = starts.get(r.end.line - 1) + r.end.column;
            if (begin < 0 || end < begin || end > source.length()) return null;
            if (r.begin.line < starts.size() && begin >= starts.get(r.begin.line)) return null;
            if (r.end.line < starts.size() && end > starts.get(r.end.line)) return null;
            return source.substring(begin, end);
        }
        String slice(Node n) {
            if (!reliable || n.getRange().isEmpty() || n.getTokenRange().isEmpty()) return null;
            for (JavaToken token : n.getTokenRange().get()) {
                if (!token.getText().isBlank() && (!token.hasRange()
                        || !token.getText().equals(raw(token.getRange().get())))) return null;
            }
            Range range = range(n);
            if (range == null) return null;
            String raw = raw(range);
            return raw != null && !(unescapedSlicesOnly && raw.contains("\\u"))
                    && raw.equals(n.getTokenRange().get().toString()) ? raw : null;
        }
        Range range(Node n) {
            // Token endpoints are checked against original text globally. Some StringLiteralExpr
            // node ranges in 3.26.4 include a following character when escapes are present.
            // Never repair that by guessing offsets: use the actual first/last token ranges.
            return n.getTokenRange().flatMap(TokenRange::toRange).orElse(null);
        }
    }

    static String sha256(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    static Path metadataPath(Path output) { return output.resolveSibling(output.getFileName() + ".meta.json"); }
    static void requireNewPair(Path output) throws IOException {
        if (Files.exists(output, LinkOption.NOFOLLOW_LINKS) || Files.exists(metadataPath(output), LinkOption.NOFOLLOW_LINKS)) {
            throw new FileAlreadyExistsException("Index or metadata already exists: " + output);
        }
    }
    static void writeIndex(IndexResult result, Path output) throws IOException {
        output = output.toAbsolutePath().normalize();
        requireNewPair(output);
        Path parent = output.getParent();
        Files.createDirectories(parent);
        byte[] bytes = JSON.writeValueAsBytes(result.files);
        result.metadata.index_file = output.getFileName().toString();
        result.metadata.index_sha256 = sha256(bytes);
        byte[] meta = JSON.writeValueAsBytes(result.metadata);
        Path indexStage = Files.createTempFile(parent, ".issuelens-index-", ".tmp");
        Path metaStage = null;
        try {
            metaStage = Files.createTempFile(parent, ".issuelens-meta-", ".tmp");
            Files.write(indexStage, bytes);
            Files.write(metaStage, meta);
            publishStagedPair(indexStage, metaStage, output);
        } finally {
            Files.deleteIfExists(indexStage);
            if (metaStage != null) Files.deleteIfExists(metaStage);
        }
    }
    static void publishStagedPair(Path indexStage, Path metaStage, Path output) throws IOException {
        boolean indexPublished = false;
        boolean complete = false;
        try {
            // No REPLACE_EXISTING; ATOMIC_MOVE may replace a destination, so is not used.
            Files.move(indexStage, output);
            indexPublished = true;
            Files.move(metaStage, metadataPath(output));
            complete = true;
        } finally {
            if (!complete && indexPublished) Files.deleteIfExists(output);
        }
    }
    private static void printUsage() {
        System.out.println("Java Source Code Indexer: Main <projectRoot> [--output <newIndexPath>]");
        System.out.println("Existing index/metadata files are never overwritten. Use --output for safe evaluation.");
    }
    public static class FileInfo {
        public String file;
        @JsonProperty("package") public String packageName = "";
        public List<ClassInfo> classes = new ArrayList<>();
        public String source_sha256, parse_status;
        public List<String> parse_problems = new ArrayList<>();
        public List<String> limitations = new ArrayList<>();
    }
    public static class ClassInfo {
        public String name;
        public List<MethodInfo> methods = new ArrayList<>();
    }
    public static class MethodInfo {
        @com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL)
        public String identity_signature;
        @com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL)
        public String source_slice_sha256;
        public String name;
        public int line;
        public List<String> annotations = new ArrayList<>();
        public List<String> strings = new ArrayList<>();
        public List<String> calls = new ArrayList<>();
        public String method_id, qualified_class_name, signature, identity_status, location_status;
        public Integer start_line, start_column, end_line, end_column;
        public boolean has_body;
        public int evidence_omissions;
        public List<EvidenceOccurrence> evidence_occurrences = new ArrayList<>();
    }
    public static class EvidenceOccurrence {
        public String evidence_id, evidence_type, file_path, source_snippet;
        public int start_line, start_column, end_line, end_column;
    }
    public static class IndexResult {
        public List<FileInfo> files = new ArrayList<>();
        public Metadata metadata = new Metadata();
    }
    public static class Metadata {
        @com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL)
        public String identity_resolution_runtime;
        public String schema_version = "2.0";
        public String method_id_scheme = "java-source-v1";
        public String position_convention = "1-based-inclusive-UTF16-tab1";
        public String source_encoding = "UTF-8";
        public String parser_version = "3.26.4";
        public String language_level = "BLEEDING_EDGE";
        public boolean preprocess_unicode_escapes = false;
        public String index_file, index_sha256;
        public List<String> source_roots = List.of("src/main/java");
        public Map<String, Integer> parse_counts = new TreeMap<>(Map.of("PARSED", 0, "PARTIAL", 0, "FAILED", 0, "READ_ERROR", 0));
        public int files_total, methods_total, evidence_occurrences;
        public int identity_conflict_groups, identity_conflict_methods;
        public int identity_unavailable_methods, location_unavailable_methods;
        public List<String> conflicting_method_ids = new ArrayList<>();
    }
}
