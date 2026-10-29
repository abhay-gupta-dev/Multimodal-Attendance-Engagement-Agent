from collections.abc import Iterable


def engagement_indicator(components: dict[str, float | None]) -> float | None:
    available = {key: value for key, value in components.items() if value is not None}
    if not available:
        return None
    return round(sum(available.values()) / len(available), 1)


def recommendations(indicator: float | None, audio_activity: float | None, data_quality: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if indicator is not None and indicator < 45:
        result.append({"title": "Try a short interactive activity", "explanation": "The observable behavioral indicator has been lower than the configured review threshold.", "action": "Consider a poll, pair discussion, or comprehension check.", "evidence": f"Indicator: {indicator:.0f}/100", "confidence": "moderate"})
    if audio_activity is not None and audio_activity < 20:
        result.append({"title": "Invite questions from the class", "explanation": "Low audio activity was measured; silence is not interpreted as disengagement.", "action": "Ask an open question and allow quiet think time.", "evidence": f"Audio activity: {audio_activity:.0f}%", "confidence": "low"})
    if data_quality != "measured":
        result.append({"title": "Review sensor availability", "explanation": "Some signals were unavailable or incomplete.", "action": "Check camera or audio input before changing teaching approach.", "evidence": f"Data quality: {data_quality}", "confidence": "high"})
    return result

