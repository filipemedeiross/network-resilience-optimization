import numpy as np
import networkx as nx
from .constants import images


# Class that will help create a connected graph
class UnionFind:
    def __init__(self, n):
        self.n = n  # number of trees in the forest
        self.v = [i for i in range(n)]  # initialize each disjoint set

    def find(self, u):
        while u != self.v[u]:
            self.v[u] = self.v[self.v[u]]  # compression technique
            u = self.v[u]
            
        return u
    
    def union(self, u, v):
        root_u, root_v = self.find(u), self.find(v)
        
        if root_u == root_v:
            return False  # union was not performed
        else:
            self.v[root_v] = root_u
            self.n -= 1

            return True  # union was not performed

# Class that defines the graph generator
class GraphGenerator:
    def __init__(self, N=100):
        self.N = N  # number os cells (default is a 10x10 grid)
        self.shape = int(self.N**0.5)

        self.edges = self.generate_edges()

    def water_network(self):        
        # Copy the edges and create a disjoint set for each cell
        edges = self.edges.copy()
        forest = UnionFind(self.N)
        
        # Generate a graph
        G = nx.Graph()
        
        # Adding nodes
        # Value of flow is True because, initially, it is a connected graph
        for v in range(self.N):
            G.add_node(v, node_prop=None, flow=True, image=images["node_water"])
        
        step = 2*self.shape + 2
        
        origin = np.random.randint(0, self.N)
        dest = np.random.choice([v for v in range(self.N) if v < origin-step or v > origin+step])
    
        G.nodes[origin]["node_prop"] = "origin"
        G.nodes[origin]["image"] = images["origin"]
        G.nodes[dest]["node_prop"] = "dest"
        G.nodes[dest]["image"] = images["dest"]
        
        # Adding edges
        # Makes sure that the initial graph will be connected
        # Edges with endpoint at origin or dest are marked as red (constraint that prevents the deletion of an edge)
        while forest.n > 1:
            v, w = edges.pop(np.random.randint(0, len(edges)))
            forest.union(v, w)
            G.add_edge(v, w, color="red" if G.nodes[v]["node_prop"] or G.nodes[w]["node_prop"] else "blue")
                
        return G
    
    def military_network(self):        
        # Copy the edges and create a disjoint set for each cell
        edges = self.edges.copy()
        forest = UnionFind(self.N)
        
        # Generate a graph
        G = nx.Graph()
        
        # Adding nodes
        endurances = np.random.choice(np.arange(1, 4), self.N, [0.2, 0.2, 0.6])
        for v, e in enumerate(endurances):
            G.add_node(v, node_prop=None, endurance=e, provided=True, image=images[f"base_{e}"])

        # Adding edges
        # Makes sure that the initial graph will be connected
        while forest.n > 1:
            v, w = edges.pop(np.random.randint(0, len(edges)))
            forest.union(v, w)
            G.add_edge(v, w)

        # The headquarters is the most difficult enemy military installation to attack
        h = np.random.randint(0, self.N)
    
        G.nodes[h]["node_prop"] = "headquarters"
        G.nodes[h]["endurance"] = 10000
        G.nodes[h]["image"] = images["headquarters"]

        # Harder to attack military installations adjacent to headquarters
        for v in G[h]:
            G.nodes[v]["node_prop"] = "secure"
            G.nodes[v]["endurance"] = 100
                
        return G
                
    def available_edges(self, v):
        available_edges = []

        # Takes into account the dimensions of the grid
        if (v+1) % self.shape != 0:
            available_edges.append((v, v+1))
        if v+self.shape < self.N:
            available_edges.append((v, v+self.shape))

        return available_edges  # possible edges in cell v

    def generate_edges(self):
        edges = []
        
        for v in range(self.N):
            edges.extend(self.available_edges(v))

        return edges
