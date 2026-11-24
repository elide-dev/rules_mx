#!/bin/sh
# Invoked by CcToolchainInfo and GraalVM Native Image, including on RBE.
set -eu
here=${0%/*}
unset SDKROOT
exec "$here/bin/clang" --no-default-config -Qunused-arguments @DRIVER_FLAGS@ "$@"
