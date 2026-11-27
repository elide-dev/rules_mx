# Elide's Darwin ThinLTO can discard public C API symbols referenced only by
# separately loaded extensions. Keep native optimization on those extensions.
cmake_language(DEFER CALL set_property TARGET python-native PROPERTY INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF)
