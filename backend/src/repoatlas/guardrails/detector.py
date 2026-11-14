"""Injection detector (GRD-3): find instruction-like text in repository content.

``scan(text)`` returns ``Flag``s: the kind of thing found, where (character offsets into
``text``), the matched snippet, and a severity from 0 to 1. ``scan_path(path)`` does the same
for a file path, since a file's name can itself be an instruction.

It is a set of pattern rules, tuned for recall on the planted-injection fixture and for few
false positives on ordinary code. It is a **signal, not a defense**: novel phrasing gets past
it. The defenses are the prompt layout (repo text is data, never instructions), validated
citations, read-only tools, and the injection suite. What the detector adds is that flagged
text is marked in the prompt and in the UI.

How text is read:

- Rules run on a *view* of the text with the same length, so offsets carry over: underscores
  become spaces (``test_assistant_must_say`` reads as words), and a line break followed by a
  comment marker becomes spaces (an instruction split over two comment lines reads as one
  sentence).
- Runs of base64 are decoded; one that decodes to readable text is flagged, more severely when
  the decoded text is itself an instruction.

Kinds: ``instruction_to_assistant`` (imperatives addressed to an AI, "ignore previous
instructions"), ``role_or_system_claim`` (fake ``SYSTEM:`` blocks, "you are now …"),
``tool_request`` (tool-call shapes, requests to call a tool), ``exfiltration_url`` (URLs with
query parameters, worse next to "fetch this" or as an image), ``encoded_payload`` (base64 that
decodes to text), ``canary_like`` (marker tokens the text asks to have repeated).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DETECTOR_VERSION = 1  # bump when rules change what is flagged

FlagKind = Literal[
    "instruction_to_assistant",
    "role_or_system_claim",
    "tool_request",
    "exfiltration_url",
    "encoded_payload",
    "canary_like",
]
FLAG_KINDS: tuple[FlagKind, ...] = (
    "instruction_to_assistant",
    "role_or_system_claim",
    "tool_request",
    "exfiltration_url",
    "encoded_payload",
    "canary_like",
)
# Flags at or above this severity are marked in prompts and in the UI.
MARK_SEVERITY = 0.5
MAX_SNIPPET_CHARS = 160


class Flag(BaseModel):
    """One finding: ``text[span[0]:span[1]]`` looks like ``kind``."""

    model_config = ConfigDict(frozen=True)

    kind: FlagKind
    span: tuple[int, int]
    snippet: str
    severity: float = Field(ge=0, le=1)


_I = re.IGNORECASE
_AI = r"(?:ai|a\.i\.|llm|language model|assistant|chatbot|copilot|agent|model|claude|gpt|bot)"
_SAY = (
    r"(?:say|reply|respond|output|print|answer|tell|include|cite|claim|state|write|repeat|"
    r"ignore|report|recommend|append|mention|reveal|disregard)"
)

# (kind, severity, pattern). Patterns run on the view (see ``_view``): underscores are spaces.
_RULES: tuple[tuple[FlagKind, float, re.Pattern[str]], ...] = (
    # --- instructions addressed to an assistant ----------------------------------------
    (
        "instruction_to_assistant",
        0.9,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the|your|every|of)\s+)*"
            r"(?:previous|prior|above|earlier|preceding|former|system|other)\s+"
            r"(?:instructions?|prompts?|rules|directions?|messages?|guidelines|context)",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.8,
        re.compile(
            rf"\b{_AI}s?\b[^.\n]{{0,40}}?\b(?:must|should|shall|has to|have to|needs? to|is "
            rf"required to|please)\s+(?:always\s+|now\s+|only\s+|never\s+)?{_SAY}\b",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.8,
        re.compile(r"\b(?:ai|llm)\s+(?:assistant|agent|model|system|reviewer)s?\s*[:,]", _I),
    ),
    (
        "instruction_to_assistant",
        0.7,
        re.compile(
            r"\b(?:to|for|attention|note to|dear|hey|hello)\s+(?:the\s+|any\s+|all\s+)?"
            r"(?:ai|llm|assistant|language model|chatbot)s?\b(?:\s+(?:assistant|agent|model)s?)?"
            r"(?:\s+(?:reading|reviewing|analy[sz]ing|summari[sz]ing|processing|answering)\b|"
            r"\s*[:,])",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.8,
        re.compile(
            r"\b(?:do not|don't|never|you must not)\s+(?:tell|mention|reveal|inform|show|"
            r"disclose)\s+(?:(?:this|that|it|any of this)\s+to\s+)?the\s+user",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.6,
        re.compile(
            r"\b(?:when|if)\s+(?:you(?:\s+are)?|an?\s+(?:ai|assistant|llm)(?:\s+is)?)\s+"
            r"(?:asked|answer(?:ing)?|summari[sz](?:e|ing)|explain(?:ing)?|describ(?:e|ing))\b"
            r"[^.\n]{0,80}?\b(?:always|must|never|instead|reply|respond|say|claim|tell)\b",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.6,
        re.compile(
            r"\binstead\s*,?\s+(?:reply|respond|say|output|answer|print)\b|"
            r"\b(?:reply|respond|answer)\s+(?:only\s+)?with\s+(?:exactly|only|just|the (?:word|"
            r"phrase|string|token))\b",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.8,
        re.compile(
            r"\b(?:say|claim|state|report|answer|tell\s+(?:the\s+)?(?:user|reader|developer)s?)"
            r"\s+that\b[^.\n]{0,80}?\b(?:is|are)\s+(?:completely\s+|totally\s+|perfectly\s+)?"
            r"safe\s+to\s+(?:delete|remove|drop|disable)",
            _I,
        ),
    ),
    (
        "instruction_to_assistant",
        0.7,
        re.compile(
            r"\b(?:always|must|should|you\s+(?:must|should)|be sure to)\s+cite\b|"
            r"\bcite\s+(?:the\s+file\s+)?\S+\s+as\s+(?:the\s+|your\s+)?(?:source|evidence)",
            _I,
        ),
    ),
    # Other languages: "ignore the previous instructions".
    (
        "instruction_to_assistant",
        0.9,
        re.compile(
            r"\bignora(?:r)?\s+(?:todas\s+)?las\s+instrucciones\s+(?:anteriores|previas)|"
            r"\bignore[zr]?\s+(?:toutes\s+)?les\s+instructions\s+(?:pr[ée]c[ée]dentes|"
            r"ant[ée]rieures)|"
            r"\bignorier(?:e|en\s+sie|t)?\s+(?:alle\s+)?(?:vorherigen|bisherigen|obigen|"
            r"vorigen)\s+(?:anweisungen|instruktionen)|"
            r"\bignor[ea]\s+(?:todas\s+)?as\s+instru[çc][õo]es\s+anteriores|"
            r"\bignora\s+(?:tutte\s+)?le\s+istruzioni\s+precedenti|"
            r"игнорируй(?:те)?\s+(?:все\s+)?предыдущие\s+инструкции|"
            r"忽略(?:之前|以上|先前|上述|前面)(?:的)?(?:所有)?(?:指令|指示|说明|提示)|"
            r"(?:以前|これまで|上記|前)の(?:指示|命令)を無視|"
            r"이전(?:의)?\s*(?:모든\s*)?(?:지시|명령)(?:를|을)?\s*무시",
            _I,
        ),
    ),
    # --- role and system claims ----------------------------------------------------------
    (
        "role_or_system_claim",
        0.8,
        re.compile(r"\bSYSTEM\s*(?:PROMPT|MESSAGE|OVERRIDE|NOTE|INSTRUCTION)?\s*:"),
    ),
    (
        "role_or_system_claim",
        0.8,
        re.compile(
            r"<\|?\s*(?:system|im start|im end|assistant|endoftext)\s*\|?>|"
            r"\[\s*(?:system|inst|/inst)\s*\]|"
            r"\b(?:begin|end|start)\s+(?:of\s+)?(?:system|developer)\s+(?:prompt|message)",
            _I,
        ),
    ),
    (
        "role_or_system_claim",
        0.5,
        re.compile(
            r"(?m)^[ \t]*(?:system|assistant|developer)[ \t]*:[ \t]*\S|"
            r"\b(?:system|developer)\s+prompt\b",
            _I,
        ),
    ),
    (
        "role_or_system_claim",
        0.8,
        re.compile(
            r"\byou\s+are\s+now\s+(?:an?\s+|the\s+|in\s+|no\s+longer\s+)?\w+|"
            r"\bdo\s+anything\s+now\b|\bDAN\s+mode\b|"
            r"\bact\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:unrestricted|"
            r"jailbroken|unfiltered|uncensored)\b|"
            r"\b(?:new|updated|real|actual|revised)\s+(?:system\s+)?(?:instructions|"
            r"system prompt|rules)\s*(?::|follow\b|are\b)",
            _I,
        ),
    ),
    (
        "role_or_system_claim",
        0.6,
        re.compile(
            r"\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b|\bdeveloper\s+mode\b|"
            r"\bjailbreak(?:en|ed)?\b|\broleplay\s+as\b|\bfrom\s+now\s+on\s*,?\s+you\b",
            _I,
        ),
    ),
    # --- tool requests ---------------------------------------------------------------------
    (
        "tool_request",
        0.8,
        re.compile(
            r"""["'](?:tool use|tool calls?|function call)["']\s*:|"""
            r"""["']type["']\s*:\s*["'](?:tool use|function|function call)["']|"""
            r"""\{\s*["'](?:name|tool|function)["']\s*:\s*["'][\w. -]+["']\s*,\s*"""
            r"""["'](?:arguments|input|parameters|args)["']\s*:|"""
            r"<\s*(?:tool call|function calls?|invoke|tool use)\b",
            _I,
        ),
    ),
    (
        "tool_request",
        0.6,
        re.compile(
            # "tool", not "function": "call the same function again" is ordinary code prose.
            r"\b(?:call|use|invoke|run|execute|trigger)\s+(?:the\s+|your\s+)[\w.-]+"
            r"(?:\s+[\w.-]+)?\s+tool\b[^.\n]{0,60}?\b(?:with|on|to|and)\b|"
            r"\b(?:run|execute)\s+(?:the\s+)?(?:following\s+)?(?:shell\s+)?command\s*:",
            _I,
        ),
    ),
)

# Canaries are tokens, so these run on the text as written (a token ends where it ends).
_RAW_RULES: tuple[tuple[FlagKind, float, re.Pattern[str]], ...] = (
    ("canary_like", 0.7, re.compile(r"\bCANARY[-_:][A-Za-z0-9][A-Za-z0-9_-]{3,40}")),
    (
        "canary_like",
        0.6,
        re.compile(
            r"\b(?:say|reply\s+with|respond\s+with|output|print|include|repeat|write)\s+"
            r"(?:the\s+)?(?:(?:secret\s+|magic\s+|code\s*)?(?:word|phrase|string|token|code)\s+)?"
            r"[`\"']([A-Z0-9][A-Z0-9_-]{7,40})[`\"']"
        ),
    ),
)

_URL = re.compile(r"https?://[^\s\"'<>)\]`\\]+", _I)
_SENSITIVE_PARAM = re.compile(
    r"[?&](?:token|secret|api ?key|password|passwd|data|prompt|context|payload|env|"
    r"leak|dump|session|cookie|auth|exfil\w*)=",
    _I,
)
# A value the reader is asked to fill in: ``{context}`` or ``<your answer>``. Template-literal
# and format placeholders (``${x}``, ``%s``) are ordinary code and don't count.
_PLACEHOLDER = re.compile(r"[?&][^=&#]+=[^&#]*(?:(?<!\$)\{[A-Za-z ]+\}|<[A-Za-z ]+>)")
_FETCH_VERB = re.compile(
    r"\b(?:fetch|visit|open|load|request|send|post|curl|browse|navigate|retrieve|download|"
    r"go to|click|access|ping|call)\b",
    _I,
)
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])")
_COMMENT_BREAK = re.compile(r"\r?\n[ \t]*(?:#+|//+|\*+/?|/\*+|--+|;+|\"\"\"|''')?[ \t]*")
URL_CONTEXT_CHARS = 100
MIN_DECODED_WORDS = 4


def _view(text: str) -> str:
    """``text`` with identifiers and comment continuations readable as prose, same length."""
    spaced = text.replace("_", " ")
    return _COMMENT_BREAK.sub(lambda match: " " * len(match[0]), spaced)


def _snippet(text: str, start: int, end: int) -> str:
    return " ".join(text[start:end].split())[:MAX_SNIPPET_CHARS]


def _rule_flags(text: str, view: str) -> list[Flag]:
    found = []
    for rules, source in ((_RULES, view), (_RAW_RULES, text)):
        for kind, severity, pattern in rules:
            for match in pattern.finditer(source):
                start, end = match.span()
                found.append(
                    Flag(
                        kind=kind,
                        span=(start, end),
                        snippet=_snippet(text, start, end),
                        severity=severity,
                    )
                )
    return found


def _url_flags(text: str, view: str) -> list[Flag]:
    found = []
    for match in _URL.finditer(text):
        url = match[0].rstrip(".,;:")
        if "?" not in url:
            continue
        start, end = match.start(), match.start() + len(url)
        severity = 0.3
        if _SENSITIVE_PARAM.search(url.replace("_", " ")) or _PLACEHOLDER.search(url):
            severity = 0.6
        around = view[max(0, start - URL_CONTEXT_CHARS) : min(len(view), end + URL_CONTEXT_CHARS)]
        if _FETCH_VERB.search(around):
            # Next to a plain URL this is ordinary in HTTP code, so it stays below the mark.
            severity += 0.2 if severity >= MARK_SEVERITY else 0.1
        if text[max(0, start - 2) : start] == "](" and "![" in text[max(0, start - 200) : start]:
            severity = max(severity, 0.8)  # a markdown image loads without a click
        found.append(
            Flag(
                kind="exfiltration_url",
                span=(start, end),
                snippet=_snippet(text, start, end),
                severity=round(min(severity, 0.95), 2),
            )
        )
    return found


def _decoded_text(run: str) -> str | None:
    """The text a base64 run decodes to, if it is readable prose (not a hash or binary)."""
    body = run.rstrip("=")
    try:
        raw = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(1 for char in decoded if char.isprintable() or char in "\n\t")
    if not decoded or printable / len(decoded) < 0.95:
        return None
    words = [word for word in decoded.split() if any(char.isalpha() for char in word)]
    return decoded if len(words) >= MIN_DECODED_WORDS else None


def _encoded_flags(text: str) -> list[Flag]:
    found = []
    for match in _BASE64.finditer(text):
        decoded = _decoded_text(match[0])
        if decoded is None:
            continue
        inner = _rule_flags(decoded, _view(decoded))
        severity = 0.9 if any(flag.severity >= MARK_SEVERITY for flag in inner) else 0.4
        start, end = match.span()
        found.append(
            Flag(
                kind="encoded_payload",
                span=(start, end),
                snippet=_snippet(text, start, end),
                severity=severity,
            )
        )
    return found


def _dedupe(flags: Iterable[Flag]) -> list[Flag]:
    """Within a kind, overlapping findings keep the most severe (then the longest)."""
    kept: list[Flag] = []
    ranked = sorted(flags, key=lambda f: (-f.severity, f.span[0] - f.span[1], f.span[0], f.kind))
    for flag in ranked:
        if any(
            other.kind == flag.kind
            and other.span[0] < flag.span[1]
            and flag.span[0] < other.span[1]
            for other in kept
        ):
            continue
        kept.append(flag)
    return sorted(kept, key=lambda f: (f.span[0], f.span[1], f.kind))


def scan(text: str) -> list[Flag]:
    """Every finding in ``text``, in order of position."""
    if not text:
        return []
    view = _view(text)
    return _dedupe([*_rule_flags(text, view), *_url_flags(text, view), *_encoded_flags(text)])


def scan_path(path: str) -> list[Flag]:
    """Findings in a file path (a file's name can be an instruction); spans index ``path``."""
    view = re.sub(r"[-./\\]", " ", path)  # same length; separators and dots read as spaces
    return _dedupe(_rule_flags(path, _view(view)))


def marked(flags: Iterable[Flag], threshold: float = MARK_SEVERITY) -> list[Flag]:
    """The flags severe enough to mark in a prompt or in the UI."""
    return [flag for flag in flags if flag.severity >= threshold]


def kinds(flags: Iterable[Flag]) -> list[str]:
    """The distinct kinds among ``flags``, in the fixed order of ``FLAG_KINDS``."""
    present = {flag.kind for flag in flags}
    return [kind for kind in FLAG_KINDS if kind in present]
