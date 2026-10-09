"""ZARIA usage aggregation — pure, framework-free so it's unit-testable without
the DB, following the same shape as slotting.py: the view
does the ORM `.values(...)` query and passes plain rows in here.
"""
from typing import List, Tuple


def summarize_by_user(rows: List[Tuple[str, int, int, float]]) -> List[dict]:
    """Aggregate ZariaMessage rows into one summary row per user.

    `rows` = (username, prompt_tokens, completion_tokens, cost_pln) tuples, one
    per assistant message (the caller filters role="assistant" beforehand).
    Returns rows sorted by total cost descending.
    """
    by_user = {}
    for username, prompt_tokens, completion_tokens, cost in rows:
        agg = by_user.setdefault(username, {
            "username": username, "messages": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_pln": 0.0,
        })
        agg["messages"] += 1
        agg["prompt_tokens"] += int(prompt_tokens or 0)
        agg["completion_tokens"] += int(completion_tokens or 0)
        agg["cost_pln"] += float(cost or 0)

    result = list(by_user.values())
    for r in result:
        r["cost_pln"] = round(r["cost_pln"], 4)
    result.sort(key=lambda r: r["cost_pln"], reverse=True)
    return result


def total_spend(rows: List[Tuple[str, int, int, float]]) -> float:
    """Total cost (PLN) across all rows — for the report's summary tile."""
    return round(sum(float(cost or 0) for _, _, _, cost in rows), 4)
