import com.fasterxml.jackson.databind.JsonNode;
import com.github.javaparser.*;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.AnnotatedElementContext;
import org.junit.jupiter.api.extension.ExtensionContext;
import org.junit.jupiter.api.io.CleanupMode;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.api.io.TempDirFactory;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MainTest {
    /** Keep fixtures/reports inside the explicitly configured build temp, including failures. */
    public static class TestTempFactory implements TempDirFactory {
        public Path createTempDirectory(AnnotatedElementContext element, ExtensionContext context) throws IOException {
            Path root = Path.of(System.getProperty("task001.test.root"));
            Files.createDirectories(root);
            return Files.createTempDirectory(root, "case-");
        }
    }
    @TempDir(factory = TestTempFactory.class, cleanup = CleanupMode.NEVER) Path temp;

    Path source(String path, String text) throws IOException {
        Path p = temp.resolve("repo/src/main/java").resolve(path);
        Files.createDirectories(p.getParent());
        Files.writeString(p, text, StandardCharsets.UTF_8);
        return p;
    }
    Main.IndexResult index() throws IOException { return Main.indexProject(temp.resolve("repo")); }
    List<Main.MethodInfo> methods(Main.IndexResult result) {
        return result.files.stream().flatMap(f -> f.classes.stream()).flatMap(c -> c.methods.stream()).toList();
    }
    Main.MethodInfo first() throws IOException { return methods(index()).get(0); }

    @Test void normalIdentityAndRange() throws Exception {
        source("p/A.java", "package p;\nclass A {\n  int answer(int x) {\n    return x;\n  }\n}\n");
        Main.MethodInfo m = first();
        assertEquals("java-source-v1:p.A#answer(int)", m.method_id);
        assertEquals("answer(int)", m.signature);
        assertEquals("p.A", m.qualified_class_name);
        assertEquals(3, m.line); assertEquals(3, m.start_line); assertEquals(3, m.start_column);
        assertEquals(5, m.end_line); assertEquals(3, m.end_column); assertTrue(m.has_body);
        assertEquals("AVAILABLE", m.location_status);
    }

    @Test void overloadsTypesAndVarargs() throws Exception {
        source("A.java", "class A { void f(int x) {} void f(String x) {} void f(String... x) {} "
                + "void g(java.util.Map<String, java.util.List<Integer>> x, int[] y) {} }");
        List<Main.MethodInfo> ms = methods(index());
        assertEquals(4, ms.stream().map(m -> m.method_id).distinct().count());
        assertEquals("f(String[])", ms.get(2).signature);
        assertEquals("g(java.util.Map<String,java.util.List<Integer>>,int[])", ms.get(3).signature);
    }

    @Test void packageClassAndNestedTypeIdentity() throws Exception {
        source("p/A.java", "package p; class A { void f() {} class Inner { void f() {} } } class B { void f() {} }");
        source("q/A.java", "package q; class A { void f() {} }");
        Set<String> ids = new HashSet<>(methods(index()).stream().map(m -> m.method_id).toList());
        assertEquals(Set.of("java-source-v1:p.A#f()", "java-source-v1:p.A.Inner#f()",
                "java-source-v1:p.B#f()", "java-source-v1:q.A#f()"), ids);
    }

    @Test void methodIdentitySurvivesBodyAndWhitespaceChanges() throws Exception {
        Path path = source("A.java", "class A { void f(int a) { print(\"one\"); } }");
        String before = first().method_id;
        Files.writeString(path, "class A {\n\n void f( int renamed ) { print(\"two\"); }\n}");
        assertEquals(before, first().method_id);
    }

    @Test void repeatedEvidenceAndOriginalSpelling() throws Exception {
        String raw = "class A {\n @PostMapping( value = \"/x\" )\n void f(@Valid String x) {\n"
                + "  sink(\"same\"); sink ( \"same\" );\n  sink(\"a\\nb\");\n }\n}\n";
        source("A.java", raw);
        Main.MethodInfo m = first();
        assertEquals(List.of("same", "a\nb"), m.strings);
        assertEquals(List.of("sink"), m.calls);
        List<Main.EvidenceOccurrence> es = m.evidence_occurrences;
        assertEquals(2, es.stream().filter(e -> e.evidence_type.equals("ANNOTATION")).count());
        assertEquals(3, es.stream().filter(e -> e.evidence_type.equals("STRING_LITERAL")).count());
        assertEquals(3, es.stream().filter(e -> e.evidence_type.equals("METHOD_CALL")).count());
        assertEquals(es.size(), es.stream().map(e -> e.evidence_id).distinct().count());
        assertTrue(es.stream().anyMatch(e -> e.source_snippet.equals("@PostMapping( value = \"/x\" )")));
        assertTrue(es.stream().anyMatch(e -> e.source_snippet.equals("sink ( \"same\" )")));
        assertTrue(es.stream().anyMatch(e -> e.source_snippet.equals("\"a\\nb\"")));
        for (Main.EvidenceOccurrence e : es) assertEquals(e.source_snippet, independentSlice(raw, e));
        assertTrue(es.stream().filter(e -> e.evidence_type.equals("METHOD_CALL"))
                .noneMatch(e -> e.source_snippet.endsWith(";")));
    }

    // Independent test oracle: split with retained line endings, then UTF-16 substring.
    String independentSlice(String raw, Main.EvidenceOccurrence e) {
        String[] lines = raw.split("(?<=\\n)|(?<=\\r)(?!\\n)", -1);
        int start = 0, end = 0;
        for (int i = 0; i < e.start_line - 1; i++) start += lines[i].length();
        for (int i = 0; i < e.end_line - 1; i++) end += lines[i].length();
        return raw.substring(start + e.start_column - 1, end + e.end_column);
    }

    @Test void newlineTabChineseAndSurrogatePairs() throws Exception {
        for (String newline : List.of("\n", "\r\n", "\r")) {
            String raw = "class A {" + newline + "\tvoid f() { sink(\"中文😀\"); sink(" + newline
                    + "\t\t\"second\"); }" + newline + "}" + newline;
            source("A.java", raw);
            Main.MethodInfo m = first();
            assertEquals("AVAILABLE", m.location_status, newline);
            assertEquals(2, m.start_column);
            assertEquals(4, m.evidence_occurrences.size());
            for (Main.EvidenceOccurrence e : m.evidence_occurrences) assertEquals(e.source_snippet, independentSlice(raw, e));
            Main.EvidenceOccurrence literal = m.evidence_occurrences.stream()
                    .filter(e -> e.source_snippet.equals("\"中文😀\"")).findFirst().orElseThrow();
            assertEquals(6, literal.end_column - literal.start_column + 1);
        }
    }

    @Test void unicodeEscapeFailsClosedWithoutConflatingParseAndIdentity() throws Exception {
        String escape = "\\" + "u0061";
        source("A.java", "class A { void f() { sink(\"" + escape + "\"); } }");
        Main.IndexResult result = index();
        assertEquals("PARSED", result.files.get(0).parse_status);
        Main.MethodInfo m = methods(result).get(0);
        assertEquals("AVAILABLE", m.identity_status);
        assertEquals("UNAVAILABLE_MAPPING", m.location_status);
        assertNull(m.start_line); assertTrue(m.evidence_occurrences.isEmpty());
        assertTrue(result.files.get(0).limitations.contains("UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED"));
    }

    @Test void unicodeEscapeChangingLineStructureFailsClosed() throws Exception {
        String escape = "\\" + "u000a";
        source("A.java", "class A { // comment" + escape + " void f(){sink(\"x\");} }");
        Main.IndexResult result = index();
        // Preserve the v1 parser's Unicode preprocessing policy. It need not recover a method.
        assertTrue(methods(result).stream().allMatch(m -> !m.location_status.equals("AVAILABLE")));
        assertTrue(methods(result).stream().allMatch(m -> m.evidence_occurrences.isEmpty()));
    }

    @Test void corruptedButInBoundsRangeAndMissingRangeAreRejected() {
        String raw = "class A { void f(){sink(\"x\");} }";
        CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
        Main.SourceMap map = new Main.SourceMap(raw, cu);
        MethodDeclaration m = cu.findFirst(MethodDeclaration.class).orElseThrow();
        assertNotNull(map.slice(m));
        JavaToken token = m.getTokenRange().orElseThrow().getEnd();
        Range r = token.getRange().orElseThrow();
        token.setRange(new Range(r.begin, new Position(r.end.line, r.end.column - 1)));
        assertNull(map.slice(m));
        m.setRange(null); assertNull(map.slice(m));
    }

    @Test void alteredTokenTextDisablesGlobalMapping() {
        String raw = "class A { void f(){} }";
        CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
        cu.getTokenRange().orElseThrow().getBegin().setText("wrong");
        Main.SourceMap map = new Main.SourceMap(raw, cu);
        assertFalse(map.reliable);
    }

    @Test void fileHashUsesExactBytes() throws Exception {
        Path path = source("A.java", "class A { void f(){} }\r\n");
        String expected = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
        assertEquals(expected, index().files.get(0).source_sha256);
        Files.writeString(path, "class A { void f(){} }\n");
        assertNotEquals(expected, index().files.get(0).source_sha256);
    }

    @Test void oldFieldsHaveOriginalTypesValuesAndDeduplication() throws Exception {
        source("p/A.java", "package p; class A { @GetMapping(\"/x\") void f(){sink(\"x\");sink(\"x\");} }");
        JsonNode file = Main.JSON.valueToTree(index().files).get(0);
        assertEquals("src/main/java/p/A.java", file.get("file").asText());
        assertEquals("p", file.get("package").asText()); assertTrue(file.get("classes").isArray());
        JsonNode cls = file.get("classes").get(0), m = cls.get("methods").get(0);
        assertEquals("A", cls.get("name").asText()); assertEquals("f", m.get("name").asText());
        assertTrue(m.get("line").isIntegralNumber()); assertEquals(1, m.get("line").asInt());
        assertEquals(Main.JSON.readTree("[\"@GetMapping(\\\"/x\\\")\"]"), m.get("annotations"));
        assertEquals(Main.JSON.readTree("[\"x\"]"), m.get("strings"));
        assertEquals(Main.JSON.readTree("[\"sink\"]"), m.get("calls"));
    }

    @Test void duplicateIdsAcrossFilesAreExplicitConflicts() throws Exception {
        source("one/A.java", "package p; class A { void f() {sink(\"x\");} }");
        source("two/A.java", "package p; class A { void f() {sink(\"y\");} }");
        Main.IndexResult r = index();
        assertEquals(1, r.metadata.identity_conflict_groups); assertEquals(2, r.metadata.identity_conflict_methods);
        assertEquals(List.of("java-source-v1:p.A#f()"), r.metadata.conflicting_method_ids);
        for (Main.MethodInfo m : methods(r)) {
            assertEquals("CONFLICT", m.identity_status); assertNull(m.method_id);
            assertEquals("AVAILABLE", m.location_status); assertTrue(m.evidence_occurrences.isEmpty());
        }
    }

    @Test void arrayAndVarargDuplicateIdentityIsNotHiddenByLineNumbers() throws Exception {
        source("A.java", "class A { void f(String[] x){} void f(String... x){} }");
        Main.IndexResult r = index();
        assertEquals(2, r.metadata.identity_conflict_methods);
        assertTrue(methods(r).stream().allMatch(m -> m.method_id == null));
    }

    @Test void scopeBoundariesAndUnsupportedLocalIdentity() throws Exception {
        source("A.java", "class A { void outer() { class Local { void inner(){sink(\"local\");} } "
                + "Object o = new Object(){ void anon(){sink(\"anon\");} }; "
                + "Runnable r = () -> sink(\"lambda\"); } }");
        Main.IndexResult r = index();
        assertEquals(2, methods(r).size());
        Main.MethodInfo outer = methods(r).get(0), inner = methods(r).get(1);
        assertEquals(List.of("local", "anon", "lambda"), outer.strings);
        assertTrue(outer.evidence_occurrences.stream().noneMatch(e -> e.source_snippet.contains("local") || e.source_snippet.contains("anon")));
        assertEquals(2, outer.evidence_occurrences.size());
        assertEquals("UNSUPPORTED_LOCAL_TYPE", inner.identity_status); assertNull(inner.method_id);
        assertTrue(inner.evidence_occurrences.isEmpty());
        assertTrue(r.files.get(0).limitations.contains("ANONYMOUS_CLASS_METHODS_NOT_INDEXED"));
    }

    @Test void abstractMethodConstructorAndUnsupportedTypes() throws Exception {
        source("A.java", "abstract class A { A(){} abstract void f(); } record R(int x){void g(){}} enum E { X; void h(){} }");
        Main.IndexResult r = index();
        assertEquals(1, methods(r).size()); assertFalse(methods(r).get(0).has_body);
        assertEquals("java-source-v1:A#f()", methods(r).get(0).method_id);
        assertTrue(r.files.get(0).limitations.contains("ENUM_RECORD_ANNOTATION_DECLARATIONS_NOT_INDEXED_AS_CLASSES"));
    }

    @Test void partialParsePreservesStatusAndSuppressesNewEvidence() throws Exception {
        Path p = source("A.java", "class A { void f(){ Runnable r = () -> sink(\"x\"); } }");
        JavaParser older = new JavaParser(new ParserConfiguration().setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_7));
        Main.FileInfo f = Main.parseJavaFile(older, temp.resolve("repo"), p);
        assertEquals("PARTIAL", f.parse_status); assertFalse(f.parse_problems.isEmpty());
        assertEquals("UNAVAILABLE_PARSE", f.classes.get(0).methods.get(0).identity_status);
        assertEquals("UNAVAILABLE_PARSE", f.classes.get(0).methods.get(0).location_status);
        assertTrue(f.classes.get(0).methods.get(0).evidence_occurrences.isEmpty());
    }

    @Test void failedParseIsExplicitAndHasNoVerifiedEvidence() throws Exception {
        source("A.java", "\"unterminated");
        Main.IndexResult r = index();
        assertEquals("FAILED", r.files.get(0).parse_status);
        assertFalse(r.files.get(0).parse_problems.isEmpty()); assertTrue(r.files.get(0).classes.isEmpty());
        assertEquals(1, r.metadata.parse_counts.get("FAILED"));
        assertFalse(Main.JSON.writeValueAsString(r.files).contains("VERIFIED"));
    }

    @Test void readErrorAndDecodeErrorAreDistinctFromParseFailure() throws Exception {
        Path p = source("A.java", "class A {}");
        Files.write(p, new byte[]{(byte)0xff});
        Main.IndexResult r = index();
        assertEquals("READ_ERROR", r.files.get(0).parse_status); assertNotNull(r.files.get(0).source_sha256);
        assertEquals(1, r.metadata.parse_counts.get("READ_ERROR"));
        Main.FileInfo missing = Main.parseJavaFile(Main.parser(), temp.resolve("repo"), p.resolveSibling("missing.java"));
        assertEquals("READ_ERROR", missing.parse_status); assertNull(missing.source_sha256);
    }

    @Test void deterministicSerializationAndBoundMetadata() throws Exception {
        source("A.java", "class A { void f(){sink(\"x\");} }");
        Path a = temp.resolve("one/index.v2.json"), b = temp.resolve("two/index.v2.json");
        Main.writeIndex(index(), a); Main.writeIndex(index(), b);
        assertArrayEquals(Files.readAllBytes(a), Files.readAllBytes(b));
        assertArrayEquals(Files.readAllBytes(Main.metadataPath(a)), Files.readAllBytes(Main.metadataPath(b)));
        JsonNode meta = Main.JSON.readTree(Files.readAllBytes(Main.metadataPath(a)));
        assertEquals(Main.sha256(Files.readAllBytes(a)), meta.get("index_sha256").asText());
        assertEquals("2.0", meta.get("schema_version").asText());
        assertEquals(4, meta.get("parse_counts").size());
    }

    @Test void cliSeparateOutputAndRefusalToOverwrite() throws Exception {
        source("A.java", "class A { void f(){} }");
        Path old = temp.resolve("repo/index.json"); Files.writeString(old, "[\"sentinel\"]");
        Path output = temp.resolve("new/index.json");
        Main.main(new String[]{temp.resolve("repo").toString(), "--output", output.toString()});
        assertEquals("[\"sentinel\"]", Files.readString(old));
        byte[] written = Files.readAllBytes(output);
        assertThrows(FileAlreadyExistsException.class, () -> Main.main(new String[]{temp.resolve("repo").toString(), "--output", output.toString()}));
        assertArrayEquals(written, Files.readAllBytes(output));
        assertThrows(IOException.class, () -> Main.main(new String[]{temp.resolve("repo").toString(), "--output", old.toString()}));
        assertThrows(FileAlreadyExistsException.class, () -> Main.main(new String[]{temp.resolve("repo").toString()}));
    }

    @Test void existingSidecarPreventsAnyMainIndexPublication() throws Exception {
        source("A.java", "class A {}");
        Path out = temp.resolve("blocked.json"); Files.writeString(Main.metadataPath(out), "sentinel");
        assertThrows(FileAlreadyExistsException.class, () -> Main.writeIndex(index(), out));
        assertFalse(Files.exists(out)); assertEquals("sentinel", Files.readString(Main.metadataPath(out)));
    }

    @Test void emptyScanStillHasSchemaAndCompleteCounts() throws Exception {
        Files.createDirectories(temp.resolve("repo/src/main/java"));
        Main.IndexResult r = index(); Path out = temp.resolve("empty.json"); Main.writeIndex(r, out);
        assertTrue(Main.JSON.readTree(Files.readAllBytes(out)).isArray()); assertEquals(0, r.metadata.files_total);
        assertEquals(Map.of("PARSED",0,"PARTIAL",0,"FAILED",0,"READ_ERROR",0), r.metadata.parse_counts);
    }

    @Test void publicationFailureRemovesOnlyNewlyPublishedIndex() throws Exception {
        Path stage = temp.resolve("stage"), out = temp.resolve("out.json");
        Files.writeString(stage, "new-index");
        assertThrows(IOException.class, () -> Main.publishStagedPair(stage, temp.resolve("missing-meta"), out));
        assertFalse(Files.exists(out)); assertFalse(Files.exists(Main.metadataPath(out)));
        Files.writeString(out, "existing"); Files.writeString(stage, "new-index");
        assertThrows(FileAlreadyExistsException.class, () -> Main.publishStagedPair(stage, temp.resolve("missing-meta"), out));
        assertEquals("existing", Files.readString(out));
    }

    @Test void bomNeverProducesGuessedLocations() throws Exception {
        source("A.java", "\ufeffclass A {void f(){sink(\"x\");}}");
        Main.IndexResult r = index();
        assertTrue(methods(r).stream().allMatch(m -> m.evidence_occurrences.isEmpty()));
        assertTrue(methods(r).stream().allMatch(m -> !m.location_status.equals("AVAILABLE")));
    }

    @Test void signatureExcludesParameterTypeAnnotationsAndComments() throws Exception {
        source("A.java", "class A { void f(java.util.List<@Mark String> x, String /* note */ [] y) {} }");
        assertEquals("f(java.util.List<String>,String[])", first().signature);
    }

    @Test void narrowUnicodeCharsPreserveRawEvidenceAcrossNewlines() throws Exception {
        String u = "\\" + "u";
        for (String nl : List.of("\n", "\r\n", "\r")) {
            String raw = "class A {" + nl + "\t@Deprecated" + nl
                    + "\tvoid f(){ char c='" + u + "0000'; char d='" + u + "FFFD';"
                    + " sink(\"中文😀\"); sink(String.valueOf('" + u + "0000')); }" + nl + "}";
            source("A.java", raw);
            Main.IndexResult r = index();
            Main.MethodInfo m = methods(r).get(0);
            assertEquals("PARSED", r.files.get(0).parse_status);
            assertEquals("AVAILABLE", m.location_status);
            assertEquals("java-source-v1:A#f()", m.method_id);
            assertTrue(r.files.get(0).limitations.isEmpty());
            assertFalse(Main.parser().getParserConfiguration().isPreprocessUnicodeEscapes());
            for (Main.EvidenceOccurrence e : m.evidence_occurrences)
                assertEquals(e.source_snippet, independentSlice(raw, e));
            assertTrue(m.evidence_occurrences.stream().anyMatch(e -> e.source_snippet.equals("String.valueOf('" + u + "0000')")));
        }
    }

    @Test void narrowUnicodeRealTriggerContextsAreSupported() throws Exception {
        String u = "\\" + "u";
        for (String body : List.of("char replacementChar='" + u + "FFFD';",
                "String nullString=String.valueOf('" + u + "0000');", "char nullChar='" + u + "0000';")) {
            source("A.java", "class A { " + body + " void f(){sink(\"x\");} }");
            assertEquals("AVAILABLE", first().location_status);
        }
    }

    @Test void narrowUnicodeRejectsUnknownAndIneligibleMarkers() {
        String b = "\\", u = b + "u";
        for (String content : List.of("'" + u + "000a'", "'" + u + "0009'", "'" + u + "0041'",
                "'" + u + "fffd'", "'" + b + "uu0000'", "'" + u + "00G0'", "'" + u + "000'",
                "'" + b + b + "u0000'", "'" + b + b + b + "u0000'",
                "\"" + u + "0000\"", "\"" + u + "FFFD" + u + "0000\"",
                "\"" + u + "005c" + u + "005a\"", u + "0009", "int " + u + "0066=0;")) {
            assertNull(Main.narrowUnicodeCharPositions("class A { void f(){ " + content + "; } }"), content);
        }
    }

    @Test void narrowUnicodeRejectsCommentAndQuotedLookalikes() {
        String u = "\\" + "u", safe = "'" + u + "0000'";
        for (String s : List.of("// " + safe + "\nclass A {}", "/* " + safe + " */ class A {}",
                "class A {String s=\"" + safe + "\";}",
                "class A {String s=\"\"\"\n" + safe + "\n\"\"\";}"))
            assertNull(Main.narrowUnicodeCharPositions(s), s);
    }

    @Test void narrowUnicodeDoesNotHideUnsafeMarkerInSameFile() throws Exception {
        String u = "\\" + "u";
        source("A.java", "class A {char c='" + u + "0000'; void f(){sink(\"" + u + "0041\");}}");
        assertEquals("UNAVAILABLE_MAPPING", first().location_status);
        assertTrue(first().evidence_occurrences.isEmpty());
    }

    @Test void narrowUnicodeChecksAstCharacterValueIndependently() {
        String raw = "class A {char c='" + "\\" + "u0000';void f(){sink(\"x\");}}";
        CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
        assertTrue(new Main.SourceMap(raw, cu).reliable);
        cu.findFirst(com.github.javaparser.ast.expr.CharLiteralExpr.class).orElseThrow().setValue("x");
        assertFalse(new Main.SourceMap(raw, cu).reliable);
    }

    @Test void narrowUnicodeRequiresMatchingAstCharacterNode() {
        String raw = "class A {char c='" + "\\" + "uFFFD';void f(){sink(\"x\");}}";
        CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
        cu.findFirst(com.github.javaparser.ast.expr.CharLiteralExpr.class).orElseThrow().replace(new com.github.javaparser.ast.expr.IntegerLiteralExpr("0"));
        assertFalse(new Main.SourceMap(raw, cu).reliable);
    }

    @Test void opaqueUnicodeElsewhereDoesNotDisableUnescapedMethod() throws Exception {
        String u = "\\" + "u";
        for (String nl : List.of("\n", "\r\n", "\r")) {
            String raw = "class A {" + nl
                    + "char upper='" + u + "0141', lower='" + u + "0142';" + nl
                    + "/** isAsciiPrintable(\"" + u + "007f\") = false */" + nl
                    + "  @Deprecated String unwrap(String s, String wrap) { return s; }" + nl + "}";
            source("A.java", raw);
            assertNull(Main.narrowUnicodeCharPositions(raw)); // reproduce old constructor's early return
            CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
            Main.SourceMap map = new Main.SourceMap(raw, cu);
            MethodDeclaration method = cu.findFirst(MethodDeclaration.class).orElseThrow();
            assertTrue(map.reliable);
            assertEquals("@Deprecated String unwrap(String s, String wrap) { return s; }", map.slice(method));
            Main.MethodInfo m = first();
            assertEquals("AVAILABLE", m.location_status);
            assertEquals("java-source-v1:A#unwrap(String,String)", m.method_id);
            assertEquals(4, m.start_line); assertEquals(3, m.start_column);
        }
    }

    @Test void opaqueUnicodeFallbackStillRejectsEscapedSlices() throws Exception {
        String u = "\\" + "u";
        source("A.java", "class A { void changed(){ char c='" + u + "0141'; } "
                + "void unaffected(){sink(\"x\");} }");
        List<Main.MethodInfo> ms = methods(index());
        assertEquals("UNAVAILABLE_MAPPING", ms.get(0).location_status);
        assertNull(ms.get(0).start_line); assertTrue(ms.get(0).evidence_occurrences.isEmpty());
        assertEquals("AVAILABLE", ms.get(1).location_status);
        assertEquals(2, ms.get(1).evidence_occurrences.size());
    }

    @Test void opaqueUnicodeFallbackRejectsLexicalHazardsAndIneligibleEscapes() {
        String u = "\\" + "u";
        List<String> fields = new ArrayList<>();
        for (String code : List.of("000a", "000d", "005c", "002f", "002a"))
            fields.add("/* " + u + code + " */");
        fields.add("String s=\"" + u + "0022\";"); // quote may end the literal after translation
        fields.add("char c='" + u + "0027';");
        fields.add("String s=\"\\" + u + "0061\";"); // ineligible marker after a backslash
        fields.add("String s=\"\\uu0061\";");
        for (String field : fields) {
            String raw = "class A { " + field + " void unaffected(){} }";
            ParseResult<CompilationUnit> parsed = Main.parser().parse(raw);
            if (parsed.getResult().isEmpty()) {
                assertFalse(parsed.isSuccessful(), field);
                continue; // A lexical parse failure is already fail closed.
            }
            CompilationUnit cu = parsed.getResult().orElseThrow();
            Main.SourceMap map = new Main.SourceMap(raw, cu);
            assertFalse(map.reliable, field);
            for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) assertNull(map.slice(method), field);
        }
    }

    @Test void opaqueUnicodeFallbackRetainsGlobalTokenAndLocalRangeChecks() {
        String raw = "class A { char c='" + "\\" + "u0141'; void f(){sink(\"x\");} }";
        CompilationUnit cu = Main.parser().parse(raw).getResult().orElseThrow();
        Main.SourceMap map = new Main.SourceMap(raw, cu);
        MethodDeclaration method = cu.findFirst(MethodDeclaration.class).orElseThrow();
        assertNotNull(map.slice(method));
        JavaToken end = method.getTokenRange().orElseThrow().getEnd();
        Range r = end.getRange().orElseThrow();
        end.setRange(new Range(r.begin, new Position(r.end.line, r.end.column - 1)));
        assertNull(map.slice(method));
        assertFalse(new Main.SourceMap(raw, cu).reliable);
        cu = Main.parser().parse(raw).getResult().orElseThrow();
        cu.getTokenRange().orElseThrow().getBegin().setText("wrong");
        assertFalse(new Main.SourceMap(raw, cu).reliable);
    }

    @Test void partialProjectCountIsSeparateFromOtherStatuses() throws Exception {
        source("A.java", "class A { void f(){ int x = ; } }");
        Main.IndexResult r = index();
        assertEquals("PARTIAL", r.files.get(0).parse_status);
        assertEquals(1, r.metadata.parse_counts.get("PARTIAL"));
        assertEquals(0, r.metadata.parse_counts.get("FAILED"));
        assertTrue(methods(r).stream().allMatch(m -> m.evidence_occurrences.isEmpty()));
    }
}
