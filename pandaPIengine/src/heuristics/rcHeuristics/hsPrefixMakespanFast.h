/*
 * hsPrefixMakespanFast.h
 *
 * Incremental variant of hsPrefixMakespan that eliminates Phase 1 (prefix replay)
 * by reading fact availability times directly from the search node's factEarliestTrue[].
 *
 * factEarliestTrue[] is maintained at O(precs) per search step by Model::apply(),
 * so there is no per-heuristic-call cost for replaying the prefix.
 *
 * Admissibility argument:
 *   factEarliestTrue[f] >= Phase1's factTime[f] because Model::apply() also
 *   enforces HTN ordering constraints (taskEarliestStart), which can only delay
 *   facts relative to the pure data-flow parallel relaxation Phase 1 computes.
 *   Seeding Phase 2 with later times pushes the RPG estimate up, never below h*.
 *   Phase 2 delete-relaxed RPG remains a valid lower bound.
 *
 * Phase 2 is h^1_p over the whole relaxed-composition model: an action waits for
 * every one of its preconditions — the top-down and bottom-up bookkeeping bits
 * included — and every effect holds when the action finishes. A method action's
 * preconditions are the bottom-up bits of its subtasks and it takes no time, so
 * the hierarchy is propagated by the same sweep and needs no separate pass.
 *
 * Return value: max(0, globalMax - nodeMakespan) — remaining makespan (h-value).
 */

#ifndef HEURISTICS_HSPREFIXMAKESPANFAST_H_
#define HEURISTICS_HSPREFIXMAKESPANFAST_H_

#include <climits>
#include <vector>
#include <string>
#include <list>
#include <algorithm>
#include "../../intDataStructures/IntPairHeap.h"
#include "../../intDataStructures/bucketSet.h"
#include "../../intDataStructures/noDelIntSet.h"
#include "../../Model.h"
#include "LMCutLandmark.h"

using namespace std;

namespace progression {

    class hsPrefixMakespanFast {
    protected:
        Model *m;

        // Where the RC model's own actions end and its method actions begin
        int numHtnActions = 0;

        // Fact times and node makespan injected by hhRC2 before each call
        int *nodeFactTimes = nullptr;   // pointer to n->factEarliestTrue (not owned)
        int numHtnBits = 0;
        int nodeMakespan = 0;

        int duration(int op) const;

    public:
        hsPrefixMakespanFast(Model *htn);
        virtual ~hsPrefixMakespanFast() = default;

        // Called by hhRC2 before getHeuristicValue (O(1) — pointer store only)
        void setFactTimesFromNode(int* factEarliestTrue, int numHtnStateBits) {
            nodeFactTimes = factEarliestTrue;
            numHtnBits = numHtnStateBits;
        }

        void setNodeMakespan(int g) { nodeMakespan = g; }

        int getHeuristicValue(bucketSet &s, noDelIntSet &g);

        string getDescription() { return "prefix-makespan-fast"; }

        list<LMCutLandmark *>* cuts = nullptr;
    };

} /* namespace progression */

#endif /* HEURISTICS_HSPREFIXMAKESPANFAST_H_ */
