import re

STOPWORDS = frozenset(
    """a an the and or but if of to in on at by for with from as is are was were be been being
    do does did have has had i me my we our you your he she it its they them their this that these
    those what which who whom how when where why can could should would will shall may might must
    not no yes so than then there here about into over under up down out any all each per much many
    am get got please tell know""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Tiny suffix stripper: enough to match 'expenses'/'expense', 'accrued'/'accrual'."""
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ed", "es", "s", "al"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return word


def terms(text: str) -> list[str]:
    return [stem(w) for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS]


def keywords(text: str) -> list[str]:
    """Unstemmed content words, for engines that apply their own stemmer (Postgres FTS)."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS]
