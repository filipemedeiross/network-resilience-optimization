import streamlit as st
import networkx as nx
from graphy.utils import plot_water_network, interrupt_flow


# Initial page settings
st.set_page_config(page_title="Water Distribution Network", layout="centered")

# Getting variables that will be used
generator = st.session_state.generator
water_network = st.session_state.water_network
water_solver = st.session_state.water_solver

# Auxiliary functions
def disabled_attempts():
    st.session_state.disabled = True

# Sidebar features
colside1, colside2, colside3 = st.sidebar.columns((4.6, 2.9, 2.5))

colside1.markdown("### ⚙️ Options")

if colside2.button("Update"):
    st.session_state.disabled = False

    water_network = st.session_state.water_network = generator.water_network()

if colside3.button("Solve"):
    st.session_state.disabled = True

    water_solver.create_model(water_network)
    water_solver.optimize()
    
    water_network.remove_edges_from(water_solver.edges_to_remove)  # removing edges obtained by the model    
    interrupt_flow(water_network, water_solver.disconnected_nodes)  # updating the flow on nodes
    
    st.sidebar.info(f"Solver removed {water_solver.objective_value} edges\
                      to disconnect destination supply from origin", icon="ℹ️")   

# User entries
with st.sidebar:
    form = st.form("user_entries", clear_on_submit=True)

    edges = form.multiselect("Choose the edges", [(v, w) for v, w, c in water_network.edges.data("color") if c == "blue"],
                             disabled=st.session_state.disabled, help="Pipes chosen to be removed from the network")

    if form.form_submit_button("Try", on_click=disabled_attempts):
        # Saving information about the optimal solution
        water_solver.create_model(water_network)
        water_solver.optimize()

        # Perform user attempt
        water_network.remove_edges_from(edges)
    
        for c in map(water_network.subgraph, nx.connected_components(water_network)):
            if "origin" not in nx.get_node_attributes(c, "node_prop").values():
                interrupt_flow(water_network, c.nodes)

        # Checking:
        # if the user has completed the objective
        # difference between user solution and integer programming model solution
        win = False
        for _, node_dict in water_network.nodes.items():
            if node_dict["node_prop"] == "dest" and node_dict["flow"] == False:
                win = True
                break

        if win:
            solution_gap = len(edges) - water_solver.objective_value

            st.success("You have successfully stopped provisioning from the origin to the destination", icon="✅")
            st.balloons()  # congratulating user who managed to accomplish the goal
            
            if solution_gap:
                st.warning(f"Your solution was worse than the optimum by {solution_gap} units", icon="⚠️")
        else:
            st.error("You must stop provisioning from the origin to the destination", icon="🚨")

# General explanation of the problem
st.markdown("""
<h1 style='text-align: center'>Disconnecting a water distribution network</h1>
""", unsafe_allow_html=True)

st.markdown("""---""")

st.markdown("""
<h6 style='text-align: center'>
The objective of this problem is to remove pipes (edges) in order to interrupt the water supply
from origin to destination. The origin and destination vertices are the only distinct ones to be
displayed in the graph, to differentiate them notice that the destination has a red x in its center.
</h6>
<h6 style='text-align: center'>
The sidebar has the mechanisms for interacting with the graph and in it you can try a solution by
choosing edges to be removed. After each attempt you will have as feedback the information if you
completed the objective (a feasible solution) and if you succeed, your solution will be compared
with the optimal solution obtained by integer programming.
</h6>
""", unsafe_allow_html=True)

st.pyplot(plot_water_network(water_network))

with st.expander("**More information**"):
    st.write(r"""
        Given that the problem is represented as a graph $G=(V, E)$ and has origin and destination nodes and edges
        $(i, j) \in C$ colored red that cannot be removed, let's consider the variables $x_{i}$ $\forall i \in V$
        which represents whether the node $i$ is disconnected with respect to the origin and $y_{i, j}$
        $\forall (i, j) \in E$ which represents whether the edge $(i, j)$ has been removed.
        
        Therefore, we have the following integer programming model:

        $$
        Min \sum\limits_{(i, j) \in E}y_{i, j}
        $$

        Subject to:

        $$
        x_{origin} = 0
        $$

        $$
        x_{destination} = 1
        $$

        $$
        y_{i, j} \geq x_{i} - x_{j}, \space\space\space \forall (i, j) \in E
        $$

        $$
        y_{i, j} \geq x_{j} - x_{i}, \space\space\space \forall (i, j) \in E
        $$

        $$
        y_{i, j} = 0, \space\space\space \forall (i, j) \in C
        $$

        $$
        x_{i} \in \{0, 1\}, \space\space\space \forall i \in V
        $$

        $$
        y_{i, j} \in \{0, 1\}, \space\space\space \forall (i, j) \in E
        $$
    """)
