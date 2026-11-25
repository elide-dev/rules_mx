package com.oracle.mxtool.junit;

import com.google.testing.junit.runner.BazelTestRunner;
import java.util.Set;
import java.util.TreeSet;

/** Apply mx module access before Bazel's JUnit runner inspects test methods. */
public final class CompilerJUnit {
    public static void main(String[] args) throws Exception {
        ModuleSupport modules = new ModuleSupport(System.out);
        Set<String> opened = new TreeSet<>();
        Set<String> exported = new TreeSet<>();
        for (String spec : new String[]{
                "jdk.internal.vm.ci/*=org.graalvm.truffle.runtime,jdk.graal.compiler,ALL-UNNAMED",
                "jdk.graal.compiler/*", "org.graalvm.*/*"}) {
            modules.openPackages(spec, CompilerJUnit.class, opened, exported);
        }
        Class<?> test = Class.forName(System.getProperty("bazel.test_suite"), false, CompilerJUnit.class.getClassLoader());
        modules.processAddModulesAnnotations(Set.of(test));
        modules.processAddExportsAnnotations(Set.of(test), opened, exported);
        System.setProperty(MxJUnitWrapper.OPENED_PACKAGES_PROPERTY_NAME, String.join(System.lineSeparator(), opened));
        System.setProperty(MxJUnitWrapper.EXPORTED_PACKAGES_PROPERTY_NAME, String.join(System.lineSeparator(), exported));
        BazelTestRunner.main(args);
    }
}
