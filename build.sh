#!/usr/bin/env bash
# Build all PANDA C++ components.
# Run from the PANDA root directory.
set -euo pipefail

PANDA_ROOT="$(cd "$(dirname "$0")" && pwd)"
NPROC=$(nproc 2>/dev/null || sysctl -n hw.logicalcpu 2>/dev/null || echo 4)

echo "=== Building pandaPIparser ==="
if [ -f "$PANDA_ROOT/pandaPIparser/pandaPIparser" ]; then
    echo "  Already built. Remove pandaPIparser/pandaPIparser to rebuild."
else
    cd "$PANDA_ROOT/pandaPIparser"
    make -j"$NPROC"
    echo "  Done: pandaPIparser/pandaPIparser"
fi

echo ""
echo "=== Building pandaPIengine ==="
if [ -f "$PANDA_ROOT/pandaPIengine/build/pandaPIengine" ]; then
    echo "  Already built. Remove pandaPIengine/build/pandaPIengine to rebuild."
else
    mkdir -p "$PANDA_ROOT/pandaPIengine/build"
    cd "$PANDA_ROOT/pandaPIengine/build"
    cmake ../src -DCMAKE_BUILD_TYPE=Release
    make -j"$NPROC"
    echo "  Done: pandaPIengine/build/pandaPIengine"
fi

echo ""
echo "=== Building pandaPIgrounder ==="
GROUNDER_DIR="$PANDA_ROOT/pandaPIgrounder"
GROUNDER_BIN="$GROUNDER_DIR/pandaPIgrounder"
if [ -f "$GROUNDER_BIN" ]; then
    echo "  Already built. Remove pandaPIgrounder/pandaPIgrounder to rebuild."
else
    if [ ! -f "$GROUNDER_DIR/cpddl/libpddl.a" ]; then
        echo "  Building cpddl dependency..."
        cd "$PANDA_ROOT"
        git submodule update --init pandaPIgrounder/cpddl pandaPIgrounder/h2-fd-preprocessor
        cd "$GROUNDER_DIR/cpddl"
        git submodule update --init
        if git diff --quiet HEAD 2>/dev/null; then
            git apply "$GROUNDER_DIR/0002-makefile.patch" 2>/dev/null || true
        fi
        make boruvka opts bliss lpsolve
        make
    fi
    echo "  Building pandaPIgrounder..."
    cd "$GROUNDER_DIR/src"
    make -j"$NPROC"
    echo "  Done: $GROUNDER_BIN"
fi

echo ""
echo "=== All components built ==="
echo "  pandaPIparser:   $PANDA_ROOT/pandaPIparser/pandaPIparser"
echo "  pandaPIengine:   $PANDA_ROOT/pandaPIengine/build/pandaPIengine"
echo "  pandaPIgrounder: $GROUNDER_BIN"
echo ""
echo "Next: run 'uv sync' to set up the Python environment."
