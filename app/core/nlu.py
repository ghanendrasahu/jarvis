"""Lightweight NLU helpers shared by the voice pipeline.

Responsibilities (kept deliberately small):
- Build the domain lexicon dynamically from existing registries/aliases.
- RapidFuzz-based candidate generation: obvious entity variants, fuzzy wake word.
- Decide whether a transcript needs LLM semantic interpretation.
- Build the Whisper initial prompt so STT itself is biased toward real commands.

This module NEVER executes anything and is fully offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from app.tools.system_tools import APP_ALIASES, WEB_APP_ALIASES

SURFACES = ("youtube", "whatsapp")

_COMMAND_VERBS = (
    "play",
    "pause",
    "resume",
    "next",
    "previous",
    "prev",
    "skip",
    "rewind",
    "forward",
    "back",
    "seek",
    "open",
    "launch",
    "start",
    "close",
    "quit",
    "kill",
    "search",
    "send",
    "remind",
    "schedule",
    "cancel",
    "list",
    "show",
    "read",
    "find",
    "move",
    "run",
    "type",
    "press",
    "take",
    "capture",
    "analyze",
    "ocr",
    "look",
    "mute",
    "unmute",
    "fullscreen",
    "captions",
    "subtitle",
    "faster",
    "slower",
    "volume",
    "jump",
    "announce",
    "copy",
    "index",
    "remember",
)

_COMMON_WORDS = frozenset(
    """
    a about after all am an and any are around at be been being but by can come could day did do does doing done
    down else for from get go going good got had has have help hey hi him his how i if in into is it its just let
    like make me maybe minutes more much my no not now of oh ok okay on one only or our out over please put said
    same see set should so some something start still such sure take tell than that the their them then there these
    they thing things think this those time to today tonight too two up us use very want was way we week well went
    were what when where which while who why will with would yeah yes yet you your whats whats
    """.split()
)

_LEADING_FILLER = re.compile(
    r"^(?:hey|hi|hello|ok|okay|yo|jarvis|jervis|please|could\s+you|can\s+you|would\s+you|will\s+you"
    r"|i\s+(?:want|would\s+like)\s+(?:you\s+)?to)\b[,\s]*",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(r"[,\s]*(?:please|for\s+me|thanks|thank\s+you)[.!]?\s*$", re.IGNORECASE)

_QUERY_SLOT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^search\s+(?:web\s+)?.+",  # free-text web query
        r"^find\s+files\s+.+",
        r"^read\s+(?:file|url)\s+.+",
        r"^recall\s+.+",
        r"^research\b.+",
        r"^type\s+.+",  # literal typing target
        r"^run\s+.+",  # literal shell command
        r"^announce\s+.+",
        r"^copy\s+.+",
        r"^remind\s+me\s+to\s+.+",
        r"^apply\s+for\s+.+",  # job query
        r"^(?:play\s+)?youtube\s+(?:and\s+)?play\s+.+",  # youtube search query
        r"^play\s+.+\s+on\s+youtube$",
        r"^(?:send\s+)?whatsapp(?:\s+message)?\s+to\s+.+",  # contact + message body
        r"^send\s+email\s+to\s+.+",
    )
)

_TOKEN_RE = re.compile(r"[a-z']+")


@dataclass(frozen=True)
class DomainLexicon:
    apps: frozenset[str]
    websites: frozenset[str]
    surfaces: frozenset[str]
    verbs: frozenset[str]

    @property
    def entities(self) -> frozenset[str]:
        return self.apps | self.websites | self.surfaces

    @property
    def words(self) -> frozenset[str]:
        return self.verbs | self.entities

    @property
    def understood_words(self) -> frozenset[str]:
        return self.words | _COMMON_WORDS


@lru_cache(maxsize=1)
def build_lexicon() -> DomainLexicon:
    """Derive the lexicon from the registries that already exist in the codebase."""
    return DomainLexicon(
        apps=frozenset(APP_ALIASES.keys()),
        websites=frozenset(WEB_APP_ALIASES.keys()),
        surfaces=frozenset(SURFACES),
        verbs=frozenset(_COMMAND_VERBS),
    )


def strip_filler(text: str) -> str:
    """Remove conversational filler ("hey jarvis", "could you", trailing "please")."""
    cleaned = text.strip()
    for _ in range(4):
        stripped = _LEADING_FILLER.sub("", cleaned, count=1).strip()
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = _TRAILING_FILLER.sub("", cleaned).strip(" ,.")
    return cleaned.strip()


def correct_transcript(text: str, cutoff: int = 88) -> tuple[str, bool]:
    """Fix obvious single-token entity variants ("watsap" -> "whatsapp").

    Conservative: only tokens that are NOT already understood are candidates, and a
    replacement requires a RapidFuzz score >= cutoff against the known vocabulary.
    Returns (possibly corrected text, whether anything changed).
    """
    lexicon = build_lexicon()
    understood = lexicon.understood_words
    choices = sorted(lexicon.words | {"jarvis"})
    parts = re.split(r"(\s+)", text)
    changed = False
    for index, part in enumerate(parts):
        core = part.lower().strip(".,!?;:'\"").replace("'", "")
        if len(core) < 3 or not core.isalpha() or core in understood:
            continue
        match = process.extractOne(core, choices, scorer=fuzz.ratio, score_cutoff=cutoff)
        if match:
            parts[index] = match[0]
            changed = True
    return "".join(parts), changed


def has_free_text_slot(text: str) -> bool:
    """True when the command grammar puts remaining words in a verbatim slot."""
    lowered = text.lower().strip()
    return any(pattern.match(lowered) for pattern in _QUERY_SLOT_PATTERNS)


_ENTITY_TARGET_VERBS = frozenset({"open", "close", "launch", "start", "quit", "kill", "play"})


def needs_semantic_interpretation(text: str) -> bool:
    """Decide whether a transcript is suspicious enough to warrant the semantic LLM.

    Fast-path commands (all words understood, or free-text queries like searches)
    never trigger it. Unknown content words do only when the phrase targets an
    entity ("open hot tub") or mangles a verb-carrying command heavily.
    """
    lowered = text.lower().strip()
    if not lowered:
        return False
    if has_free_text_slot(lowered):
        return False

    lexicon = build_lexicon()
    understood = lexicon.understood_words
    tokens = _TOKEN_RE.findall(lowered)

    def _is_known(token: str) -> bool:
        return token in understood or token.replace("'", "") in understood

    unknown = [token for token in tokens if not _is_known(token)]
    if not unknown:
        return False

    if tokens and tokens[0] in _ENTITY_TARGET_VERBS:
        return True

    # Otherwise only clearly-mangled verb commands justify an LLM round-trip;
    # everything else falls through to the planner exactly as before.
    has_verb = any(token in lexicon.verbs for token in tokens)
    return has_verb and len(tokens) >= 4 and len(unknown) >= max(2, len(tokens) // 3)


def has_lexicon_support(text: str) -> bool:
    """True when a transcript shows any command-relevant signal.

    Used to reject obviously non-command garbage (e.g., STT prompt echoes)
    during follow-up windows instead of forwarding it to the planner.
    """
    lowered = text.lower().strip()
    if not lowered:
        return False
    if has_free_text_slot(lowered):
        return True
    understood = build_lexicon().understood_words
    for match in re.finditer(r"[a-z']+", lowered):
        token = match.group()
        bare = token.replace("'", "")
        if token in understood or bare in understood or bare.isdigit():
            return True
    return False


def build_stt_prompt(wake_word: str = "jarvis") -> str:
    """Compact Whisper initial_prompt biased toward JARVIS's actual vocabulary.

    Vocabulary/context ONLY - never instructional sentences, which Whisper can
    echo back into transcripts on ambiguous audio.
    """
    lexicon = build_lexicon()
    vocab = ", ".join(sorted(lexicon.words))
    return f"Wake word '{wake_word}'. Commands: {vocab}. Surfaces: {', '.join(SURFACES)}."[:1000]


# Static scaffolding fragments of build_stt_prompt() that are never legitimate
# user speech; echoes of these are stripped from transcripts conservatively.
_PROMPT_ECHO_PATTERNS = (
    re.compile(r"\bwake\s+word\s+['\"']?jarvis['\"']?\s*\.?", re.IGNORECASE),
    re.compile(r"\bcommands?:[^.]*\.?", re.IGNORECASE),
    re.compile(r"\bsurfaces?:\s*[a-z,\s]+\.?", re.IGNORECASE),
    re.compile(r"\bignore\s+filler\s+words\b\.?", re.IGNORECASE),
)


def strip_prompt_echo(text: str) -> str:
    """Remove STT-prompt scaffolding echoed into a transcript.

    Conservative: only labeled prompt constructs ("wake word 'jarvis'",
    "commands: ...", "surfaces: ...", "ignore filler words") are removed.
    Bare vocabulary words are NEVER stripped from user speech.
    """
    cleaned = text
    for pattern in _PROMPT_ECHO_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.:")


def best_entity_match(name: str, choices: frozenset[str] | set[str], cutoff: int) -> str | None:
    """Resolve a possibly-mangled entity name to a known one, or None."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        return None
    if normalized in choices:
        return normalized
    match = process.extractOne(normalized, sorted(choices), scorer=fuzz.WRatio, score_cutoff=cutoff)
    return match[0] if match else None


def locate_wake_phrase(transcript: str, wake_phrase: str, threshold: float = 82.0) -> tuple[int, int] | None:
    """Character span (start, end) of the best wake-phrase match, or None.

    Exact substring wins; otherwise token-window RapidFuzz scoring locates a
    fuzzy variant ("jervis"), with a partial-alignment fallback.
    """
    text = transcript.lower()
    phrase = wake_phrase.lower().strip()
    if not phrase or not text:
        return None
    exact = text.find(phrase)
    if exact != -1:
        return exact, exact + len(phrase)

    tokens = [(m.start(), m.group()) for m in re.finditer(r"[a-z']+", text)]
    window_size = max(1, len(phrase.split()))
    best_span: tuple[int, int] | None = None
    best_score = 0.0
    for start in range(max(0, len(tokens) - window_size + 1)):
        window_tokens = tokens[start : start + window_size]
        window = " ".join(t[1] for t in window_tokens)
        score = fuzz.ratio(window, phrase)
        if score > best_score:
            best_score = score
            best_span = (window_tokens[0][0], window_tokens[-1][0] + len(window_tokens[-1][1]))
    if best_span is not None and best_score >= threshold:
        return best_span

    try:
        alignment = fuzz.partial_ratio_alignment(phrase, text)
    except Exception:
        return None
    if alignment is not None and alignment.score >= threshold:
        return alignment.dest_start, alignment.dest_end
    return None


def fuzzy_wake_detected(transcript: str, wake_phrase: str, threshold: float = 82.0) -> bool:
    """Accept close wake-word variants ("jervis") without firing on unrelated words."""
    return locate_wake_phrase(transcript, wake_phrase, threshold) is not None
