/*
 * hsPrefixMakespan.h
 *
 * Admissible makespan heuristic for HTN planning that accounts for
 * past actions (Prefix) and future tasks (Fringe) in a unified
 * relaxed timeline starting from S0.
 *
 * Phase 1 replays the node's prefix to date the facts it leaves behind; phase 2 is
 * h^1_p over the whole relaxed-composition model, where an action waits for every one
 * of its preconditions — the top-down and bottom-up bookkeeping bits included — and
 * every effect holds when the action finishes. A method action's preconditions are the
 * bottom-up bits of its subtasks and it takes no time, so the hierarchy is propagated
 * by the same sweep and needs no pass of its own.
 *
 * hsPrefixMakespanFast computes the same estimate, reading phase 1's result from the
 * search node instead of replaying it.
 */

#ifndef HEURISTICS_HSPREFIXMAKESPAN_H_
#define HEURISTICS_HSPREFIXMAKESPAN_H_

#include <climits>
#include <vector>
#include <string>
#include <list>
#include "../../intDataStructures/IntPairHeap.h"
#include "../../intDataStructures/bucketSet.h"
#include "../../intDataStructures/noDelIntSet.h"
#include "../../Model.h"
#include "LMCutLandmark.h"

using namespace std;

namespace progression {

    class hsPrefixMakespan {
    protected:
        Model *m;

        // Where the original model's facts end, and where the RC model's own actions end and
        // its method actions begin
        int numHtnBits = 0;
        int numHtnActions = 0;

        // Context from the search engine
        vector<int> currentPrefix;
        int nodeMakespan = 0; // planMakespan of the current search node (g-value)

        int duration(int op) const;

    public:
        hsPrefixMakespan(Model *htn);
        virtual ~hsPrefixMakespan() = default;

        // Set prefix and node g-value before each getHeuristicValue call
        void setPrefix(const vector<int>& prefix);
        void setNodeMakespan(int g) { nodeMakespan = g; }

        // Returns the global makespan estimate
        int getHeuristicValue(bucketSet &s, noDelIntSet &g);

        string getDescription() { return "prefix-aware-makespan-rc"; }

        list<LMCutLandmark *>* cuts = nullptr;
    };

} /* namespace progression */

#endif /* HEURISTICS_HSPREFIXMAKESPAN_H_ */
