# NETWORK RESILIENCE ANALYSIS

Go to the app and access all the features and more information about the project:

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://filipemedeiross-resilience-of-a-graph-using-in-main-page-kkv4nk.streamlit.app/)

## Application of integer programming in analyzing the resilience of a network

The objective of this project is to demonstrate, without much scientific rigor, the importance of connectivity analysis in investigating the resilience and robustness of a network represented as a graph.
  
For this purpose, the integer programming was used to optimize specific objectives while disconnecting connected
graphs created by an automatic graph generator implemented to simulate two different practical contexts:
- A water distribution network with specified origin and destination nodes
- A military resource distribution network with headquarters specification and different destruction difficulties of the military units

## Graphy Pack Organization
```
graphy/
      __init__.py
      constants.py
      generator.py
      solvers.py
      utils.py
```
## Running the App Locally

Using some Linux distro and make sure you have [Python 3](https://www.python.org/) installed.

Clone the project:

```bash
  git clone https://github.com/filipemedeiross/resilience_of_a_graph_using_integer_programming.git
```

Access the project directory:

```bash
  cd resilience_of_a_graph_using_integer_programming
```

Creating a virtual environment (for the example we use the location directory parameter as `.venv`):

```bash
  python3 -m venv .venv
```

Activating the virtual environment:

```bash
  source .venv/bin/activate
```

Install all required packages specified in requirements.txt:

```bash
  pip install -r requirements.txt
```

Use the following command to run the app:

```bash
  streamlit run Main_Page.py
```

## References

[<img src="https://user-images.githubusercontent.com/81262956/215500660-3ba254bd-6b55-4993-a047-473f818fe85b.png" alt="drawing" height="50" width="40"/>](<https://opengameart.org/>) Open Game Art

[<img src="https://user-images.githubusercontent.com/81262956/215494578-6ebd8cf8-1485-4e69-a335-3b8a68bb18b3.png" alt="drawing" height="25" width="40"/>](https://docs.streamlit.io/library/api-reference) Streamlit Docs

[<img src="https://user-images.githubusercontent.com/81262956/215497265-fe1f539d-4af1-4f90-b015-7cb3013826fb.png" alt="drawing" height="25" width="40"/>](https://networkx.org/documentation/stable/reference/index.html) Networkx Docs
