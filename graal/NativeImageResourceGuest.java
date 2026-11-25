package graal;

import com.oracle.svm.with.space.test.NativeImageResourceTest;

public final class NativeImageResourceGuest {
    public static void main(String[] args) {
        new NativeImageResourceTest().accessFileFromJarWithSpaceInName();
    }
}
