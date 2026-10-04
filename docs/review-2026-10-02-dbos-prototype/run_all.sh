#!/bin/sh
# Runs every behaviour test; each writes out_<name>.txt. Usage: sh run_all.sh [names...]
cd "$(dirname "$0")/tests"
for t in ${@:-b1_single_spawn b2_displaced_writer b3_crash_resume b3b_code_edit b4_stop_while_down b5_extend b6_ensure_reads_first b7_cold_liveness b8_no_dbos_read b9_stop_in_long_step}; do
  ../venv/bin/python $t.py > ../out_$t.txt 2>&1
  grep -A8 "^=== " ../out_$t.txt
done
