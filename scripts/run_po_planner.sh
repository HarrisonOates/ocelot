#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "DEPRECATED: use 'uv run ocelot' instead of this script." >&2

# --- Argument Parsing ---
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <domain.hddl> <problem.hddl> <output_basename>"
    exit 1
fi

# --- Configuration ---
# Input files
DOMAIN_HDDL="$1"
PROBLEM_HDDL="$2"
BASENAME="$3" # Base name for all generated files to keep things tidy
TIMING_LOG="${BASENAME}.log"

# Clean up previous log
rm -f "$TIMING_LOG"

# The format string for /usr/bin/time:
# %e = Wall-clock time (seconds)
# %M = Max resident set size (Kilobytes)
TIME_FORMAT="%e,%M" 


# --- Tool Paths ---
PARSER="./pandaPIparser"
GROUNDER="./pandaPIgrounder"
ENGINE="./pandaPIengine"
POP_ENCODER="python3 ./htnpop.py"
SOLVER="rc2.py"
ANALYZER="python3 ./analyzer.py"

# --- Pipeline Execution ---
echo "Step 1: Parsing..."
/usr/bin/time -f "Parsing,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $PARSER "$DOMAIN_HDDL" "$PROBLEM_HDDL" "${BASENAME}.htn"

echo "Step 2: Grounding..."
/usr/bin/time -f "Grounding,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $GROUNDER "${BASENAME}.htn" "${BASENAME}.sas"

echo "Step 3: Initial Plan Search..."
# Note: pandaPIengine might print to stderr, so we handle that
{ /usr/bin/time -f "InitialEngine,$TIME_FORMAT" -o "$TIMING_LOG" -a \
        $ENGINE -g makespan \
            -H "rc2(prefixMakespanFast),rc2(ff)" \
            "${BASENAME}.sas" > "${BASENAME}.original"; } 2>/dev/null

echo "Step 4: Cleaning Plan..."
/usr/bin/time -f "Cleaning,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $PARSER -c "${BASENAME}.original" "${BASENAME}.actual"

echo "Step 5: Encoding POP..."
/usr/bin/time -f "Encoding,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $POP_ENCODER "${BASENAME}.actual" "$DOMAIN_HDDL" "$PROBLEM_HDDL" -o "${BASENAME}.wcnf"

echo "Step 6: Solving MAX-SAT..."
/usr/bin/time -f "Solving,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $SOLVER -vv "${BASENAME}.wcnf" > "${BASENAME}.sol"

echo "Step 7: Analyzing Solution..."
# This step is usually very fast and can be excluded from "Planning Time"
# but we time it for completeness.
/usr/bin/time -f "Analysis,$TIME_FORMAT" -o "$TIMING_LOG" -a \
    $ANALYZER --map "${BASENAME}.wcnf.map" --rc2out "${BASENAME}.sol" --show-popstats > "${BASENAME}.stats"

echo "---"
echo "Pipeline finished successfully!"
echo "Performance data logged in ${TIMING_LOG}"
echo "Final stats saved to ${BASENAME}.stats"