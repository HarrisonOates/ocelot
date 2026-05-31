/*
 * hhSimple.h
 *
 *  Created on: 29.01.2023
 *      Author: Gregor Behnke
 */

#include "hhSimple.h"
#include <cassert>
#include <cstring>
#include <climits>

namespace progression {

hhModDepth::hhModDepth(Model* htn, int index, bool _invert) : Heuristic(htn, index){invert = _invert;}
hhModDepth::~hhModDepth() {}

void hhModDepth::setHeuristicValue(searchNode *n, searchNode *parent, int action) {
	n->heuristicValue[index] = n->modificationDepth * (invert?-1:1);
	n->goalReachable = true;
}
void hhModDepth::setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method) {
	n->heuristicValue[index] = n->modificationDepth * (invert?-1:1);
	n->goalReachable = true;
}

hhMixedModDepth::hhMixedModDepth(Model* htn, int index, bool _invert) : Heuristic(htn, index){invert = _invert;}
hhMixedModDepth::~hhMixedModDepth() {}

void hhMixedModDepth::setHeuristicValue(searchNode *n, searchNode *parent, int action) {
	n->heuristicValue[index] = n->mixedModificationDepth * (invert?-1:1);
	n->goalReachable = true;
}
void hhMixedModDepth::setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method) {
	n->heuristicValue[index] = n->mixedModificationDepth * (invert?-1:1);
	n->goalReachable = true;
}

hhCost::hhCost(Model* htn, int index, bool _invert) : Heuristic(htn, index){invert = _invert;}
hhCost::~hhCost() {}

void hhCost::setHeuristicValue(searchNode *n, searchNode *parent, int action) {
	n->heuristicValue[index] = n->actionCosts * (invert?-1:1);
	n->goalReachable = true;
}
void hhCost::setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method) {
	n->heuristicValue[index] = n->actionCosts * (invert?-1:1);
	n->goalReachable = true;
}

hhMakespan::hhMakespan(Model* htn, int index, bool _invert) : Heuristic(htn, index) {
    invert = _invert;
    
    // Initialize data structures for makespan computation
    layerPropInit = new int[htn->numStateBits];
    for (int i = 0; i < htn->numStateBits; i++) {
        layerPropInit[i] = UNREACHABLE;
    }
    
    queue = new IntPairHeap<int>(htn->numStateBits * 2);
    numSatPrecs = new int[htn->numActions];
    layerOp = new int[htn->numActions];
    layerProp = new int[htn->numStateBits];
    
    reachableActionsSet.init(htn->numActions);
    goalSet.init(htn->numStateBits);
}

hhMakespan::~hhMakespan() {
    delete[] layerPropInit;
    delete[] numSatPrecs;
    delete[] layerOp;
    delete[] layerProp;
    delete queue;
}

int hhMakespan::computeMakespan(vector<bool>& state, noDelIntSet& goals) {
    // Clear data structures
    reachableActionsSet.clear();
    memcpy(numSatPrecs, htn->numPrecs, sizeof(int) * htn->numActions);
    memcpy(layerProp, layerPropInit, sizeof(int) * htn->numStateBits);
    
    // Initialize the first layer (layer 0) with facts that are true in initial state
    queue->clear();
    for (unsigned int f = 0; f < state.size(); f++) {
        if (state[f]) {
            queue->add(0, f);
            layerProp[f] = 0;
        }
    }
    
    // Initialize preconditionless actions (can be applied at layer 0)
    for (int i = 0; i < htn->numPrecLessActions; i++) {
        int ac = htn->precLessActions[i];
        reachableActionsSet.insert(ac);
        layerOp[ac] = 0; // Can be executed at layer 0
        
        // Add effects to layer 1
        for (int iAdd = 0; iAdd < htn->numAdds[ac]; iAdd++) {
            int fAdd = htn->addLists[ac][iAdd];
            if (layerProp[fAdd] == UNREACHABLE || layerProp[fAdd] > 1) {
                layerProp[fAdd] = 1; // Effects appear at next layer
                queue->add(layerProp[fAdd], fAdd);
            }
        }
    }
    
    // Propagate through layers using a modified Dijkstra-like algorithm
    while (!queue->isEmpty()) {
        int pLayer = queue->topKey(); // Current layer
        int prop = queue->topVal(); // Current proposition
        queue->pop();
        
        if (layerProp[prop] < pLayer)
            continue; // Already processed at earlier layer
            
        // Check all actions that have this proposition as a precondition
        for (int iOp = 0; iOp < htn->precToActionSize[prop]; iOp++) {
            int op = htn->precToAction[prop][iOp];
            
            // Update the maximum layer required for this action's preconditions
            if (layerOp[op] < pLayer) {
                layerOp[op] = pLayer;
            }
            
            // Check if all preconditions are satisfied
            if ((--numSatPrecs[op] == 0)) {
                reachableActionsSet.insert(op);
                
                // Action can execute at layerOp[op], effects appear at layerOp[op] + 1  
                int effectLayer = layerOp[op] + 1;
                
                for (int iF = 0; iF < htn->numAdds[op]; iF++) {
                    int f = htn->addLists[op][iF];
                    if (layerProp[f] == UNREACHABLE || layerProp[f] > effectLayer) {
                        layerProp[f] = effectLayer;
                        queue->add(layerProp[f], f);
                    }
                }
            }
        }
    }
    
    // Find the maximum layer among goal facts
    int maxGoalLayer = 0;
    for (int f = goals.getFirst(); f >= 0; f = goals.getNext()) {
        if (layerProp[f] == UNREACHABLE) {
            return UNREACHABLE; // Goal unreachable
        }
        if (layerProp[f] > maxGoalLayer) {
            maxGoalLayer = layerProp[f];
        }
    }
    
    return maxGoalLayer;
}

void hhMakespan::setHeuristicValue(searchNode *n, searchNode *parent, int action) {
    // Clear and populate goal set from HTN goals
    goalSet.clear();
    for (int i = 0; i < htn->gSize; i++) {
        goalSet.insert(htn->gList[i]);
    }
    
    // Compute makespan heuristic
    int heurValue = computeMakespan(n->state, goalSet);
    
    // Apply inversion if requested
    n->heuristicValue[index] = (heurValue == UNREACHABLE) ? UNREACHABLE : (heurValue * (invert ? -1 : 1));
    n->goalReachable = (heurValue != UNREACHABLE);
}

void hhMakespan::setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method) {
    // Same implementation for both action and method application
    setHeuristicValue(n, parent, -1);
}

} /* namespace progression */
