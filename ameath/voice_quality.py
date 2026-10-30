"""Conservative checks for local synthesized speech; recognition is only a heuristic."""
from difflib import SequenceMatcher


def excessive_silence(levels, window_seconds=0.25):
    if not levels:
        return True
    peak = max(levels)
    if peak < 0.003:
        return True
    floor = max(0.0015, peak * 0.035)
    quiet = [value < floor for value in levels]
    longest = running = 0
    for silent in quiet:
        running = running + 1 if silent else 0
        longest = max(longest, running)
    return longest * window_seconds > 2.0 or sum(quiet) / len(quiet) > 0.50


def transcript_matches(expected, actual):
    """Compare normalized syllables; deliberately reject missing/reference-only speech."""
    if not expected or not actual:
        return False
    # Short natural hesitation sounds are expressive rather than content errors.
    # Only remove extra leading/trailing fillers; words requested by the user stay.
    actual = list(actual)
    fillers = {"n", "en", "eng", "heng", "hm"}
    while len(actual) > len(expected) and actual[0] in fillers and actual[0] != expected[0]:
        actual.pop(0)
    while len(actual) > len(expected) and actual[-1] in fillers and actual[-1] != expected[-1]:
        actual.pop()
    ref = ("mu", "biao", "jie", "lu")
    def reference_like(window):
        return sum(SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.65
                   for a, b in zip(window, ref)) >= 3
    if len(actual) >= 4 and len(expected) >= 4:
        reference_in_input = any(reference_like(expected[i:i+4])
                                 for i in range(len(expected) - 3))
        if not reference_in_input and any(reference_like(actual[i:i+4])
                                          for i in range(len(actual) - 3)):
            return False
    blocks = SequenceMatcher(None, expected, actual, autojunk=False).get_matching_blocks()
    matched = sum(block.size for block in blocks)
    nonempty = [block for block in blocks if block.size]
    # The reference clip sometimes leaks into the beginning with imperfectly
    # recognized syllables. Reject extra speech adjacent to an otherwise matched
    # sentence rather than relying on a single exact transcription of the leak.
    if nonempty and len(expected) >= 6:
        first, last = nonempty[0], nonempty[-1]
        if first.a <= 1 and first.b >= 2:
            return False
        if last.a + last.size >= len(expected) - 1 and len(actual) - last.b - last.size >= 2:
            return False
    matched_positions = {block.a + i for block in blocks for i in range(block.size)}
    missing_run = longest_missing = 0
    for position in range(len(expected)):
        missing_run = 0 if position in matched_positions else missing_run + 1
        longest_missing = max(longest_missing, missing_run)
    coverage = matched / len(expected)
    precision = matched / len(actual)
    anchored = (len(expected) < 6 or
                (any(i in matched_positions for i in range(2)) and
                 any(i in matched_positions for i in range(len(expected) - 2, len(expected)))))
    return (coverage >= 0.85 and precision >= 0.65 and matched >= min(3, len(expected))
            and anchored and longest_missing <= 1)
