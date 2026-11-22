# Ocelot — Makespan-Optimal HTN Planning using Progression Search

Ocelot (Optimal Concurrent Execution, Linked Ordered Tasks) is a planning system for **makespan-optimal HTN planning**.
It finds plans that minimise concurrent execution time (makespan) rather than
sequential plan length.

A description of Ocelot's approach can be found in our [HPlan paper](https://bibbase.org/network/publication/oates-bercher-noplanspacenoproblemtowardsmakespanoptimalhtnplanningviaprogressionsearch-2026).

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
        │
        ▼
  certificate.py      (write HDDL 1.1 partial-order plan, <output>.hddl)
        │
        ▼
  Bell (optional)     (validate the plan; check its makespan)
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
  --heuristic TEXT            Heuristic passed to -H [default: rc2(prefixMakespanFast),rc2(ff)]
  --g-value TEXT              G-value mode           [default: makespan]
  --weight INT                A* weight              [default: 1]
  --semantics ["pocl"|"parallel"] Makespan semantics [default: "pocl"]
  --plan-output PATH  Where to write the plan certificate [default: <output>.hddl]
  --bell PATH         Bell validator binary [default: $OCELOT_BELL,
                      ../bell-htn/target/release/bell, or `bell` on PATH]
  --no-bell           Skip validating the certificate
  --bell-profile ["auto"|"ipc2027"|"hddl-1.1"]  [default: auto]
  --bell-timeout S    Seconds allowed for Bell [default: 600]
  --engine PATH       Override engine binary
  --parser PATH       Override parser binary
  --grounder PATH     Override grounder binary
```

## Plan output

The final plan is written to `<output>.hddl` as an **HDDL 1.1 plan certificate**
in the original (lifted) domain's names: the chosen decomposition (every method
with all its parameters, in declaration order) plus the primitive actions as a
partial order (`:tasks` / `:ordering`). pandaPI compilation artefacts
(`__method_precondition_*`, `__top`, ...) are removed. Method parameters missing
from the cleaned PANDA plan are recovered by unification with the task head and
subtasks, and otherwise by search over typed objects against the method's
precondition.

The partial order is the reorderer's own MaxSAT `Order` relation (causal links,
threat resolutions and HTN orderings), plus every ordering inherited through
the decomposition, plus (for `--semantics parallel`) an edge between each pair of
interfering actions in different layers, transitively reduced. Its longest chain
is checked to equal the reported makespan.

When the [Bell](https://github.com/HarrisonOates/bell) validator is found,
the pipeline runs it on the certificate. The run fails, with Bell's
diagnostics, if Bell rejects the plan or reports a different makespan. Bell's
verdict is saved in `<output>.bell.json`. With `--bell-profile auto`, Bell uses
its competition profile `ipc2027` when the instance declares the makespan
objective (`:makespan` requirement and `(:metric minimize (makespan))`).
Otherwise it uses `hddl-1.1`, because `ipc2027` accepts partial orders only for
makespan.

Known limitation: the MaxSAT reorderer does not yet model method preconditions,
so it can leave an action unordered that may destroy a method's precondition.
Bell then rejects the certificate (`method_precondition`) and the run fails
instead of reporting an invalid plan.

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
│   ├── certificate.py          # HDDL 1.1 partial-order plan output
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

## Makespan Semantics

Ocelot is primarily a **POCL-optimal planner**: that is, it optimizes makespan over a Partial-Order Causal Link (POCL) representation of the plan, rather than restricting actions to discrete, lock-step time slices.

We support two distinct scheduling semantics during MaxSAT optimization:

* **POCL Semantics (`--semantics pocl`, default):**
Orders actions strictly by causal dependencies and threat resolutions. Makespan is defined as the length of the **critical path** through the dependency graph. Non-interfering actions float flexibly in continuous time, eliminating artificial synchronization delays.
* **Parallel Semantics (`--semantics parallel`):**
Partitions actions into discrete, synchronized time steps ($t_0, t_1, \dots, t_k$), requiring all actions executed within a step to be mutually non-interfering. Makespan is defined as the **total number of parallel steps**.

As shown in [Oates and Bercher (AAAI 2026)](https://ojs.aaai.org/index.php/AAAI/article/view/40953), the different semantics can result in different optimal plan lengths.

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
