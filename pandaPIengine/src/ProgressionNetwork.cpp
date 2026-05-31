/*
 * ProgressionNetwork.cpp
 *
 *  Created on: 25.09.2017
 *      Author: Daniel Höller
 */

#include <stdlib.h>
#include <iomanip>
#include <algorithm>
#include "ProgressionNetwork.h"
#include "Model.h"
#include <fstream>
#include <unordered_map>

namespace progression {

#ifdef TRACESOLUTION
int currentSolutionStepInstanceNumber = 0;
#endif

#ifdef SAVESEARCHSPACE
int currentSearchNodeID = 0;
#endif 

////////////////////////////////
// solutionStep
////////////////////////////////
solutionStep::~solutionStep() {
	if (prev != nullptr) {
		prev->pointersToMe--;
		if (prev->pointersToMe == 0) {
			delete prev;
		}
	}
}
////////////////////////////////
// planStep
////////////////////////////////

bool planStep::operator==(const planStep &that) const {
	return (this->id == that.id);
}

planStep::~planStep() {
	for (int i = 0; i < numSuccessors; i++) {
		planStep* succ = successorList[i];
		succ->pointersToMe--;
		if (succ->pointersToMe == 0) {
			delete succ;
		}
	}
	delete[] successorList;
	delete[] reachableT;
#ifdef RCHEURISTIC
	delete[] goalFacts;
#endif
}

////////////////////////////////
// searchNode
////////////////////////////////

bool searchNode::operator<(searchNode other) const {
	return heuristicValue > other.heuristicValue;
}

searchNode::searchNode() {
	modificationDepth = -1;
	mixedModificationDepth = -1;
	planMakespan = 0;
	unconstraintPrimitive = nullptr;
	unconstraintAbstract = nullptr;
	numAbstract = 0;
	numPrimitive = 0;
	solution = nullptr;
	taskEarliestStart = nullptr;
#ifdef SAVESEARCHSPACE
	searchNodeID = currentSearchNodeID++;
#endif
}

searchNode::~searchNode() {
	for (int i = 0; i < numAbstract; i++) {
		unconstraintAbstract[i]->pointersToMe--;
		if (unconstraintAbstract[i]->pointersToMe == 0) {
			delete unconstraintAbstract[i];
		}
	}
	for (int i = 0; i < numPrimitive; i++) {
		unconstraintPrimitive[i]->pointersToMe--;
		if (unconstraintPrimitive[i]->pointersToMe == 0) {
			delete unconstraintPrimitive[i];
		}
	}
	if (solution != nullptr) {
		solution->pointersToMe--;
		if (solution->pointersToMe == 0) {
			delete solution;
		}
	}
	delete[] unconstraintAbstract;
	delete[] unconstraintPrimitive;
	
	delete[] heuristicValue;

	delete[] containedTasks;
	delete[] containedTaskCount;
	delete[] factEarliestTrue;
	delete taskEarliestStart;
	delete pendingObservationPredecessors;

	// todo: need to destroy heuristic payload. To do so, I need to know the number of heuristics used
}


void searchNode::printDFS(planStep * s, map<planStep*,int> & psp, set<pair<planStep*,planStep*>> & orderpairs){
	if (psp.count(s)) return;
	int num = psp.size();
	psp[s] = num;
	for (int ns = 0; ns < s->numSuccessors; ns++){
		orderpairs.insert({s,s->successorList[ns]});
		this->printDFS(s->successorList[ns], psp, orderpairs);
	}
}


void searchNode::printNode(std::ostream & out){
	out << "Node: " << this << endl;
	for (int a = 0; a < this->numAbstract; a++)  cout << "\tUC A: " << this->unconstraintAbstract[a] << endl;
	for (int a = 0; a < this->numPrimitive; a++) cout << "\tUV P: " << this->unconstraintPrimitive[a] << endl;
	
	
	map<planStep*,int> psp;
	set<pair<planStep*,planStep*>> orderpairs;
	for (int a = 0; a < this->numAbstract; a++)  this->printDFS(this->unconstraintAbstract[a], psp, orderpairs);
	for (int a = 0; a < this->numPrimitive; a++) this->printDFS(this->unconstraintPrimitive[a],psp, orderpairs);

	// names
	map<int,planStep*> bpsp;
	for (auto [a,b] : psp) bpsp[b] = a;
	for (int i = 0; i < bpsp.size(); i++) out << "\t" << setw(2) << i << " " << bpsp[i] << " " << bpsp[i]->task << endl;

	// ordering
	for (auto [a,b] : orderpairs) out << "\t" << setw(2) << psp[a] << " < " << setw(2) << psp[b] << endl;
}

void searchNode::node2Dot(std::ostream & out){
	out << "digraph searchNode { "  << endl;
	map<planStep*,int> psp;
	set<pair<planStep*,planStep*>> orderpairs;
	for (int a = 0; a < this->numAbstract; a++)  this->printDFS(this->unconstraintAbstract[a], psp, orderpairs);
	for (int a = 0; a < this->numPrimitive; a++) this->printDFS(this->unconstraintPrimitive[a],psp, orderpairs);

	// names
	for (auto [a,b] : psp) out << "\t" << "n" << a << "[label=\"" << a->task <<  "\"];" << endl;

	// ordering
	for (auto [a,b] : orderpairs) out << "\tn" << a << " -> n" << b << ";" << endl;
	out << "}"; 
}




#ifdef TRACESOLUTION
pair<string,int> extractSolutionFromSearchNode(Model * htn, searchNode* tnSol){
	int sLength = 0;
	string sol = "";
	solutionStep* sost = tnSol->solution;
	bool done = sost == nullptr || sost->prev == nullptr;

	map<int,vector<pair<int,int>>> children;
	vector<pair<int,string>> decompositionStructure;

	int root = -1;

	while (!done) {
		sLength++;
		if (sost->method >= 0){
			pair<int,string> application;
			application.first = sost->mySolutionStepInstanceNumber;
			application.second = htn->taskNames[sost->task] + " -> " + htn->methodNames[sost->method];
			decompositionStructure.push_back(application);
			if (sost->task == htn->initialTask) root = application.first;
		} else {
			sol = to_string(sost->mySolutionStepInstanceNumber) + " " +
					htn->taskNames[sost->task] + "\n" + sol;
		}
		
		if (sost->mySolutionStepInstanceNumber != 0)
			children[sost->parentSolutionStepInstanceNumber].push_back(
					make_pair(
						sost->myPositionInParent,
						sost->mySolutionStepInstanceNumber));
		
		done = sost->prev == nullptr;
		sost = sost->prev;
	}

	sol = "==>\n" + sol;
	sol = sol + "root " + to_string(root) + "\n";
	for (auto x : decompositionStructure){
		sol += to_string(x.first) + " " + x.second;
		sort(children[x.first].begin(), children[x.first].end());
		for (auto [_,y] : children[x.first])
			sol += " " + to_string(y);
		sol += "\n";
	}

	sol += "<==";

	return make_pair(sol,sLength);
}
#endif


pair<string,int> printTraceOfSearchNode(Model* htn, searchNode* tnSol){
	int sLength = 0;
	string sol = "";
	solutionStep* sost = tnSol->solution;
	bool done = sost == nullptr || sost->prev == nullptr;
	while (!done) {
		sLength++;
		if (sost->method >= 0)
			sol = htn->methodNames[sost->method] + " @ "
					+ htn->taskNames[sost->task] + "\n" + sol;
		else
			sol = htn->taskNames[sost->task] + "\n" + sol;
		done = sost->prev == nullptr;
		sost = sost->prev;
	}

	return make_pair(sol,sLength);
}

}

namespace progression {
void exportImpliedOrderings(Model* htn, searchNode* tnSol){
#ifdef TRACESOLUTION
	if (!tnSol) return;
	solutionStep* s = tnSol->solution;
	if (!s) return;

	// Maps
	// id -> (task, method)
	std::map<int, std::pair<int,int>> nodeInfo;
	// parentID -> vector of (position, childID)
	std::map<int, std::vector<std::pair<int,int>>> children;
	int root = -1;

	// Walk the chain; it's in reverse order (last applied at end), but we only collect info
	while (s && s->prev) {
		nodeInfo[s->mySolutionStepInstanceNumber] = {s->task, s->method};
		if (s->mySolutionStepInstanceNumber != 0) {
			children[s->parentSolutionStepInstanceNumber].push_back({s->myPositionInParent, s->mySolutionStepInstanceNumber});
		}
		if (s->method >= 0 && s->task == htn->initialTask) {
			root = s->mySolutionStepInstanceNumber;
		}
		s = s->prev;
	}

	// Sort children by position for each parent
	for (auto &kv : children) {
		auto &vec = kv.second;
		std::sort(vec.begin(), vec.end(), [](const std::pair<int,int> &a, const std::pair<int,int> &b){ return a.first < b.first; });
	}

	// Helper to collect primitive leaves under a node id
	std::function<void(int, std::vector<int>&)> collect_leaves = [&nodeInfo, &children, &collect_leaves](int nid, std::vector<int>& out){
		auto it = nodeInfo.find(nid);
		if (it == nodeInfo.end()) return;
		int method = it->second.second;
		if (method < 0) { // primitive
			out.push_back(nid);
			return;
		}
		auto chIt = children.find(nid);
		if (chIt == children.end()) return;
		for (auto &pc : chIt->second) {
			collect_leaves(pc.second, out);
		}
	};

	// Produce edges between primitive leaves induced by each method's ordering
	std::set<std::pair<int,int>> edges;
	for (auto &kv : nodeInfo) {
		int nid = kv.first;
		int method = kv.second.second;
		if (method < 0) continue; // only for method nodes

		// Map subtask position -> child id (if present)
		std::map<int,int> pos2child;
		auto chIt = children.find(nid);
		if (chIt != children.end()) {
			for (auto &pc : chIt->second) pos2child[pc.first] = pc.second;
		}

		int numO = htn->numOrderings[method];
		for (int o = 0; o+1 < numO; o += 2) {
			int p = htn->ordering[method][o];
			int s2 = htn->ordering[method][o+1];
			if (!pos2child.count(p) || !pos2child.count(s2)) continue;
			int leftChild = pos2child[p];
			int rightChild = pos2child[s2];

			std::vector<int> L, R;
			collect_leaves(leftChild, L);
			collect_leaves(rightChild, R);
			for (int u : L) for (int v : R) if (u != v) edges.insert({u,v});
		}
	}

	// Write outputs
	std::ofstream amap("panda_actions.map");
	std::ofstream lidx("panda_order.lock");
	std::ofstream lname("panda_order.lock.names");

	// Write only primitive nodes to action map
	for (auto &kv : nodeInfo) {
		int nid = kv.first;
		int task = kv.second.first;
		int method = kv.second.second;
		if (method >= 0) continue;
		std::string name = (task >= 0 && task < htn->numTasks) ? htn->taskNames[task] : std::string("task_") + std::to_string(task);
		amap << nid << " " << name << "\n";
	}

	for (auto &e : edges) {
		int u = e.first, v = e.second;
		lidx << u << " " << v << "\n";
		auto iu = nodeInfo.find(u), iv = nodeInfo.find(v);
		if (iu != nodeInfo.end() && iv != nodeInfo.end()) {
			int tu = iu->second.first, tv = iv->second.first;
			std::string nu = (tu >= 0 && tu < htn->numTasks) ? htn->taskNames[tu] : std::string("task_") + std::to_string(tu);
			std::string nv = (tv >= 0 && tv < htn->numTasks) ? htn->taskNames[tv] : std::string("task_") + std::to_string(tv);
			lname << nu << " " << nv << "\n";
		}
	}
#else
	std::cout << "[orderOutput] TRACESOLUTION not enabled at compile time; skipping export." << std::endl;
#endif
}
} // end namespace progression
