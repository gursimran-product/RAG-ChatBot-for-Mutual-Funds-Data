"""
Refusal Handler — Component 8.

Returns a static, polite refusal for advisory or out-of-scope queries.
No LLM call — zero latency, zero cost.
Always includes an AMFI educational link as required by the problem statement.
"""

_REFUSAL_TEMPLATE = (
    "I can only provide factual information about mutual fund schemes — "
    "I'm not able to offer investment advice, recommendations, or return projections.\n\n"
    "For independent guidance on choosing funds, please visit: "
    "https://www.amfiindia.com/investor-corner/knowledge-center"
)


def get_refusal() -> str:
    return _REFUSAL_TEMPLATE
