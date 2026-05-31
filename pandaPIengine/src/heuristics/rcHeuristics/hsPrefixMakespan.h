/*
 * hsPrefixMakespan.h
 *
 * Admissible makespan heuristic for HTN planning that accounts for 
 * past actions (Prefix) and future tasks (Fringe) in a unified 
 * relaxed timeline starting from S0.
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
        
        // Static RC Reachability Data (Computed once from S0)
        int* factEarliestTime;     // Earliest time a fact becomes true
        int* taskEarliestCompletion; // Earliest time a task/action can finish
        
        // RC Bookkeeping detection
        vector<bool> isRCFact;
        vector<bool> isAbstractTask;
        
        // Context from the search engine
        vector<int> currentPrefix;
        int nodeMakespan = 0; // planMakespan of the current search node (g-value)

        // Initialization helpers
        void identifyRCStateBits();
        void computeStaticReachability();

    public:
        hsPrefixMakespan(Model *htn);
        virtual ~hsPrefixMakespan();

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