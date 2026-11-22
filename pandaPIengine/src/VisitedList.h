#ifndef PANDAPIENGINE_VISITEDLIST_H
#define PANDAPIENGINE_VISITEDLIST_H

#include <stdint.h>
#include "../ProgressionNetwork.h"
#include <unordered_map>
#include <vector>
#include <map>
#include "../intDataStructures/HashTable.h"
#include "search/fringes/OneQueueWAStarFringe.h"


// XXX magic number, restricts number of copies of a task per task net to 255, thereafter the hash becomes incorrect
// 3 -> up to three bits for (number-1), max is 8, thus whole number is max 225
const int number_of_bits_for_task_count = 3;
const int max_task_count = (1 << (1 << number_of_bits_for_task_count)) - 1;

using namespace progression;

// The part of a node's timing state that determines how the makespan tracker
// schedules the rest of the plan. Two nodes with the same logical state and
// task network are only interchangeable if one is no later in every component
// (see dominates()).
//
// Fact availability times (factEarliestTrue) are deliberately left out. A node
// that is later only there can usually still reach the better schedule through
// another interleaving of the same steps, and leaving them out roughly halves
// the extra expansions on Medical. This is backed by differential fuzzing, not
// a proof; the read and delete times below are needed (they decide whether a
// deleter must wait, which reordering cannot undo).
struct TimingSignature {
	int makespan = 0;
	vector<int> latestDeleted;  // factLatestDeleted
	vector<int> lastNeeded;     // factLastNeeded
	// HTN release times in a canonical order of the task network (hash modes
	// only). In GI mode releases are compared per occurrence inside the
	// isomorphism search instead, so this stays empty there.
	vector<int> releases;
	// GI mode: each task label's release times, sorted. Some label-preserving
	// pairing of occurrences respects "no later" iff these are elementwise <=,
	// a cheap necessary condition checked before the isomorphism search.
	map<int, vector<int>> releasesByLabel;

	bool dominates(const TimingSignature &other) const;
};

struct VisitedList{
	VisitedList(Model *m, bool _noVisitedCheck, bool _noReOpening, aStar _costMode, bool _taskHash, bool _taskSequenceHash, bool _topologicalOrdering, bool _orderPairs, bool _layers, bool _allowGIcheck, bool _allowedToUseParallelSequences);
	
	// insert the node into the visited list
	// @returns true if the node was *new* and false, if the node was already contained in the visited list
	bool insertVisi(searchNode * node);

	bool canDeleteProcessedNodes;
	
	int attemptedInsertions = 0;
	int uniqueInsertions = 0;
	int subHashCollision = 0;
	double time = 0;

private:
	Model * htn;
	// automatically determined configuration
	bool useTotalOrderMode;
	bool useSequencesMode;
	int bitsNeededPerTask;
	// configuration given by the user
	bool noVisitedCheck;
	bool noReopening;
	aStar costMode;
	bool GIcheck;
	bool taskHash;
	bool sequenceHash;
	bool topologicalOrdering;
	bool orderPairs;
	bool layers;

	uint64_t hashingP;

	hash_table * stateTable;

	uint64_t taskCountHash(searchNode * n);
	uint64_t taskSequenceHash(vector<int> & tasks);

    vector<int> topSort(searchNode *n);

    uint32_t getHash(vector<int> *pVector);
	int routeCost(searchNode *n) const;

	// Makespan mode prunes by componentwise dominance of the timing state: the
	// scalar makespan alone does not determine future finish times.
	bool timingDominance() const { return costMode == gValMakespan && !noReopening; }
	TimingSignature timingSignature(searchNode *n) const;
};


#endif
