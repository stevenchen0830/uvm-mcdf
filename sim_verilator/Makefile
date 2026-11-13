#==============================================================================
# uvm-mcdf : open-source simulation flow  (Verilator + UVM 1800.2-2020.3.2)
#
# Usage mirrors the original VCS flow in ../sim/Makefile:
#
#   make                          compile + run case0
#   make TESTNAME=case1           run another test (recompiles only if something changed)
#   make sim TESTNAME=case2       same as above (the model is built first when needed)
#   make regress                  run all tests (case0 .. case4) and print a pass/fail table
#   make COV=1 regress cov        coverage build, run all tests, merge + report
#                                 (report: coverage/report/coverage_report.html)
#   make DUMP=1 TESTNAME=case0    also write case0_sim_dir/case0.fst (GTKWave / Surfer)
#   make SEED=1234 TESTNAME=case2 reproduce a run (default: a new random seed each run)
#   make VERBOSITY=UVM_HIGH       more UVM messages
#   make check                    cross-check the last logs against an independent model
#   make clean | make cleanall    remove the compiled model | also logs, waves, coverage
#   make help                     print this text
#
# One compiled model (simv) serves every test: the test is picked at run time with
# +UVM_TESTNAME. COV=1 uses a second model built with coverage instrumentation.
#==============================================================================
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c

TESTNAME  ?= case0
TESTS     ?= case0 case1 case2 case3 case4
COV       ?= 0
DUMP      ?= 0
VERBOSITY ?= UVM_MEDIUM
SEED      ?=
# Parallel C++ compile jobs: one per CPU, but no more than the free memory allows
# (one compile job of the generated UVM code needs roughly 1.5 GB)
JOBS      ?= $(shell awk -v n=$$(nproc) '/MemAvailable/ {m=int($$2/1500000); if (m<1) m=1; print (n<m)?n:m}' /proc/meminfo)
# C++ optimisation of the generated model: -O0 compiles fastest, -O1/-O2 simulate faster
OPT       ?= -O1
# Wall-clock limit of one simulation in seconds (0 = no limit). It protects against a hung
# test: the UVM watchdog (+UVM_TIMEOUT) counts simulated time, and 1 s of it takes hours here.
WALL_LIMIT ?= 1800
# A test must make at least this many scoreboard comparisons to count as passed. The
# testbench itself only looks at the number of UVM_ERRORs, so a run in which no packet was
# compared at all would still print "TestCase Passed". Use MIN_COMPARE=0 for a test of your
# own that sends no channel data.
MIN_COMPARE ?= 1
# Oldest Verilator that runs this testbench correctly (UVM support, clocking-block drive fix)
VERILATOR_MIN := 5.054

#------------------------------------------------------------------------------
# Tools: use what is on PATH, otherwise the no-sudo toolchain from setup_toolchain.sh
#------------------------------------------------------------------------------
EDA_ENV ?= $(or $(EDA_HOME),$(HOME)/eda)/mamba/envs/sim
ifeq ($(shell command -v verilator 2>/dev/null),)
  ifneq ($(wildcard $(EDA_ENV)/bin/verilator),)
    export PATH := $(EDA_ENV)/bin:$(PATH)
  endif
endif

#------------------------------------------------------------------------------
# Build directory: always on the Linux file system and outside the project.
#  - compiling ~3000 generated C++ files on a Windows drive seen from WSL (/mnt/c/...) is
#    very slow;
#  - make cannot handle a build path that contains blanks, whereas the project itself may
#    live in such a directory ("C:\Users\me\My Project\...").
# Every project location gets its own directory (named after a hash of its path);
# "make clean" removes it.
#------------------------------------------------------------------------------
PRJ_KEY := $(shell p=$$(realpath .); case "$$p" in (/mnt/?/*) p=$$(printf '%s' "$$p" | tr 'A-Z' 'a-z');; esac; printf '%s' "$$p" | md5sum | cut -c1-8)
BUILD_ROOT ?= $(HOME)/.cache/uvm-mcdf-verilator/$(PRJ_KEY)
ifneq ($(words $(BUILD_ROOT)),1)
  $(error The build directory "$(BUILD_ROOT)" contains a blank. Choose another one, e.g.  make BUILD_ROOT=/tmp/mcdf-build)
endif

ifeq ($(COV),1)
  CFG      := cov
  COV_COMP_OPT = --coverage +define+COV_EN
  COV_SIM_OPT  = +verilator+coverage+file+coverage/$(TESTNAME).dat
else
  CFG      := nocov
  COV_COMP_OPT =
  COV_SIM_OPT  =
endif
OBJ_DIR := $(BUILD_ROOT)/obj_$(CFG)
SIMV    := $(OBJ_DIR)/simv

#------------------------------------------------------------------------------
# Sources (same include directories and RTL list as ../sim/Makefile)
#------------------------------------------------------------------------------
UVM_HOME ?= uvm

INC_DIR  = +incdir+. +incdir+$(UVM_HOME)
INC_DIR += +incdir+../testbench/interface/
INC_DIR += +incdir+../testbench/component/case
INC_DIR += +incdir+../testbench/component/transaction/
INC_DIR += +incdir+../testbench/component/sequencer/
INC_DIR += +incdir+../testbench/component/driver/
INC_DIR += +incdir+../testbench/component/monitor/
INC_DIR += +incdir+../testbench/component/agent/
INC_DIR += +incdir+../testbench/component/model/
INC_DIR += +incdir+../testbench/component/scoreboard/
INC_DIR += +incdir+../testbench/component/env/
INC_DIR += +incdir+../testbench/component/register/
INC_DIR += +incdir+../testbench/component/coverage/

# Compile order matters, exactly as in the VCS flow: UVM package first (it defines the UVM
# macros), then the RTL list (param_def.v defines the widths used by interface.sv), then tb_top.
UVM_SRC  = $(UVM_HOME)/uvm_pkg_all_v2020_3_2_dpi.svh
RTL_SRC  = -f ../sim/rtl.list
TB_TOP   = ../testbench/tb_top.sv
# The C sources of the UVM DPI library are compiled by the generated makefile, which runs
# inside the build directory and therefore needs an absolute path without blanks: they are
# copied next to the build first.
UVM_DPI_DIR := $(BUILD_ROOT)/uvm_dpi
UVM_DPI  = $(UVM_DPI_DIR)/uvm_dpi.cc

DEPS := $(wildcard ../dut/*.v ../testbench/*.sv ../testbench/*/*.sv ../testbench/component/*/*.sv) \
        $(wildcard $(UVM_HOME)/*.svh $(UVM_HOME)/dpi/*) ../sim/rtl.list mcdf.vlt Makefile

#------------------------------------------------------------------------------
# Compile options
#------------------------------------------------------------------------------
COMP_OPT  = --binary                          # build an executable (implies --timing: #delays, @events, fork)
COMP_OPT += -j $(JOBS)                        # parallel build
COMP_OPT += --timescale 1ns/1ns               # default timescale, like "vcs -timescale=1ns/1ns"
COMP_OPT += --vpi                             # UVM DPI code (register backdoor) uses VPI
COMP_OPT += --trace-fst                       # waveform support; dumping is enabled at run time (+DUMP)
# Type coverage of a covergroup computed over merged instances (IEEE 1800 19.11.3 with
# type_option.merge_instances = 1). Only then does get_coverage() honour the
# "type_option.weight = 0" of the coverpoints that merely feed a cross, which is what the
# coverage model in coverage_pkg.sv intends.
COMP_OPT += --coverage-merge-instances
COMP_OPT += -Wno-fatal                        # a lint warning does not stop the build
COMP_OPT += -CFLAGS "$(OPT)"
COMP_OPT += --top-module tb_top --Mdir $(OBJ_DIR) -o simv

#------------------------------------------------------------------------------
# Simulation options
#------------------------------------------------------------------------------
# No SEED given -> pick a random one every run (like vcs +ntb_random_seed_automatic)
ifeq ($(strip $(SEED)),0)
  $(error SEED=0 means "pick a random seed" to Verilator, so the run could not be reproduced. Use SEED=1 .. 2147483647, or leave SEED out for a new random seed)
endif
ifeq ($(strip $(SEED)),)
  RUN_SEED = $$(( ( $$(od -An -N4 -tu4 /dev/urandom) % 2147483646 ) + 1 ))
else
  RUN_SEED = $(SEED)
endif

SIM_OPT  = +UVM_TESTNAME=$(TESTNAME) +TESTNAME=$(TESTNAME)
SIM_OPT += +UVM_VERBOSITY=$(VERBOSITY)
SIM_OPT += +UVM_TIMEOUT="1000000000ns,YES"    # watch dog time out
SIM_OPT += +UVM_NO_RELNOTES
#SIM_OPT += +UVM_OBJECTION_TRACE              # debug: trace objection raise or drop
ifeq ($(DUMP),1)
  SIM_OPT += +DUMP
endif

SIM_LOG = $(TESTNAME)_sim_dir/sim_$(TESTNAME).log

#------------------------------------------------------------------------------
# Targets
#------------------------------------------------------------------------------
.PHONY: all comp sim regress cov check wave help clean cleanall check_tools
# goals given together ("make COV=1 regress cov") run in the order written, also under "make -j"
# (the C++ build is parallel anyway: verilator -j $(JOBS))
.NOTPARALLEL:

all: comp sim

check_tools:
	@command -v verilator >/dev/null || { echo "ERROR: verilator not found. Run ./setup_toolchain.sh once, then use ./run.sh (see README.md)."; exit 1; }
	@v=$$(verilator --version | awk '{print $$2}'); \
	[ "$$(printf '%s\n' $(VERILATOR_MIN) "$$v" | sort -V | head -1)" = $(VERILATOR_MIN) ] || { \
		echo "ERROR: Verilator $$v ($$(command -v verilator)) is too old: $(VERILATOR_MIN) or newer is needed."; \
		echo "       Use ./run.sh, or first:  source ~/eda/env.sh"; exit 1; }
	@command -v z3 >/dev/null || { echo "ERROR: z3 not found on PATH (the constraint solver behind randomize()). Use ./run.sh, or first:  source ~/eda/env.sh"; exit 1; }

comp: check_tools $(SIMV)

# Signature of the build options and of the size + time stamp of every source. The file is
# rewritten only when the signature changes, so the model is also rebuilt after another OPT
# or COV setting and after a source was replaced by a file with an OLDER time stamp (unzip,
# cp -p, git checkout), which make's "newer than" test alone would not notice.
SIG_FILE := $(OBJ_DIR)/sources.sig
$(shell mkdir -p $(OBJ_DIR); s='$(OPT) $(COV_COMP_OPT) '"$$(ls -l --time-style=+%s $(DEPS) 2>/dev/null | md5sum)"; \
        [ "$$s" = "$$(cat $(SIG_FILE) 2>/dev/null)" ] || echo "$$s" > $(SIG_FILE))

$(SIMV): $(DEPS) $(SIG_FILE)
	@mkdir -p $(OBJ_DIR) $(UVM_DPI_DIR)
	@cp -pu $(UVM_HOME)/dpi/* $(UVM_DPI_DIR)/
	@echo "[comp] building the simulation model with Verilator; this takes a minute or more and prints nothing meanwhile"
	@echo "[comp] model: $(SIMV)   log: compile_$(CFG).log"
	@verilator $(COMP_OPT) $(COV_COMP_OPT) $(INC_DIR) mcdf.vlt \
		$(UVM_SRC) $(UVM_DPI) $(RTL_SRC) $(TB_TOP) > compile_$(CFG).log 2>&1 \
		|| { grep -n -A8 '^%Error' compile_$(CFG).log | head -60; echo "[comp] FAILED, see compile_$(CFG).log"; exit 1; }
	@touch $(SIMV)
	@grep -h 'Verilator: Walltime' compile_$(CFG).log | sed 's/^- /[comp] /'
	@n=$$(grep -c '^%Warning' compile_$(CFG).log); \
	if [ $$n -gt 0 ]; then echo "[comp] $$n lint warning(s), see compile_$(CFG).log"; fi

# A test passes when all of this holds:
#   - the simulator exits normally within WALL_LIMIT seconds
#   - the testbench prints "TestCase Passed" (base_test::report_phase: no UVM_ERROR) and no
#     UVM_FATAL was reported
#   - no randomize() call failed and the simulator itself printed no %Error / %Warning
#     (a missing or broken constraint solver would otherwise give a run without stimulus
#     that still "passes")
#   - the scoreboard compared at least MIN_COMPARE packets
# The first line of the log records the seed.
sim: check_tools $(SIMV)
	@mkdir -p $(TESTNAME)_sim_dir coverage
	@seed=$(RUN_SEED); \
	echo "[sim] $(TESTNAME)  seed=$$seed  (log: $(SIM_LOG))"; \
	{ echo "[sim] $(TESTNAME) seed=$$seed   (reproduce with: make sim TESTNAME=$(TESTNAME) SEED=$$seed)"; \
	  timeout $(WALL_LIMIT) $(SIMV) $(SIM_OPT) $(COV_SIM_OPT) +verilator+seed+$$seed; } > $(SIM_LOG) 2>&1; rc=$$?; \
	sed -n '/Compare Summary/,/^\*\*\*\*/p;/COVERAGE SUMMARY/,/^----/p' $(SIM_LOG); \
	grep -E '^UVM_(WARNING|ERROR|FATAL) :' $(SIM_LOG); \
	cmp=$$(sed -n 's/^total comparison count: *\([0-9]*\).*/\1/p' $(SIM_LOG)); \
	why=""; \
	if [ $$rc -eq 124 ]; then why="still running after $(WALL_LIMIT) s (WALL_LIMIT)"; \
	elif [ $$rc -ne 0 ]; then why="the simulator exited with code $$rc"; \
	elif ! grep -q "TestCase Passed: $(TESTNAME)" $(SIM_LOG); then why="no \"TestCase Passed\" in the log"; \
	elif ! grep -q '^UVM_FATAL : *0$$' $(SIM_LOG); then why="UVM_FATAL reported"; \
	elif grep -q -E 'Unable to communicate with SAT solver|\[RNDFLD\]|\[STRDEFSEQ\]' $(SIM_LOG); then why="constrained randomisation failed (is z3 on PATH? use ./run.sh)"; \
	elif grep -q -E '^%(Error|Warning)' $(SIM_LOG); then why="the simulator printed %Error / %Warning"; \
	elif [ "$${cmp:-0}" -lt $(MIN_COMPARE) ]; then why="the scoreboard compared $${cmp:-0} packet(s), fewer than MIN_COMPARE=$(MIN_COMPARE)"; \
	fi; \
	if [ -z "$$why" ]; then \
		echo "[sim] $(TESTNAME) PASSED  ($${cmp:-0} packets compared)"; \
	else \
		echo "[sim] $(TESTNAME) FAILED: $$why, see $(SIM_LOG)"; exit 1; \
	fi

# Run every test in $(TESTS); keep going after a failure and summarise at the end
regress: check_tools $(SIMV)
	@fail=0; rows=""; for t in $(TESTS); do \
		if $(MAKE) --no-print-directory sim TESTNAME=$$t COV=$(COV) DUMP=$(DUMP) SEED=$(SEED) VERBOSITY=$(VERBOSITY); \
		then r=PASSED; else r=FAILED; fail=1; fi; \
		rows="$$rows$$(printf '  %-8s %s   (%s)' $$t $$r "$$(grep -h 'total comparison count' $${t}_sim_dir/sim_$$t.log 2>/dev/null | tr -d '\r')")"$$'\n'; \
	done; \
	echo "==================== regression summary ===================="; \
	printf '%s' "$$rows"; \
	echo "============================================================"; \
	exit $$fail

# Merge the per-test coverage databases and write reports (needs runs made with COV=1)
cov: check_tools
	@ls coverage/*.dat >/dev/null 2>&1 || { echo "No coverage data. Run:  make COV=1 regress"; exit 1; }
	@bash ./cov_report.sh

# Cross-check the logs of the last case0/case1/case2 runs (the three original tests, the
# model does not know the others) against an independent cycle-level
# Python model of the DUT and the testbench timing (oracle/): comparison counts, register
# bus activity, the time stamp of every scoreboard comparison and the end-of-test time.
check:
	@fail=0; for t in case0 case1 case2; do \
		echo "== $$t"; \
		if [ -f $${t}_sim_dir/sim_$$t.log ]; then \
			python3 oracle/check_log_against_oracle.py $$t $${t}_sim_dir/sim_$$t.log || fail=1; \
		else echo "  no log ($${t}_sim_dir/sim_$$t.log). Run:  make regress"; fail=1; fi; \
	done; exit $$fail

# Open the waveform of $(TESTNAME) (needs a run made with DUMP=1)
wave:
	@f=$(TESTNAME)_sim_dir/$(TESTNAME).fst; [ -f $$f ] || { echo "$$f not found. Run:  make DUMP=1 TESTNAME=$(TESTNAME)"; exit 1; }; \
	if command -v gtkwave >/dev/null; then gtkwave $$f & \
	elif command -v surfer >/dev/null; then surfer $$f & \
	else \
		echo "No waveform viewer found here. Open this file with Surfer (drag it into https://app.surfer-project.org) or with GTKWave:"; \
		echo "  $$(wslpath -w "$$PWD/$$f" 2>/dev/null || echo "$$PWD/$$f")"; \
	fi

help:
	@sed -n '2,/^#====.*$$/p' Makefile | sed '$$d' | sed 's/^# \{0,1\}//'

# Only the directories this Makefile creates are removed, whatever BUILD_ROOT is set to
clean:
	rm -rf "$(BUILD_ROOT)/obj_nocov" "$(BUILD_ROOT)/obj_cov" "$(BUILD_ROOT)/uvm_dpi" compile_*.log
	@rmdir "$(BUILD_ROOT)" 2>/dev/null || true

cleanall: clean
	rm -rf coverage *_sim_dir *.log *.fst oracle/__pycache__
