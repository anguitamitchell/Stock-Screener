import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from self_screener.models import Holding
from self_screener.screeners.semantic import build_research_evidence


CASES = [
    {
        "holding": Holding("MNST", "Monster Beverage Corporation"),
        "forbidden": {"energy_security_tailwinds"},
    },
    {
        "holding": Holding("CELH", "Celsius Holdings Inc"),
        "forbidden": {"energy_security_tailwinds"},
    },
    {
        "holding": Holding("PEP", "PepsiCo Inc"),
        "forbidden": {"energy_security_tailwinds"},
    },
    {
        "holding": Holding("XOM", "Exxon Mobil Corporation"),
        "required": {"energy_security_tailwinds"},
    },
    {
        "holding": Holding("NEE", "NextEra Energy Inc"),
        "required": {"grid_data_center_demand"},
    },
]


def main() -> int:
    failures = []
    for case in CASES:
        holding = case["holding"]
        evidence = build_research_evidence(holding, refresh=False)
        themes = {match["theme_key"] for match in evidence.get("matches", [])}
        forbidden = case.get("forbidden", set())
        required = case.get("required", set())
        unexpected = sorted(themes & forbidden)
        missing = sorted(required - themes)
        if unexpected or missing:
            failures.append(
                {
                    "ticker": holding.ticker,
                    "themes": sorted(themes),
                    "unexpected": unexpected,
                    "missing": missing,
                }
            )

    if failures:
        print("Semantic guardrail failures:")
        for failure in failures:
            print(failure)
        return 1

    print(f"Semantic guardrails passed for {len(CASES)} cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
