from self_screener.models import ScreenResult


def classify(semantic: ScreenResult, technical: ScreenResult):
    final_score = calculate_final_score(semantic, technical)

    if final_score is None:
        return (
            None,
            "Needs criteria",
            [
                "Final rating is paused until your semantic and technical rules are defined.",
            ],
        )

    if final_score >= 75:
        return final_score, "Buy", ["Final score is strong using 50% semantic and 50% technical weighting."]
    if final_score >= 45:
        return final_score, "Hold", ["Final score is mixed using 50% semantic and 50% technical weighting."]
    return final_score, "Sell", ["Final score is weak using 50% semantic and 50% technical weighting."]


def calculate_final_score(semantic: ScreenResult, technical: ScreenResult):
    if semantic.score is None or technical.score is None:
        return None
    return round((semantic.score * 0.5) + (technical.score * 0.5), 1)
