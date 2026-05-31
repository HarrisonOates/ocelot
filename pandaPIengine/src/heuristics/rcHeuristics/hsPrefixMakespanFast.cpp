/*
 * hsPrefixMakespanFast.cpp
 */

#include "hsPrefixMakespanFast.h"
#include <algorithm>
#include <string>

namespace progression {

    hsPrefixMakespanFast::hsPrefixMakespanFast(Model *htn) : m(htn) {
        isRCFact.assign(m->numStateBits, false);
        for (int i = 0; i < m->numStateBits; ++i) {
            const string &name = m->factStrs[i];
            if (name.rfind("tdr-", 0) == 0 || name.rfind("bur-", 0) == 0)
                isRCFact[i] = true;
        }
    }

    int hsPrefixMakespanFast::getHeuristicValue(bucketSet &s, noDelIntSet &g) {
        // Working copies — one per call (same pattern as hsPrefixMakespan Phase 2)
        vector<int> factTime(m->numStateBits, INT_MAX);
        vector<int> taskCompletion(m->numTasks + m->numActions, INT_MAX);

        // Seed fact times directly from the node's incrementally-maintained array.
        // For HTN facts use nodeFactTimes; for RC bookkeeping facts use 0.
        if (nodeFactTimes != nullptr) {
            for (int i = 0; i < numHtnBits && i < m->numStateBits; ++i)
                factTime[i] = nodeFactTimes[i];
        }
        // RC model facts beyond the HTN range keep INT_MAX (set to 0 below if in state)

        // Override with current state: any fact in s that has INT_MAX gets time 0
        for (int f = s.getFirst(); f >= 0; f = s.getNext()) {
            if (factTime[f] == INT_MAX)
                factTime[f] = 0;
        }

        IntPairHeap<int> queue(m->numStateBits * 2);

        // Enqueue non-RC facts that are reachable
        for (int f = s.getFirst(); f >= 0; f = s.getNext()) {
            if (!isRCFact[f] && factTime[f] != INT_MAX)
                queue.add(factTime[f], f);
        }

        vector<int> unsatPrecs(m->numActions);
        vector<int> layerOp(m->numActions, 0);
        vector<int> zeroPrecActions;

        for (int i = 0; i < m->numActions; ++i) {
            int count = 0;
            for (int j = 0; j < m->numPrecs[i]; ++j)
                if (!isRCFact[m->precLists[i][j]]) ++count;
            unsatPrecs[i] = count;
            if (count == 0)
                zeroPrecActions.push_back(i);
        }

        // Reachability propagation (mirrors hsPrefixMakespan Phase 2)
        while (!queue.isEmpty() || !zeroPrecActions.empty()) {
            int time = 0;
            int f = -1;

            if (!zeroPrecActions.empty()) {
                // processed below
            } else {
                time = queue.topKey();
                f = queue.topVal();
                queue.pop();
            }

            if (f != -1) {
                for (int i = 0; i < m->precToActionSize[f]; ++i) {
                    int op = m->precToAction[f][i];
                    if (layerOp[op] < time) layerOp[op] = time;
                    if (--unsatPrecs[op] == 0)
                        zeroPrecActions.push_back(op);
                }
            }

            while (!zeroPrecActions.empty()) {
                int op = zeroPrecActions.back();
                zeroPrecActions.pop_back();

                int startTime = layerOp[op];
                int duration = 1;
                if (m->taskNames[op].rfind("__", 0) == 0) duration = 0;
                int finishTime = startTime + duration;

                if (finishTime < taskCompletion[op])
                    taskCompletion[op] = finishTime;

                for (int j = 0; j < m->numAdds[op]; ++j) {
                    int eff = m->addLists[op][j];
                    int effTime = isRCFact[eff] ? startTime : finishTime;
                    if (factTime[eff] > effTime) {
                        factTime[eff] = effTime;
                        if (!isRCFact[eff])
                            queue.add(effTime, eff);
                    }
                }
            }
        }

        // Hierarchical propagation (methods)
        bool changed = true;
        while (changed) {
            changed = false;
            for (int mIdx = 0; mIdx < m->numMethods; ++mIdx) {
                int methodEnd = 0;
                bool possible = true;
                for (int j = 0; j < m->numSubTasks[mIdx]; ++j) {
                    int subT = m->subTasks[mIdx][j];
                    if (taskCompletion[subT] == INT_MAX) { possible = false; break; }
                    if (taskCompletion[subT] > methodEnd) methodEnd = taskCompletion[subT];
                }
                if (possible) {
                    int parent = m->decomposedTask[mIdx];
                    if (methodEnd < taskCompletion[parent]) {
                        taskCompletion[parent] = methodEnd;
                        changed = true;
                    }
                }
            }
        }

        // Compute total makespan estimate over goal facts
        int globalMax = 0;
        for (int goalFact = g.getFirst(); goalFact >= 0; goalFact = g.getNext()) {
            if (factTime[goalFact] == INT_MAX) return INT_MAX;
            if (factTime[goalFact] > globalMax) globalMax = factTime[goalFact];
        }

        // Return remaining makespan h = f - g
        return std::max(0, globalMax - nodeMakespan);
    }

} /* namespace progression */
