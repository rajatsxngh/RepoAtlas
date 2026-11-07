"""Code graph building (GRA-1..11): nodes, edges with provenance and confidence, owners.

Everything here is pure and deterministic: no database, network, or LLM. Static analysis builds
every node and edge (decision 0004); call resolution uses the tiers in ``graph.tiers``
(decision 0005).
"""
