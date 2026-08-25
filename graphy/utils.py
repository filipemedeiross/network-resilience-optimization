import networkx as nx
import matplotlib.pyplot as plt
from .constants import images, pos


# Auxiliary function to plot a water distribution network
def plot_water_network(G):
    fig, ax = plt.subplots(figsize=(17, 15))

    nx.draw_networkx_edges(G, pos, width=6, edge_color=nx.get_edge_attributes(G, "color").values())
    nx.draw_networkx_edge_labels(G, pos, {edge : edge for edge in G.edges})

    tr_figure = ax.transData.transform
    tr_axes = fig.transFigure.inverted().transform

    icon_size = (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.005
    icon_center = icon_size / 2.0

    for n, image in G.nodes.data("image"):
        xf, yf = tr_figure(pos[n])
        xa, ya = tr_axes((xf, yf))

        a = plt.axes([xa - icon_center, ya - icon_center, icon_size, icon_size])

        a.imshow(image)
        a.axis("off")

    ax.axis("off")

    return fig

# Auxiliary function to plot a military distribution network
def plot_military_network(G):
    fig, ax = plt.subplots(figsize=(17, 15))

    nx.draw_networkx_edges(G, pos, width=6, edge_color="blue")
    nx.draw_networkx_edge_labels(G, pos, {edge : edge for edge in G.edges})

    tr_figure = ax.transData.transform
    tr_axes = fig.transFigure.inverted().transform

    icon_size = (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.01
    icon_center = icon_size / 2.0

    for n, node in G.nodes.items():
        xf, yf = tr_figure(pos[n])
        xa, ya = tr_axes((xf, yf))

        if node["node_prop"] == "headquarters":
            a = plt.axes([xa - 2*icon_center, ya - 2*icon_center, 2*icon_size, 2*icon_size])
        else:
            a = plt.axes([xa - icon_center, ya - icon_center, icon_size, icon_size])

        a.imshow(node["image"])
        a.axis("off")

    ax.axis("off")

    return fig

# Function to update the flow at the vertices of a water distribution network
def interrupt_flow(G, nodes):
    for node in nodes:
        G.nodes[node]["flow"] = False
        G.nodes[node]["image"] = images["node"]

# Function to update the provided attribute at the vertices of a military distribution network
def interrupt_supply(G, nodes):
    for node in nodes:
        if node in G.nodes:
            G.nodes[node]["provided"] = False
