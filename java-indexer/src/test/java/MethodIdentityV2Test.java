import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MethodIdentityV2Test {
    @TempDir Path temp;
    Main.IndexResult index(String code) throws Exception {
        Path path=temp.resolve("src/main/java/A.java");
        Files.createDirectories(path.getParent()); Files.writeString(path,code);
        return MethodIdentityV2.index(temp);
    }
    List<Main.MethodInfo> methods(Main.IndexResult r) {
        return r.files.stream().flatMap(f->f.classes.stream()).flatMap(c->c.methods.stream()).toList();
    }
    @Test void boundedOverloads() throws Exception {
        var r=index("import java.util.Collection; import java.util.Map; class A { <T extends Collection<?>> void f(T x){} <T extends Map<?,?>> void f(T x){} <T extends CharSequence> void f(T x){} }");
        assertEquals(List.of("f(java.util.Collection)","f(java.util.Map)","f(java.lang.CharSequence)"),methods(r).stream().map(m->m.identity_signature).toList());
        assertTrue(methods(r).stream().allMatch(m->m.signature.equals("f(T)")));
        assertEquals(0,r.metadata.identity_conflict_methods);
    }
    @Test void scopeShadowAndIntersectionAndArrays() throws Exception {
        var ms=methods(index("class A<T extends Number> { void f(T[] x){} <T extends CharSequence & Comparable<T>> void g(T... x){} <U extends T> void h(U x){} }"));
        assertEquals(List.of("f(java.lang.Number[])","g(java.lang.CharSequence[])","h(java.lang.Number)"),ms.stream().map(m->m.identity_signature).toList());
    }
    @Test void classBoundDoesNotUseShadowingMethodVariable() throws Exception {
        var ms=methods(index("class A<T extends Number,U extends T> { <T extends CharSequence> void f(U x,T y){} }"));
        assertEquals("f(java.lang.Number,java.lang.CharSequence)",ms.get(0).identity_signature);
    }
    @Test void realErasureDuplicatesStayConflict() throws Exception {
        var r=index("import java.util.List; class A { void f(List<String> a){} void f(List<Integer> b){} }");
        assertEquals(2,r.metadata.identity_conflict_methods);
        assertTrue(methods(r).stream().allMatch(m->m.method_id==null && m.identity_status.equals("CONFLICT")));
    }
    @Test void varargsEqualsArrayAndUnboundedObject() throws Exception {
        var r=index("class A { <T> void f(T... a){} void f(Object[] b){} }");
        assertEquals(2,r.metadata.identity_conflict_methods);
    }
    @Test void cyclicAndUnresolvedTypesFailClosed() throws Exception {
        var ms=methods(index("class A<T extends T> { void f(T a){} void g(Unknown a){} }"));
        assertTrue(ms.stream().allMatch(m->m.identity_status.equals("UNAVAILABLE_TYPE_ERASURE") && m.method_id==null));
    }
    @Test void ambiguousOnDemandImportsFailClosed() throws Exception {
        var ms=methods(index("import java.sql.*; import java.util.*; class A { void f(Date d){} }"));
        assertNull(ms.get(0).method_id);
    }
    @Test void v1IsUnchangedAndV2EvidenceHasNewDomain() throws Exception {
        index("class A { void f(String s){ s.trim(); } }");
        var old=Main.indexProject(temp); var fresh=MethodIdentityV2.index(temp);
        assertEquals("2.0",old.metadata.schema_version); assertEquals("3.0",fresh.metadata.schema_version);
        var before=methods(old).get(0); var after=methods(fresh).get(0);
        assertEquals(before.signature,after.signature); assertEquals(before.calls,after.calls);
        assertNotEquals(before.method_id,after.method_id);
        assertNotEquals(before.evidence_occurrences.get(0).evidence_id,after.evidence_occurrences.get(0).evidence_id);
        assertFalse(Main.JSON.writeValueAsString(before).contains("identity_signature"));
    }
    @Test void staticMethodCannotCaptureClassTypeParameter() throws Exception {
        var ms=methods(index("class A<T> { static void f(T t){} static <T> void g(T t){} }"));
        assertNull(ms.get(0).method_id); assertEquals("g(java.lang.Object)",ms.get(1).identity_signature);
    }
    @Test void inheritedSourceMemberType() throws Exception {
        var ms=methods(index("class Base { protected enum State { OPEN } } class A extends Base { void f(State s){} }"));
        assertEquals("f(Base.State)",ms.get(0).identity_signature);
    }
    @Test void inaccessibleInheritedTypeFailsClosed() throws Exception {
        var ms=methods(index("class Base { private enum State { OPEN } } class A extends Base { void f(State s){} }"));
        assertNull(ms.get(0).method_id);
    }
    @Test void implicitStaticInterfaceMemberCannotCaptureOuterVariable() throws Exception {
        var ms=methods(index("interface Outer<T> { class Nested { void f(T t){} } }"));
        assertNull(ms.get(0).method_id);
    }
    @Test void namedBoundUsesItsDeclarationScopeNotInnerMemberShadow() throws Exception {
        var ms=methods(index("class Outer<T extends Bound> { class Bound {} class Inner { class Bound {} void f(T t){} } }"));
        assertEquals("f(Outer.Bound)",ms.get(0).identity_signature);
    }
    @Test void unresolvedExternalHierarchyCannotGuessImportedType() throws Exception {
        var ms=methods(index("import external.Base; class A extends Base { void f(String s){} }"));
        assertNull(ms.get(0).method_id);
    }
    @Test void jdkInheritedMemberTypeUsesPlatformMetadata() throws Exception {
        var ms=methods(index("class A extends Thread { void f(State s){} }"));
        assertEquals("f(java.lang.Thread.State)",ms.get(0).identity_signature);
    }
    @Test void v2KeepsBomAndDangerousUnicodeUnavailable() throws Exception {
        for(String text:List.of("\ufeffclass A { void f(){} }", "class A { void f(){String s=\"\\u000a\";} }")) {
            var ms=methods(index(text));
            assertTrue(ms.stream().noneMatch(m->m.location_status.equals("AVAILABLE")));
        }
    }
}
