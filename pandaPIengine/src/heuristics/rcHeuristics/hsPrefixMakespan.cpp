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
        
        // Initialize arrays
        factEarliestTime = new int[m->numStateBits];
        taskEarliestCompletion = new int[m->numTasks + m->numActions]; // Covers abstract tasks and primitive actions

        // Initialize RC identification
        identifyRCStateBits();

        // Precompute the Relaxed Composition graph from S0
        computeStaticReachability();
    }

    hsPrefixMakespan::~hsPrefixMakespan() {
        delete[] factEarliestTime;
        delete[] taskEarliestCompletion;
    }

    void hsPrefixMakespan::setPrefix(const vector<int>& prefix) {
        this->currentPrefix = prefix;
    }

    void hsPrefixMakespan::identifyRCStateBits() {
        isRCFact.assign(m->numStateBits, false);
        isAbstractTask.assign(m->numStateBits, false);

        // Identify RC bookkeeping facts (tdr-*, bur-*) and task flags
        for (int i = 0; i < m->numStateBits; ++i) {
            const string &name = m->factStrs[i];
            if (name.rfind("tdr-", 0) == 0 || name.rfind("bur-", 0) == 0) {
                isRCFact[i] = true;
            }
            // In many RC encodings, abstract tasks in the fringe are represented 
            // by facts starting with certain prefixes or mapped specifically.
            // Here we assume standard PandaPI RC naming or that tasks are handled 
            // via the fringe passed in the state. 
            // We generally detect if a fact corresponds to a task index.
            // This part depends heavily on the specific compilation. 
            // For this implementation, we assume the state passed to getHeuristicValue
            // contains task indices directly or we rely on the generic RC structure.
        }
    }

    void hsPrefixMakespan::computeStaticReachability() {
        // 1. Initialize timings to Infinity
        for(int i = 0; i < m->numStateBits; i++) factEarliestTime[i] = INT_MAX;
        for(int i = 0; i < m->numTasks + m->numActions; i++) taskEarliestCompletion[i] = INT_MAX;

        // 2. Priority Queue for Dijkstra on Facts
        IntPairHeap<int> queue(m->numStateBits * 2);

        // 3. Add Initial State (S0) facts
        // Note: We use m->s0List, NOT the current state passed at runtime
        for (int i = 0; i < m->s0Size; i++) {
            int f = m->s0List[i];
            if (factEarliestTime[f] > 0) { // Check for duplicates/improvement
                factEarliestTime[f] = 0;
                if (!isRCFact[f]) {
                    queue.add(0, f);
                }
            }
        }

        // Track unsatisfied preconditions for actions (dynamic within this static computation)
        int* unsatPrecs = new int[m->numActions];
        int* layerOp = new int[m->numActions]; // Start time of action
        for(int i=0; i<m->numActions; i++) {
            // Only count non-RC preconditions
            int count = 0;
            for(int j=0; j<m->numPrecs[i]; j++) {
                if(!isRCFact[m->precLists[i][j]]) count++;
            }
            unsatPrecs[i] = count;
            layerOp[i] = 0; // Default start time
            
            // If no preconditions, add to reachable immediately
            if(count == 0) {
                // Conceptual "Action Ready" queue could be used, 
                // but we handle it by processing effects immediately here or via logic below
            }
        }
        
        // Handle actions with 0 precs separately or ensure they trigger
        vector<int> zeroPrecActions;
        for(int i=0; i<m->numActions; i++) {
            if(unsatPrecs[i] == 0) zeroPrecActions.push_back(i);
        }

        // 4. Reachability Loop (Bottom-Up Facts -> Actions -> Facts)
        while (!queue.isEmpty() || !zeroPrecActions.empty()) {
            int time = 0;
            int f = -1;
            
            if(!zeroPrecActions.empty()) {
                // Process 0-prec actions
                // effectively time 0
            } else {
                time = queue.topKey();
                f = queue.topVal();
                queue.pop();
            }

            // Propagate Fact -> Actions
            // If we pulled a fact f:
            if (f != -1) {
                for(int i = 0; i < m->precToActionSize[f]; i++) {
                    int op = m->precToAction[f][i];
                    
                    // Update start time based on this precondition
                    if (layerOp[op] < time) layerOp[op] = time;
                    
                    unsatPrecs[op]--;
                    if (unsatPrecs[op] == 0) {
                        zeroPrecActions.push_back(op);
                    }
                }
            }
            
            // Process newly reachable actions
            while(!zeroPrecActions.empty()) {
                int op = zeroPrecActions.back();
                zeroPrecActions.pop_back();
                
                int startTime = layerOp[op];
                int duration = 1; // Primitive action makespan cost = 1
                // If m->actionCosts exists and we want makespan based on durations:
                // duration = m->actionCosts[op]; 
                
                int finishTime = startTime + duration;
                
                // Store completion time for this primitive task
                if (finishTime < taskEarliestCompletion[op]) {
                    taskEarliestCompletion[op] = finishTime;
                }

                // Apply Effects
                for(int j=0; j<m->numAdds[op]; j++) {
                    int eff = m->addLists[op][j];
                    int effTime = isRCFact[eff] ? startTime : finishTime; // RC facts usually instant or specific logic
                    
                    if (factEarliestTime[eff] > effTime) {
                        factEarliestTime[eff] = effTime;
                        if (!isRCFact[eff]) {
                            queue.add(effTime, eff);
                        }
                    }
                }
            }
        }

        delete[] unsatPrecs;
        delete[] layerOp;

        // 5. Hierarchical Propagation (Bottom-Up Tasks -> Methods -> Tasks)
        // Since HTN can be cyclic, we iterate to fixpoint (Bellman-Ford style)
        bool changed = true;
        while(changed) {
            changed = false;

            // For every Method
            for(int mIdx = 0; mIdx < m->numMethods; mIdx++) {
                int methodStart = 0;
                int methodEnd = 0;
                bool possible = true;

                // Method Makespan = Max of subtasks (Parallel Relaxation)
                // Note: For strict sequence, sum costs. For optimal reordering, max is admissible.
                for(int j=0; j<m->numSubTasks[mIdx]; j++) {
                    int subT = m->subTasks[mIdx][j];
                    if (taskEarliestCompletion[subT] == INT_MAX) {
                        possible = false; 
                        break;
                    }
                    if (taskEarliestCompletion[subT] > methodEnd) {
                        methodEnd = taskEarliestCompletion[subT];
                    }
                }

                if (possible) {
                    // Update the parent task
                    int parentTask = m->decomposedTask[mIdx];
                    if (methodEnd < taskEarliestCompletion[parentTask]) {
                        taskEarliestCompletion[parentTask] = methodEnd;
                        changed = true;
                    }
                }
            }
        }
    }

    int hsPrefixMakespan::getHeuristicValue(bucketSet &s, noDelIntSet &g) {
        // if (!currentPrefix) return INT_MAX; // No longer pointer

        // 1. Simulate Prefix to get Fact Availability Times
        // Initialize times
        vector<int> factTime(m->numStateBits, INT_MAX);
        vector<int> taskCompletion(m->numTasks + m->numActions, INT_MAX);
        
        // S0 facts at time 0
        for (int i = 0; i < m->s0Size; i++) {
            factTime[m->s0List[i]] = 0;
        }

        int prefixMakespan = 0;

        // Process Prefix Actions
        for (int actionId : currentPrefix) {
            int startTime = 0;
            // Start time is max of precondition availability times
            for (int j = 0; j < m->numPrecs[actionId]; j++) {
                int p = m->precLists[actionId][j];
                if (factTime[p] != INT_MAX) {
                    startTime = max(startTime, factTime[p]);
                }
            }
            
            int duration = 1;
            string name = m->taskNames[actionId];
            if (name.rfind("__", 0) == 0) duration = 0;
            
            int endTime = startTime + duration; 
            prefixMakespan = max(prefixMakespan, endTime);
            
            // Record action completion
            if (endTime < taskCompletion[actionId]) {
                taskCompletion[actionId] = endTime;
            }

            // Apply Effects
            // Adds
            for (int j = 0; j < m->numAdds[actionId]; j++) {
                int a = m->addLists[actionId][j];
                factTime[a] = endTime;
            }
            // Deletes
            for (int j = 0; j < m->numDels[actionId]; j++) {
                int d = m->delLists[actionId][j];
                factTime[d] = INT_MAX;
            }
        }

        // 2. Initialize RC Heuristic with current fact times
        IntPairHeap<int> queue(m->numStateBits * 2);
        
        // Initialize queue with all currently true facts from 's'
        for (int f = s.getFirst(); f >= 0; f = s.getNext()) {
            if (factTime[f] != INT_MAX) {
                if (!isRCFact[f]) {
                     queue.add(factTime[f], f);
                }
            } else {
                // Fact is in state but not tracked by prefix simulation.
                // Assume time 0 (static fact) or prefixMakespan?
                // Safest is 0 if it's a static fact.
                factTime[f] = 0;
                if (!isRCFact[f]) {
                    queue.add(0, f);
                }
            }
        }

        // Initialize dynamic arrays for reachability
        vector<int> unsatPrecs(m->numActions);
        vector<int> layerOp(m->numActions);
        vector<int> zeroPrecActions;

        for(int i=0; i<m->numActions; i++) {
            int count = 0;
            for(int j=0; j<m->numPrecs[i]; j++) {
                if(!isRCFact[m->precLists[i][j]]) count++;
            }
            unsatPrecs[i] = count;
            layerOp[i] = 0;
            
            if(count == 0) {
                zeroPrecActions.push_back(i);
            }
        }

        // 3. Reachability Loop (Bottom-Up Facts -> Actions -> Facts)
        while (!queue.isEmpty() || !zeroPrecActions.empty()) {
            int time = 0;
            int f = -1;
            
            if(!zeroPrecActions.empty()) {
                // Process 0-prec actions
            } else {
                time = queue.topKey();
                f = queue.topVal();
                queue.pop();
            }

            // Propagate Fact -> Actions
            if (f != -1) {
                for(int i = 0; i < m->precToActionSize[f]; i++) {
                    int op = m->precToAction[f][i];
                    
                    if (layerOp[op] < time) layerOp[op] = time;
                    
                    unsatPrecs[op]--;
                    if (unsatPrecs[op] == 0) {
                        zeroPrecActions.push_back(op);
                    }
                }
            }
            
            // Process newly reachable actions
            while(!zeroPrecActions.empty()) {
                int op = zeroPrecActions.back();
                zeroPrecActions.pop_back();
                
                int startTime = layerOp[op];
                int duration = 1; 
                string name = m->taskNames[op];
                if (name.rfind("__", 0) == 0) duration = 0;

                int finishTime = startTime + duration;
                
                if (finishTime < taskCompletion[op]) {
                    taskCompletion[op] = finishTime;
                }

                // Apply Effects
                for(int j=0; j<m->numAdds[op]; j++) {
                    int eff = m->addLists[op][j];
                    int effTime = isRCFact[eff] ? startTime : finishTime;
                    
                    if (factTime[eff] > effTime) {
                        factTime[eff] = effTime;
                        if (!isRCFact[eff]) {
                            queue.add(effTime, eff);
                        }
                    }
                }
            }
        }

        // 4. Hierarchical Propagation (Bottom-Up Tasks -> Methods -> Tasks)
        bool changed = true;
        while(changed) {
            changed = false;

            for(int mIdx = 0; mIdx < m->numMethods; mIdx++) {
                int methodStart = 0;
                int methodEnd = 0;
                bool possible = true;

                for(int j=0; j<m->numSubTasks[mIdx]; j++) {
                    int subT = m->subTasks[mIdx][j];
                    if (taskCompletion[subT] == INT_MAX) {
                        possible = false; 
                        break;
                    }
                    if (taskCompletion[subT] > methodEnd) {
                        methodEnd = taskCompletion[subT];
                    }
                }

                if (possible) {
                    int parentTask = m->decomposedTask[mIdx];
                    if (methodEnd < taskCompletion[parentTask]) {
                        taskCompletion[parentTask] = methodEnd;
                        changed = true;
                    }
                }
            }
        }

        // 5. Calculate Global Makespan
        int globalMax = 0;
        
        // Check Goal/Fringe
        for (int goalFact = g.getFirst(); goalFact >= 0; goalFact = g.getNext()) {
             // In RC encoding, goal facts often represent tasks (bur-...)
             // We check the time of these facts.
             if (factTime[goalFact] != INT_MAX) {
                 if (factTime[goalFact] > globalMax) {
                     globalMax = factTime[goalFact];
                 }
             } else {
                 // Goal unreachable
                 return INT_MAX; // Or a large number
             }
        }
        
        // Also check if 'g' contains task IDs directly (if not mapped to facts)
        // This depends on how 'g' is populated. 
        // Assuming 'g' contains facts for now as per standard RC.

        if (globalMax == INT_MAX) return INT_MAX;
        // Return remaining makespan: h = f - g (where f = total from S0, g = executed prefix)
        return std::max(0, globalMax - nodeMakespan);
    }

} /* namespace progression */