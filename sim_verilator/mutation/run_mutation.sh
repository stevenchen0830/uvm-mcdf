#!/usr/bin/env bash
#==============================================================================
# Mutation testing for the uvm-mcdf testbench (Verilator flow).
#
# Each mutant is a private copy of the project in which ONE small bug is injected into the
# DUT with sed (see mutants.txt). The regression (all tests, with a fixed seed) is run on
# every mutant. A mutant is KILLED when at least one test fails, i.e. the testbench notices
# the bug; a SURVIVED mutant shows a hole in the verification.
# Nothing in the real project is modified.
#
#   mutation/run_mutation.sh                 run all mutants
#   mutation/run_mutation.sh m03 m10         run the mutants whose name starts with m03 / m10
#
# Environment:
#   PARALLEL=2   mutants built at the same time (one build needs several GB of memory)
#   SEED=1       seed used for every simulation
#   TESTS="..."  tests to run on every mutant (default: case0 case1 case2 case3 case4)
#   WORK=<dir>   scratch directory (default ~/.cache/uvm-mcdf-mutation)
#
# Takes roughly one minute per mutant. Result of a full run: mutation/mutation_report.txt
# (a run of selected mutants writes $WORK/mutation_report_partial.txt instead).
#==============================================================================
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prj="$(cd "$here/../.." && pwd)"

# Prefer the toolchain installed by setup_toolchain.sh over whatever else is on PATH
eda="${EDA_HOME:-$HOME/eda}"
if [ -f "$eda/env.sh" ]; then
    # shellcheck disable=SC1091
    source "$eda/env.sh"
elif ! command -v verilator >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
    echo "Toolchain not found. Run ../setup_toolchain.sh once." >&2
    exit 1
fi

PARALLEL="${PARALLEL:-2}"
SEED="${SEED:-1}"
WORK="${WORK:-$HOME/.cache/uvm-mcdf-mutation}"
TESTS="${TESTS:-case0 case1 case2 case3 case4}"
# The tests that came with the testbench. The report also says how many mutants these alone
# detect, which shows what the tests added later (case3, case4) are worth.
BASE_TESTS="case0 case1 case2"
# compile jobs per mutant: share the CPUs and the free memory (about 1.5 GB per job)
cpus=$(nproc)
mem_jobs=$(awk '/MemAvailable/ {print int($2/1500000)}' /proc/meminfo)
JOBS=$(( (cpus < mem_jobs ? cpus : mem_jobs) / PARALLEL ))
[ "$JOBS" -lt 1 ] && JOBS=1
mkdir -p "$WORK"
export prj WORK SEED JOBS TESTS

trim() { sed 's/^[[:space:]]*//; s/[[:space:]]*$//' <<< "$1"; }

# Prints one line per mutant:  name|verdict|description|result of test 1|result of test 2|...
run_one() {
    local line="$1" name file expr desc dir before after res="" killed=0 t log v ids cmp why
    IFS='|' read -r name file expr desc <<< "$line"
    name="$(trim "$name")"; file="$(trim "$file")"; expr="$(trim "$expr")"; desc="$(trim "$desc")"
    if [ -z "$name" ] || [ -z "$file" ] || [ -z "$expr" ]; then
        printf '%s|NOT_APPLIED|malformed line in mutants.txt\n' "${name:-?}"
        return
    fi
    dir="$WORK/$name"
    rm -rf "$dir"
    mkdir -p "$dir/sim" "$dir/sim_verilator"
    cp -r "$prj/dut" "$prj/testbench" "$dir/"
    cp "$prj/sim/rtl.list" "$dir/sim/"
    cp -r "$prj/sim_verilator/Makefile" "$prj/sim_verilator/mcdf.vlt" "$prj/sim_verilator/uvm" "$dir/sim_verilator/"
    sed -i 's/\r$//' "$dir"/dut/*.v
    before=$(md5sum < "$dir/dut/$file")
    sed -i "$expr" "$dir/dut/$file"
    after=$(md5sum < "$dir/dut/$file")
    if [ "$before" = "$after" ]; then
        printf '%s|NOT_APPLIED|%s\n' "$name" "$desc"
        return
    fi
    ( cd "$dir/sim_verilator" && make regress TESTS="$TESTS" SEED="$SEED" JOBS="$JOBS" OPT=-O0 WALL_LIMIT=600 BUILD_ROOT="$dir/build" > regress.log 2>&1 )
    if [ ! -x "$dir/build/obj_nocov/simv" ]; then
        # No model: the mutant does not compile or the build was killed (out of memory).
        # That says nothing about the testbench, so it is neither KILLED nor SURVIVED.
        printf '%s|BUILD_FAILED|%s (see %s)\n' "$name" "$desc" "$dir/sim_verilator/compile_nocov.log"
        rm -rf "$dir/build"
        return
    fi
    for t in $TESTS; do
        log="$dir/sim_verilator/${t}_sim_dir/sim_$t.log"
        if [ ! -f "$log" ]; then res="$res|FAIL: no log"; killed=1; continue; fi
        cmp=$(sed -n 's/^total comparison count: *\([0-9]*\).*/\1/p' "$log")
        ids=$(grep -h -o -E '^UVM_(ERROR|FATAL) [^ ]+ @ [0-9.]+ns: [^ ]+ \[+[^]]*\]+' "$log" \
              | sed -E 's/.*\[+([^]]*)\]+$/\1/' | sort | uniq -c | awk '{printf "%s x%s ", $2, $1}')
        # the verdict is the one of the "sim" target of the Makefile
        if grep -q "^\[sim\] $t PASSED" "$dir/sim_verilator/regress.log"; then
            v="pass (${cmp:-?} compared)"
        else
            why=$(sed -n "s/^\[sim\] $t FAILED: \(.*\), see .*/\1/p" "$dir/sim_verilator/regress.log" | head -1)
            v="FAIL: ${ids:-${why:-unknown reason}}"; killed=1
        fi
        res="$res|$v"
    done
    if [ $killed -eq 1 ]; then v=KILLED; else v=SURVIVED; fi
    printf '%s|%s|%s%s\n' "$name" "$v" "$desc" "$res"
    rm -rf "$dir/build"
}
export -f run_one trim

mapfile -t all < <(grep -v -E '^[[:space:]]*(#|$)' "$here/mutants.txt")
sel=()
if [ $# -eq 0 ]; then
    sel=("${all[@]}")
    report="$here/mutation_report.txt"
else
    for line in "${all[@]}"; do
        name="$(trim "${line%%|*}")"
        for want in "$@"; do
            case "$name" in "$want"*) sel+=("$line") ;; esac
        done
    done
    report="$WORK/mutation_report_partial.txt"      # keep the report of the last full run
fi
if [ ${#sel[@]} -eq 0 ]; then
    echo "no mutant selected"
    exit 1
fi

echo "Running ${#sel[@]} mutant(s), $PARALLEL at a time, $JOBS compile jobs each, seed $SEED ..."
raw="$WORK/results.raw"
printf '%s\n' "${sel[@]}" | xargs -P "$PARALLEL" -d '\n' -I{} bash -c 'run_one "$@"' _ {} | sort > "$raw"

count() { awk -F'|' -v v="$1" '$2 == v { n++ } END { print n + 0 }' "$raw"; }
{
    echo "Mutation test report   (seed $SEED, $(date '+%Y-%m-%d %H:%M'))"
    echo "tests: $TESTS"
    echo
    awk -F'|' -v tests="$TESTS" 'BEGIN { split(tests, t, " ") }
        { printf "%-26s %-12s %s\n", $1, $2, $3
          for (i = 4; i <= NF; i++) printf "    %s: %s\n", t[i - 3], $i }' "$raw"
    echo
    k=$(count KILLED); s=$(count SURVIVED); n=$(count NOT_APPLIED); b=$(count BUILD_FAILED)
    echo "killed: $k   survived: $s   not applied: $n   build failed: $b   (mutation score: $k / $((k + s)))"
    # how many mutants every test detects, and how many the original tests detect together
    awk -F'|' -v tests="$TESTS" -v base="$BASE_TESTS" '
        BEGIN { n = split(tests, t, " "); nb = split(base, bt, " ")
                for (i = 1; i <= nb; i++) isbase[bt[i]] = 1
                for (i = 1; i <= n; i++) if (t[i] in isbase) have++ }
        $2 == "KILLED" || $2 == "SURVIVED" {
            total++; hit = 0
            for (i = 4; i <= NF; i++) if ($i ~ /^FAIL/) { c[i - 3]++; if (t[i - 3] in isbase) hit = 1 }
            if (hit) basekill++ }
        END { printf "detected by:"
              for (i = 1; i <= n; i++) printf "   %s %d", t[i], c[i]
              printf "\n"
              if (have == nb && n > nb) printf "detected by %s alone: %d / %d\n", base, basekill, total }' "$raw"
} | tee "$report"
echo "report written to $report"
