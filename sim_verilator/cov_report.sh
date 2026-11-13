#!/usr/bin/env bash
# Merge the per-test coverage databases written by "make COV=1 ..." (coverage/<test>.dat)
# and write the reports into coverage/report/:
#   coverage_report.html   self-contained HTML report (code + functional coverage, annotated DUT source)
#   coverage_report.txt    the same summary as plain text
#   verilator_coverage.txt summary + per-instance report printed by verilator_coverage
#   annotate/              DUT sources annotated by verilator_coverage
#   merged.dat, merged.info  merged database (Verilator format / lcov format)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
shopt -s nullglob
dats=(coverage/*.dat)
if [ ${#dats[@]} -eq 0 ]; then
    echo "No coverage data in coverage/. Run:  make COV=1 regress"
    exit 1
fi
out=coverage/report
rm -rf "$out"
mkdir -p "$out"

verilator_coverage --write "$out/merged.dat" "${dats[@]}"
verilator_coverage --report summary,hier "$out/merged.dat" > "$out/verilator_coverage.txt"
verilator_coverage --annotate "$out/annotate" --annotate-points "$out/merged.dat" > /dev/null
verilator_coverage --write-info "$out/merged.info" "$out/merged.dat" > /dev/null

tests=()
for d in "${dats[@]}"; do
    tests+=(--test "$(basename "$d" .dat)=$d")
done
python3 cov_report.py --merged "$out/merged.dat" "${tests[@]}" --out "$out"
