#!/usr/bin/env bash
#==============================================================================
# One-time setup of a free, no-sudo toolchain for the Verilator flow.
# Run it inside Linux / WSL2 (x86-64):      ./setup_toolchain.sh
#
# It needs network access to github.com (micromamba) and conda.anaconda.org (about 280 MB of
# conda-forge packages) and installs into ~/eda only: nothing system-wide, no root needed.
#   ~/eda/bin/micromamba     single-file package manager (from the mamba-org GitHub releases)
#   ~/eda/mamba/envs/sim     conda-forge packages: Verilator, GCC, make, z3 (the constraint
#                            solver behind randomize()), zlib + lz4 (FST waveforms), python
#   ~/eda/env.sh             "source ~/eda/env.sh" puts the tools on PATH
#
# Options (environment variables):
#   EDA_HOME=<dir>           install somewhere else than ~/eda. Keep it exported afterwards:
#                            run.sh, the Makefile and mutation/run_mutation.sh look there too.
#   VERILATOR_VERSION=5.054  Verilator version to install (this testbench needs 5.054 or newer;
#                            5.054 is the only version it was verified with)
#   WITH_GTKWAVE=1           also install the GTKWave waveform viewer
#
# Disk: about 1.7 GB with GTKWave, a little less without (the download cache is removed at
# the end). Uninstall: rm -rf ~/eda
#==============================================================================
set -euo pipefail

EDA="${EDA_HOME:-$HOME/eda}"
VERILATOR_VERSION="${VERILATOR_VERSION:-5.054}"
PKGS=("verilator=${VERILATOR_VERSION}" gxx gcc make libz3 z3-solver zlib lz4-c python)
[ "${WITH_GTKWAVE:-0}" = "1" ] && PKGS+=(gtkwave)

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ;;
    *) echo "This script supports Linux x86-64 (including WSL2) only." >&2; exit 1 ;;
esac

mkdir -p "$EDA/bin"
if [ ! -x "$EDA/bin/micromamba" ]; then
    echo "[setup] downloading micromamba ..."
    curl -fsSL -o "$EDA/bin/micromamba" \
        https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64
    chmod +x "$EDA/bin/micromamba"
fi

export MAMBA_ROOT_PREFIX="$EDA/mamba"
ENV_DIR="$EDA/mamba/envs/sim"
if [ -x "$ENV_DIR/bin/verilator" ]; then
    echo "[setup] environment already exists: $ENV_DIR (installing any missing packages)"
    "$EDA/bin/micromamba" install -y -n sim -c conda-forge "${PKGS[@]}"
else
    echo "[setup] creating environment $ENV_DIR (downloads about 280 MB) ..."
    "$EDA/bin/micromamba" create -y -n sim -c conda-forge "${PKGS[@]}"
fi
# The downloaded packages are no longer needed once they are installed
"$EDA/bin/micromamba" clean --all --yes > /dev/null 2>&1 || true

cat > "$EDA/env.sh" <<EOS
# Source this file to use the no-sudo EDA toolchain (Verilator + GCC + make + z3) installed in $EDA
#   source $EDA/env.sh
export CONDA_PREFIX="$ENV_DIR"
export PATH="\$CONDA_PREFIX/bin:\$PATH"
EOS

# shellcheck disable=SC1091
source "$EDA/env.sh"
echo "[setup] installed:"
verilator --version
g++ --version | head -1
make --version | head -1
z3 --version
echo
echo "[setup] done. Next:"
echo "    ./run.sh regress"
echo "  (or:  source $EDA/env.sh  and then  make regress)"
