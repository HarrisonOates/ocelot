/*
 * hhSimple.h
 *
 *  Created on: 29.01.2023
 *      Author: Gregor Behnke
 */

#ifndef HHSIMPLE_H_
#define HHSIMPLE_H_

#include "../Model.h"
#include "Heuristic.h"
#include "../intDataStructures/IntPairHeap.h"
#include "../intDataStructures/bucketSet.h"
#include "../intDataStructures/noDelIntSet.h"
#include <climits>

namespace progression {

class hhModDepth: public Heuristic {
private:
	bool invert;
public:
	hhModDepth(Model* htn, int index, bool _invert);
	virtual ~hhModDepth();
	string getDescription(){ return "modDepth(invert="+to_string(invert)+")";}
	void setHeuristicValue(searchNode *n, searchNode *parent, int action);
	void setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method);
};

class hhMixedModDepth: public Heuristic {
private:
	bool invert;
public:
	hhMixedModDepth(Model* htn, int index, bool _invert);
	virtual ~hhMixedModDepth();
	string getDescription(){ return "mixedModDepth(invert="+to_string(invert)+")";}
	void setHeuristicValue(searchNode *n, searchNode *parent, int action);
	void setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method);
};

class hhCost: public Heuristic {
private:
	bool invert;
public:
	hhCost(Model* htn, int index, bool _invert);
	virtual ~hhCost();
	string getDescription(){ return "cost(invert="+to_string(invert)+")";}
	void setHeuristicValue(searchNode *n, searchNode *parent, int action);
	void setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method);
};

class hhMakespan: public Heuristic {
private:
	bool invert;
    
    // Data structures for makespan computation
    IntPairHeap<int>* queue;
    int* layerPropInit;
    int* numSatPrecs;
    int* layerOp;
    int* layerProp;
    
    // Helper data structures
    noDelIntSet reachableActionsSet;
    noDelIntSet goalSet;
    
    // Core makespan computation
    int computeMakespan(vector<bool>& state, noDelIntSet& goals);
    
public:
	hhMakespan(Model* htn, int index, bool _invert);
	virtual ~hhMakespan();
	string getDescription(){ return "makespan(invert="+to_string(invert)+")";}
	void setHeuristicValue(searchNode *n, searchNode *parent, int action);
	void setHeuristicValue(searchNode *n, searchNode *parent, int absTask, int method);
};

} /* namespace progression */

#endif /* HHSIMPLE_H_ */
