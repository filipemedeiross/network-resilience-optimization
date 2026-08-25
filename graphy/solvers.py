from mip import Model, xsum, MAXIMIZE


# Class to solve the water distribution network
class SolverWaterDistribution:
    def __init__(self):
        self.model = None

        self.x = None
        self.y = None

    def create_model(self, network):
        # Getting the origin and destination
        for node, prop in network.nodes.data("node_prop"):
            if prop == "origin":
                origin = node
            elif prop == "dest":
                dest = node
        
        # Create a model
        self.model = Model()  # default is sense MINIMIZE and solver CBC
        
        # Defining the variables and objective function coefficients
        self.x = x = [self.model.add_var(var_type="B")
                      for _ in network.nodes]
        self.y = y = {e : self.model.add_var(obj=1.0, var_type="B")
                      for e in network.edges}
            
        # Defining the constraints
        self.model += x[origin] == 0
        self.model += x[dest] == 1
        
        for v, w, color in network.edges.data("color"):
            self.model += y[v, w] >= x[v] - x[w]
            self.model += y[v, w] >= x[w] - x[v]
            
            if color == "red":
                self.model += y[v, w] == 0
    
    def optimize(self):
        self.model.optimize()

    @property
    def objective_value(self):
        return self.model.objective_value

    @property
    def disconnected_nodes(self):
        return [node for node, var in enumerate(self.x) if var.x]

    @property
    def edges_to_remove(self):
        return [edge for edge, var in self.y.items() if var.x]

# Class to solve the military distribution network
class SolverMilitaryDistribution:
    def __init__(self):
        self.model = None

        self.x = None
        self.y = None

    def create_model(self, network, fire_power=6):
        # Create a model
        self.model = Model(sense=MAXIMIZE)
        
        # Defining the variables and objective function coefficients
        self.x = x = [None]*network.number_of_nodes()
        self.y = y = [None]*network.number_of_nodes()
        
        for v, prop in network.nodes.data("node_prop"):
            x[v] = self.model.add_var(obj=1.0, var_type="B")
            y[v] = self.model.add_var(var_type="B")
            
            if prop == "headquarters":
                self.model += x[v] == 0
        
        # Defining the constraints
        self.model += xsum(c_i*y[i] for i, c_i in network.nodes.data("endurance")) <= fire_power
        
        for v, w in network.edges:
            self.model += y[v] + y[w] >= x[v] - x[w]
            self.model += y[v] + y[w] >= x[w] - x[v]

    def optimize(self):
        self.model.optimize()

    @property
    def objective_value(self):
        return self.model.objective_value

    @property
    def disconnected_nodes(self):
        return [node for node, var in enumerate(self.x) if var.x]

    @property
    def nodes_to_remove(self):
        return [node for node, var in enumerate(self.y) if var.x]
