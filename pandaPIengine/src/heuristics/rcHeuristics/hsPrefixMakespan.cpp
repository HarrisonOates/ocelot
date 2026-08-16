/*
 * hsPrefixMakespan.cpp
 *
 * Implementation of h^pm heuristic
 */

#include "hsPrefixMakespan.h"
#include <cassert>
#include <cstring>
#include <algorithm>
#include <iostream>

namespace progression {

    hsPrefixMakespan::hsPrefixMakespan(Model *htn) {
        this->m = htn;

        // The RC model appends one "tdr-" fact per action of the original model and then one
        // "bur-" fact per task, so the first of them marks where the original model's facts end
        // and counting them recovers where the RC model's own actions end and its method
        // actions begin.
        numHtnActions = 0;
        numHtnBits = m->numStateBits;
        for (int i = 0; i < m->numStateBits; ++i) {
            if (m->factStrs[i].rfind("tdr-", 0) != 0) continue;
            if (numHtnActions == 0) numHtnBits = i;
            ++numHtnActions;
        }
    }

    void hsPrefixMakespan::setPrefix(const vector<int>& prefix) {
        this->currentPrefix = prefix;
    }

    // A method action stands for a decomposition and pandaPI's artificial method-precondition
    // actions are instantaneous; everything else occupies one time step.
    int hsPrefixMakespan::duration(int op) const {
        if (op >= numHtnActions)
            return 0;
        return m->taskNames[op].rfind("__", 0) == 0 ? 0 : 1;
    }

    int hsPrefixMakespan::getHeuristicValue(bucketSet &s, noDelIntSet &g) {
        // 1. Replay the prefix to date the facts it leaves behind. Only the original model's
        // facts are replayed: the bookkeeping bits are the task network's business and are dated
        // from the node below, exactly as hsPrefixMakespanFast reads them from the node. Dating
        // a bottom-up bit here instead would leave it holding a time nothing ever enqueues, and
        // every method waiting on it would stall.
        vector<int> factTime(m->numStateBits, INT_MAX);

        for (int i = 0; i < m->s0Size; i++) {
            if (m->s0List[i] < numHtnBits) factTime[m->s0List[i]] = 0;
        }

        for (int actionId : currentPrefix) {
            int startTime = 0;
            for (int j = 0; j < m->numPrecs[actionId]; j++) {
                int p = m->precLists[actionId][j];
                if (p < numHtnBits && factTime[p] != INT_MAX) {
                    startTime = max(startTime, factTime[p]);
                }
            }

            int endTime = startTime + duration(actionId);

            // Re-adding a fact that already holds does not delay it. Overwriting here dates
            // every fact by its *last* producer in the prefix, which pushes the estimate above
            // the makespan of the schedule actually executed and loses admissibility.
            for (int j = 0; j < m->numAdds[actionId]; j++) {
                int a = m->addLists[actionId][j];
                if (a < numHtnBits && endTime < factTime[a]) factTime[a] = endTime;
            }
            for (int j = 0; j < m->numDels[actionId]; j++) {
                int d = m->delLists[actionId][j];
                if (d < numHtnBits) factTime[d] = INT_MAX;
            }
        }

        // 2. Date the facts the node holds. Those the prefix never touched — the RC
        // bookkeeping bits its task network provides — hold from time zero.
        IntPairHeap<int> queue(m->numStateBits * 2);
        for (int f = s.getFirst(); f >= 0; f = s.getNext()) {
            if (factTime[f] == INT_MAX) {
                factTime[f] = 0;
            }
            queue.add(factTime[f], f);
        }

        vector<int> unsatPrecs(m->numActions);
        vector<int> layerOp(m->numActions, 0);
        vector<int> zeroPrecActions;

        // Every precondition counts, the bookkeeping ones included. A method action's
        // preconditions are the bottom-up bits of its subtasks, so waiting for them is what makes
        // a task complete when its last subtask finishes; a primitive action's top-down bit is
        // what confines the estimate to the actions this task network can still reach.
        for (int i = 0; i < m->numActions; i++) {
            unsatPrecs[i] = m->numPrecs[i];
            if (unsatPrecs[i] == 0) {
                zeroPrecActions.push_back(i);
            }
        }

        // 3. Reachability Loop (Bottom-Up Facts -> Actions -> Facts)
        while (!queue.isEmpty() || !zeroPrecActions.empty()) {
            int time = 0;
            int f = -1;

            if (zeroPrecActions.empty()) {
                time = queue.topKey();
                f = queue.topVal();
                queue.pop();
            }

            if (f != -1) {
                for (int i = 0; i < m->precToActionSize[f]; i++) {
                    int op = m->precToAction[f][i];

                    if (layerOp[op] < time) layerOp[op] = time;

                    unsatPrecs[op]--;
                    if (unsatPrecs[op] == 0) {
                        zeroPrecActions.push_back(op);
                    }
                }
            }

            while (!zeroPrecActions.empty()) {
                int op = zeroPrecActions.back();
                zeroPrecActions.pop_back();

                int finishTime = layerOp[op] + duration(op);

                // Effects hold when the action finishes, its bottom-up bit included: a task is
                // produced from below once its last subtask is done, not once it starts.
                for (int j = 0; j < m->numAdds[op]; j++) {
                    int eff = m->addLists[op][j];
                    if (factTime[eff] > finishTime) {
                        factTime[eff] = finishTime;
                        queue.add(finishTime, eff);
                    }
                }
            }
        }

        // 4. Calculate Global Makespan
        int globalMax = 0;
        for (int goalFact = g.getFirst(); goalFact >= 0; goalFact = g.getNext()) {
            if (factTime[goalFact] == INT_MAX) return INT_MAX;
            if (factTime[goalFact] > globalMax) globalMax = factTime[goalFact];
        }

        // Return remaining makespan: h = f - g (where f = total from S0, g = executed prefix)
        return std::max(0, globalMax - nodeMakespan);
    }

} /* namespace progression */
