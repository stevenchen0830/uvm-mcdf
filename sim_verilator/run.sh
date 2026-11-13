#!/usr/bin/env bash
# Convenience wrapper for the Verilator flow: loads the toolchain installed by
# setup_toolchain.sh, then runs make in this directory with the arguments you give, e.g.
#     ./run.sh                    # compile + run case0
#     ./run.sh regress            # run case0 case1 case2
#     ./run.sh TESTNAME=case2 DUMP=1
#     ./run.sh COV=1 regress cov
#     ./run.sh help
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the toolchain from setup_toolchain.sh (a known good Verilator) over whatever else is
# on PATH. If it was installed somewhere else, keep EDA_HOME exported.
eda="${EDA_HOME:-$HOME/eda}"
if [ -f "$eda/env.sh" ]; then
    # shellcheck disable=SC1091
    source "$eda/env.sh"
elif ! command -v verilator >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
    echo "Toolchain not found. Run ./setup_toolchain.sh once (see README.md)." >&2
    exit 1
fi
cd "$here" && exec make "$@"
