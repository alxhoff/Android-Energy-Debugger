import networkx as nx
import matplotlib.pyplot as plt
from graphviz import Digraph


class Grapher:

    def __init__(self, process_tree):
        self.pt = process_tree

    def drawGraph(self):
        A = nx.nx_agraph.to_agraph(self.pt.process_branches[28].graph)
        A.draw("/home/alxhoff/Downloads/test.png", format='png', prog='dot')

        subgraph_count = len(self.pt.process_branches[28].tasks)
        for x,task in enumerate(self.pt.process_branches[28].tasks):
            task_graph = nx.nx_agraph.to_agraph(task.graph)
            task_graph.draw("/home/alxhoff/Downloads/test_task" + str(x) + ".png", format='png',
                            prog='dot')

        # test with subgraph 0
        # add as subgraph, binding it to parent task node
        A_nodes = A.nodes()
        for x, node in enumerate(A_nodes):
            cur_node = node.get_handle()
            print cur_node

        print subgraph_count




