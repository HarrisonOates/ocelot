# Ocelot — Makespan-Optimal HTN Planning using Progression Search

Ocelot (Optimal Concurrent Execution, Linked Ordered Tasks) is a planning system for **makespan-optimal HTN planning**.
It finds plans that minimise concurrent execution time (makespan) rather than
sequential plan length.

A description of Ocelot's approach can be found in our HPlan paper [link to be added when live].

## Pipeline overview

```
HDDL domain + problem
        │
        ▼
  pandaPIparser       (parsing)
        │
        ▼
  pandaPIgrounder     (grounding)
        │
        ▼
  pandaPIengine       (A* search with h^pm heuristic)
        │
        ▼
  pandaPIparser -c    (clean raw plan)
        │
        ▼
  htnpop.py           (encode as POCL plan → MAX-SAT)
        │
        ▼
  rc2 solver          (optimise for makespan)
        │
        ▼
  analyzer.py         (Extract makespan-optimal plan)
```

## Prerequisites

- **C++ compiler:** g++ with C++17 support
- **Build tools:** make, cmake ≥ 3.10, gengetopt, flex ≥ 2.6, bison ≥ 3.5
- **Python:** 3.10+
- **uv:** [install](https://docs.astral.sh/uv/getting-started/installation/)

On Ubuntu/Debian:
```bash
sudo apt install build-essential cmake gengetopt flex bison
```

## Quick start

**1. Build C++ components:**
```bash
./build.sh
```

**2. Set up Python environment:**
```bash
uv sync
```

**3. Run the pipeline on a single problem:**
```bash
uv run ocelot \
    domains/partial-order/Barman-BDI/domain.hddl \
    domains/partial-order/Barman-BDI/pfile01.hddl \
    results/barman_p01
```

**4. Run in batch mode over a whole domain:**
```bash
uv run ocelot \
    domains/partial-order/Barman-BDI/ \
    results/barman/
```

Batch mode prints a summary table with makespan, node counts, and timing after
all problems complete.

## CLI reference

```
uv run ocelot domain.hddl problem.hddl output [OPTIONS]
uv run ocelot domain_dir/ output_dir/   [OPTIONS]

Options:
  --heuristic TEXT    Heuristic passed to -H  [default: rc2(prefixMakespanFast),rc2(ff)]
  --g-value TEXT      G-value mode            [default: makespan]
  --weight INT        A* weight               [default: 1]
  --engine PATH       Override engine binary
  --parser PATH       Override parser binary
  --grounder PATH     Override grounder binary
```

## Repository layout

```
PANDA/
├── build.sh                    # Build all C++ components
├── pyproject.toml              # Python project (ocelot CLI)
├── LICENSE                     # MIT license (new code)
├── NOTICE                      # Third-party attributions
│
├── pandaPIengine/              # HTN planner (CMake, C++)
│   ├── build/pandaPIengine     # Compiled binary
│   └── src/heuristics/        # prefixMakespan heuristic
│
├── pandaPIparser/              # HDDL parser (make, C++)
│   └── pandaPIparser           # Compiled binary
│
├── pandaPIgrounder/            # HTN grounder (make, C++)
│   └── pandaPIgrounder/src/pandaPIgrounder
│
├── scripts/                    # Python pipeline (ocelot)
│   ├── run_planner.py          # CLI entry point
│   ├── htnpop.py               # HTN → POCL encoding
│   └── ...                     # MAX-SAT utilities (popgen-derived)
│
├── domains/                    # Benchmark problems
│   └── partial-order/          # Barman, Rover, Satellite, ...
│
└── tests/                      # Python unit tests
```

## Heuristics

Both naive and an incrementally computed phase 1 implementation of $h^{pm}$ is included.

- `rc2(prefixMakespan)` is the naive implementation
- `rcr(prefixMakespanFast)` is the incremental implementation.

All other heuristics included in $\mathrm{PANDA}_\pi$ are also configurable via the `--heuristic` flag.

## Licence

New code copyright © 2026 Harrison Oates, MIT License (see `LICENSE`).
Third-party components: see `NOTICE`.

## Citing This Work

```latex
@InProceedings{Oates2026MakespanViaProgression,
  author    = {Harrison Oates and Pascal Bercher},
  booktitle = {Proceedings of the 9th ICAPS Workshop on Hierarchical Planning (HPlan 2026)},
  title     = {No Plan-Space? No Problem! Towards Makespan-Optimal HTN Planning Via Progression Search},
  year      = {2026},
}
```
