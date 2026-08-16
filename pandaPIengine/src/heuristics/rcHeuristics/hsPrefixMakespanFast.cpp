/*
 * hsPrefixMakespanFast.cpp
 */

#include "hsPrefixMakespanFast.h"
#include <algorithm>
#include <limits>
#include <string>

namespace progression {

    // Model::apply marks a fact that is currently false with this sentinel rather than with
    // INT_MAX, so it has to be recognised when the node's times are read back in.
    static const int parallelInf = std::numeric_limits<int>::max() / 4;

    hsPrefixMakespanFast::hsPrefixMakespanFast(Model *htn) : m(htn) {
        // The RC model holds one "tdr-" fact per action of the original model, so counting them
        // recovers where its own actions end and its method actions begin.
        numHtnActions = 0;
        for (int i = 0; i < m->numStateBits; ++i)
            if (m->factStrs[i].rfind("tdr-", 0) == 0)
                ++numHtnActions;
    }

    // A method action stands for a decomposition and pandaPI's artificial method-precondition
    // actions are instantaneous; everything else occupies one time step.
    int hsPrefixMakespanFast::duration(int op) const {
        if (op >= numHtnActions)
            return 0;
        return m->taskNames[op].rfind("__", 0) == 0 ? 0 : 1;
    }

    int hsPrefixMakespanFast::getHeuristicValue(bucketSet &s, noDelIntSet &g) {
        vector<int> factTime(m->numStateBits, INT_MAX);

        // Seed the facts of the original model from the node's incrementally maintained times.
        // A fact that is not currently true carries the sentinel and has to stay unreachable
        // rather than become huge-but-finite, or an unreachable goal reads as a vast estimate
        // and the node is never recognised as a dead end.
        if (nodeFactTimes != nullptr) {
            for (int i = 0; i < numHtnBits && i < m->numStateBits; ++i)
                factTime[i] = (nodeFactTimes[i] >= parallelInf) ? INT_MAX : nodeFactTimes[i];
        }

        // The RC bookkeeping facts are not tracked there. Those the node's task network provides
        // hold from time zero.
        for (int f = s.getFirst(); f >= 0; f = s.getNext())
            if (factTime[f] == INT_MAX)
                factTime[f] = 0;

        IntPairHeap<int> queue(m->numStateBits * 2);
        for (int f = s.getFirst(); f >= 0; f = s.getNext())
            if (factTime[f] != INT_MAX)
                queue.add(factTime[f], f);

        vector<int> unsatPrecs(m->numActions);
        vector<int> layerOp(m->numActions, 0);
        vector<int> zeroPrecActions;

        // Every precondition counts, the bookkeeping ones included. A method action's
        // preconditions are the bottom-up bits of its subtasks, so waiting for them is what makes
        // a task complete when its last subtask finishes; a primitive action's top-down bit is
        // what confines the estimate to the actions this task network can still reach.
        for (int i = 0; i < m->numActions; ++i) {
            unsatPrecs[i] = m->numPrecs[i];
            if (unsatPrecs[i] == 0)
                zeroPrecActions.push_back(i);
        }

        while (!queue.isEmpty() || !zeroPrecActions.empty()) {
            int time = 0;
            int f = -1;

            if (zeroPrecActions.empty()) {
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

                int finishTime = layerOp[op] + duration(op);

                // Effects hold when the action finishes, its bottom-up bit included: a task is
                // produced from below once its last subtask is done, not once it starts.
                for (int j = 0; j < m->numAdds[op]; ++j) {
                    int eff = m->addLists[op][j];
                    if (factTime[eff] > finishTime) {
                        factTime[eff] = finishTime;
                        queue.add(finishTime, eff);
                    }
                }
            }
        }

        int globalMax = 0;
        for (int goalFact = g.getFirst(); goalFact >= 0; goalFact = g.getNext()) {
            if (factTime[goalFact] == INT_MAX) return INT_MAX;
            if (factTime[goalFact] > globalMax) globalMax = factTime[goalFact];
        }

        // Return remaining makespan h = f - g
        return std::max(0, globalMax - nodeMakespan);
    }

} /* namespace progression */
