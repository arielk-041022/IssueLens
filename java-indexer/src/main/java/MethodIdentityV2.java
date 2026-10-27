import com.github.javaparser.ast.*;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.type.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Stream;

/** Opt-in v2: scoped erasure, never a guessed ordinal/path identity or classpath lookup.
 * External simple names require explicit imports, a known source declaration, or
 * an unambiguous JDK on-demand import. Unknown/inherited/ambiguous names fail closed.
 */
final class MethodIdentityV2 {
    static class Unknown extends RuntimeException {}
    final CompilationUnit cu;
    final MethodDeclaration method;
    final Set<String> declared;
    final Map<String, Set<String>> parents;
    final Map<String, String> memberAccess;
    final List<List<TypeParameter>> scopes = new ArrayList<>();
    final List<Node> scopeNodes = new ArrayList<>();
    MethodIdentityV2(CompilationUnit cu, MethodDeclaration method, Set<String> declared, Map<String, Set<String>> parents,
                     Map<String, String> memberAccess) {
        this.cu = cu; this.method = method; this.declared = declared;
        this.parents = parents;
        this.memberAccess = memberAccess;
        scopes.add(method.getTypeParameters());
        scopeNodes.add(method);
        if (!method.isStatic()) {
            for (Node n = method.getParentNode().orElse(null); n != null; n = n.getParentNode().orElse(null)) {
                if (n instanceof ClassOrInterfaceDeclaration c) {
                    scopes.add(c.getTypeParameters());
                    scopeNodes.add(c);
                    boolean implicitStatic = c.getParentNode().filter(p -> p instanceof AnnotationDeclaration
                            || p instanceof ClassOrInterfaceDeclaration parent && parent.isInterface()).isPresent();
                    if (c.isStatic() || c.isInterface() || implicitStatic) break;
                }
            }
        }
        for (List<TypeParameter> scope : scopes) {
            Set<String> names = new HashSet<>();
            for (TypeParameter t : scope) if (!names.add(t.getNameAsString())) throw new Unknown();
        }
    }
    String signature() {
        List<String> params = new ArrayList<>();
        for (Parameter p : method.getParameters()) {
            Set<TypeParameter> active = Collections.newSetFromMap(new IdentityHashMap<>());
            params.add(erase(p.getType(), 0, active) + (p.isVarArgs() ? "[]" : ""));
        }
        return method.getNameAsString() + "(" + String.join(",", params) + ")";
    }
    String erase(Type type, int start, Set<TypeParameter> active) {
        if (type.isArrayType()) return erase(type.asArrayType().getComponentType(), start, active) + "[]";
        if (type.isPrimitiveType()) return type.asPrimitiveType().asString();
        if (!type.isClassOrInterfaceType()) throw new Unknown();
        ClassOrInterfaceType ref = type.asClassOrInterfaceType();
        String name = ref.getNameWithScope();
        for (int i = start; i < scopes.size(); i++) for (TypeParameter t : scopes.get(i)) {
            if (t.getNameAsString().equals(name)) {
                if (ref.getTypeArguments().isPresent() || !active.add(t)) throw new Unknown();
                String erased = t.getTypeBound().isEmpty() ? "java.lang.Object" : erase(t.getTypeBound().get(0), i, active);
                active.remove(t);
                return erased;
            }
            if (name.startsWith(t.getNameAsString() + ".")) throw new Unknown();
        }
        return resolve(name, start == 0 ? method.getParentNode().orElse(null) : scopeNodes.get(start));
    }
    static String owner(TypeDeclaration<?> declaration) {
        List<String> names = new ArrayList<>();
        for (Node n = declaration; n != null; n = n.getParentNode().orElse(null)) {
            if (n instanceof CallableDeclaration<?> || n instanceof ObjectCreationExpr || n instanceof InitializerDeclaration) throw new Unknown();
            if (n instanceof TypeDeclaration<?> t) names.add(t.getNameAsString());
        }
        Collections.reverse(names);
        String pkg = declaration.findCompilationUnit().orElseThrow().getPackageDeclaration().map(p -> p.getNameAsString() + ".").orElse("");
        return pkg + String.join(".", names);
    }
    static boolean jdk(String name) {
        return jdkClass(name) != null;
    }
    static Class<?> jdkClass(String name) {
        if (!name.startsWith("java.")) return null;
        String binary = name;
        while (true) {
            try { return Class.forName(binary, false, ClassLoader.getPlatformClassLoader()); }
            catch (ClassNotFoundException e) {
                int dot = binary.lastIndexOf('.');
                if (dot < 0) return null;
                binary = binary.substring(0, dot) + "$" + binary.substring(dot + 1);
            } catch (LinkageError | SecurityException e) { return null; }
        }
    }
    String resolve(String name) {
        return resolve(name, method.getParentNode().orElse(null));
    }
    String resolve(String name, Node lookupScope) {
        String first = name.split("\\.")[0];
        String tail = name.substring(first.length());
        // Lexically declared/enclosing member types precede imports.
        for (Node n = lookupScope; n != null; n = n.getParentNode().orElse(null)) {
            if (n instanceof TypeDeclaration<?> t) {
                String base = owner(t);
                if (t.getNameAsString().equals(first)) return base + tail;
                if (declared.contains(base + "." + first)) return base + "." + name;
                Set<String> inherited = inherited(base, first, new HashSet<>());
                if (inherited.size() > 1) throw new Unknown();
                if (inherited.size() == 1) return inherited.iterator().next() + tail;
            }
        }
        Set<String> explicit = new HashSet<>();
        for (ImportDeclaration imp : cu.getImports()) if (!imp.isAsterisk() && imp.getName().getIdentifier().equals(first)) {
            if (imp.isStatic()) throw new Unknown();
            explicit.add(imp.getNameAsString() + tail);
        }
        if (explicit.size() > 1) throw new Unknown();
        if (explicit.size() == 1) return explicit.iterator().next();
        String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString() + ".").orElse("");
        if (declared.contains(pkg + first)) return pkg + name;
        // An explicit package-qualified spelling is retained, never synthesized.
        if (name.contains(".") && Character.isLowerCase(name.charAt(0))) return name;
        Set<String> demand = new HashSet<>();
        if (jdk("java.lang." + first)) demand.add("java.lang." + name);
        for (ImportDeclaration imp : cu.getImports()) if (imp.isAsterisk()) {
            if (imp.isStatic() || !imp.getNameAsString().startsWith("java.")) throw new Unknown();
            String candidate = imp.getNameAsString() + "." + first;
            if (jdk(candidate)) demand.add(candidate + tail);
        }
        if (demand.size() != 1) throw new Unknown();
        return demand.iterator().next();
    }
    Set<String> inherited(String base, String name, Set<String> active) {
        if (!active.add(base)) throw new Unknown();
        Set<String> found = new HashSet<>();
        if (!declared.contains(base)) {
            Class<?> type = jdkClass(base);
            if (type == null) throw new Unknown();
            for (Class<?> member : type.getDeclaredClasses()) if (member.getSimpleName().equals(name)) {
                int mods = member.getModifiers();
                String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
                if (!java.lang.reflect.Modifier.isPrivate(mods) && (java.lang.reflect.Modifier.isPublic(mods)
                        || java.lang.reflect.Modifier.isProtected(mods) || type.getPackageName().equals(pkg))) {
                    found.add(member.getCanonicalName());
                }
            }
            if (found.isEmpty()) {
                if (type.getSuperclass() != null) found.addAll(inherited(type.getSuperclass().getCanonicalName(), name, active));
                for (Class<?> parent : type.getInterfaces()) found.addAll(inherited(parent.getCanonicalName(), name, active));
            }
            active.remove(base); return found;
        }
        for (String parent : parents.getOrDefault(base, Set.of())) {
            String candidate = parent + "." + name;
            String access = memberAccess.getOrDefault(candidate, "public");
            String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
            if (access.equals("private") || (access.startsWith("package:") && !access.equals("package:" + pkg))) continue;
            if (declared.contains(candidate)) found.add(candidate);
            else found.addAll(inherited(parent, name, active));
        }
        active.remove(base);
        return found;
    }
    static Main.IndexResult index(Path root) throws IOException {
        Main.IndexResult result = new Main.IndexResult();
        result.metadata.schema_version = "3.0";
        result.metadata.method_id_scheme = "java-source-v2";
        result.metadata.identity_resolution_runtime = System.getProperty("java.runtime.version");
        List<Path> paths;
        try (Stream<Path> stream = Files.walk(root.resolve("src/main/java"))) {
            paths = stream.filter(Files::isRegularFile).filter(p -> p.toString().endsWith(".java")).sorted().toList();
        }
        Map<Path, CompilationUnit> units = new LinkedHashMap<>();
        Map<Path, String> sourceTexts = new HashMap<>();
        Set<String> declarations = new HashSet<>();
        Map<String, Set<String>> parents = new HashMap<>();
        Map<String, String> access = new HashMap<>();
        for (Path path : paths) {
            if (!path.toRealPath().startsWith(root.toRealPath())) throw new IOException("Source outside repository");
            Main.FileInfo file = Main.parseJavaFile(Main.parser(), root, path);
            result.files.add(file);
            if (!file.parse_status.equals("PARSED")) continue;
            byte[] bytes = Files.readAllBytes(path);
            if (!Main.sha256(bytes).equals(file.source_sha256)) throw new IOException("Source changed during v2 parse");
            String source = StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString();
            sourceTexts.put(path, source);
            CompilationUnit cu = Main.parser().parse(source).getResult().orElseThrow();
            units.put(path, cu);
            for (TypeDeclaration<?> t : cu.findAll(TypeDeclaration.class)) {
                try {
                    String id = owner(t); declarations.add(id);
                    boolean interfaceMember = t.getParentNode().filter(n -> n instanceof ClassOrInterfaceDeclaration c && c.isInterface()).isPresent();
                    access.put(id, t.isPrivate() ? "private" : t.isPublic() || t.isProtected() || interfaceMember ? "public"
                            : "package:" + cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse(""));
                } catch (Unknown ignored) { }
            }
        }
        // Resolve only explicit source-declared supertypes, without inspecting
        // method implementations or inventing inaccessible inherited members.
        for (CompilationUnit cu : units.values()) for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            try {
                String base = owner(cls);
                MethodDeclaration lookup = new MethodDeclaration(); lookup.setParentNode(cls);
                MethodIdentityV2 resolver = new MethodIdentityV2(cu, lookup, declarations, Map.of(), access);
                Set<String> supers = new HashSet<>();
                for (ClassOrInterfaceType t : cls.getExtendedTypes()) supers.add(resolver.resolve(t.getNameWithScope()));
                for (ClassOrInterfaceType t : cls.getImplementedTypes()) supers.add(resolver.resolve(t.getNameWithScope()));
                parents.put(base, supers);
            } catch (Unknown ignored) {
                try { parents.put(owner(cls), Set.of("<UNRESOLVED_SUPERTYPE>")); } catch (Unknown unsupported) { }
            }
        }
        for (int f = 0; f < paths.size(); f++) {
            Main.FileInfo file = result.files.get(f);
            CompilationUnit cu = units.get(paths.get(f));
            if (cu == null) continue;
            Main.SourceMap map = new Main.SourceMap(sourceTexts.get(paths.get(f)), cu);
            int c = 0;
            for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
                int at = 0;
                for (MethodDeclaration method : cls.getMethods()) {
                    Main.MethodInfo m = file.classes.get(c).methods.get(at++);
                    String slice = map.slice(method);
                    if (slice != null) m.source_slice_sha256 = Main.sha256(slice.getBytes(StandardCharsets.UTF_8));
                    if (!m.identity_status.equals("AVAILABLE")) continue;
                    try {
                        m.identity_signature = new MethodIdentityV2(cu, method, declarations, parents, access).signature();
                        m.method_id = "java-source-v2:" + m.qualified_class_name + "#" + m.identity_signature;
                    } catch (Unknown unknown) {
                        m.identity_status = "UNAVAILABLE_TYPE_ERASURE"; m.method_id = null;
                        m.evidence_occurrences.clear(); continue;
                    }
                    for (Main.EvidenceOccurrence e : m.evidence_occurrences) {
                        e.evidence_id = "sha256:" + Main.sha256(Main.JSON.writeValueAsBytes(List.of("index-schema-3.0",
                            file.file, file.source_sha256, m.method_id, e.evidence_type,
                            e.start_line, e.start_column, e.end_line, e.end_column)));
                    }
                }
                c++;
            }
        }
        Main.detectIdentityConflicts(result);
        for (Main.FileInfo file : result.files) {
            result.metadata.parse_counts.merge(file.parse_status, 1, Integer::sum);
            for (Main.ClassInfo cls : file.classes) for (Main.MethodInfo m : cls.methods) {
                result.metadata.methods_total++;
                if (!m.identity_status.equals("AVAILABLE")) result.metadata.identity_unavailable_methods++;
                if (!m.location_status.equals("AVAILABLE")) result.metadata.location_unavailable_methods++;
                result.metadata.evidence_occurrences += m.evidence_occurrences.size();
            }
        }
        result.metadata.files_total = result.files.size();
        return result;
    }
}
