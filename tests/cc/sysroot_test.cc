#include <sstream>
#include <string>
#include <sys/utsname.h>

#ifndef _LIBCPP_VERSION
#error "The Elide toolchain must supply libc++"
#endif

int main() {
    struct utsname system;
    if (uname(&system) != 0) {
        return 1;
    }
    std::ostringstream platform;
    platform << system.sysname;
    return platform.str().empty();
}
