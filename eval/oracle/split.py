"""Frozen repository grouping and overlap rejection for oracle packages."""

import hashlib
import hmac
import json
from pathlib import Path
import re
import time


POLICY_PATH = Path(__file__).with_name("similarity-policy-v1.json")
POLICY_SHA256 = "afe5010343623c9f413c3304350b751219f25137113422c241460a70260dfeb5"
LANGUAGES = ("python", "java", "typescript", "rust", "go")
REFERENCE_CATEGORIES = ("prompt", "example", "test", "fixture", "prior-corpus")
MAX_SIMILARITY_ROWS = 10_000
MAX_REFERENCE_ITEMS = 10_000
MAX_TEXT_BYTES = 1024 * 1024
MAX_TOKENS_PER_FIELD = 200_000
MAX_COMPARISONS = 5_000_000
MAX_SIMILARITY_SECONDS = 30.0
_EXPECTED_POLICY = {
    "schema_version": 1,
    "lineage": {"field": "lineage_id", "inference": False, "required": True},
    "code": {
        "tokenizer": "finite-state-code-v1", "languages": list(LANGUAGES),
        "drop_comments": True, "numeric_token": "<num>", "string_token": "<str>",
        "structural_identifier_token": "<id>",
    },
    "documentation": {
        "tokenizer": "ascii-word-v1", "lowercase": True, "numeric_token": "<num>",
    },
    "comparison": {
        "fields": ["code", "documentation"],
        "reference_categories": list(REFERENCE_CATEGORIES), "concatenate_fields": False,
        "normalized_token_equality": True, "structural_code_equality": True,
    },
    "fuzzy": {
        "metric": "set-jaccard-token-shingles", "shingle_tokens": 5,
        "threshold": 0.85, "minimum_tokens_each": 20,
    },
}

_KEYWORDS = {
    "python": frozenset("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case".split()),
    "java": frozenset("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null record sealed permits non-sealed var yield".split()),
    "typescript": frozenset("any as async await boolean break case catch class const constructor continue debugger declare default delete do else enum export extends false finally for from function get if implements import in infer instanceof interface keyof let module namespace never new null number object of package private protected public readonly require return set static string super switch symbol this throw true try type typeof undefined unique unknown var void while with yield".split()),
    "rust": frozenset("as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn abstract become box do final macro override priv typeof unsized virtual yield try union".split()),
    "go": frozenset("break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var true false iota nil".split()),
}

_OPERATORS = {
    "python": ("**=", "//=", ">>=", "<<=", ":=", "->", "==", "!=", "<=", ">=", "**", "//", "<<", ">>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "..."),
    "java": (">>>=", "<<=", ">>=", ">>>", "::", "->", "++", "--", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="),
    "typescript": (">>>=", "**=", "&&=", "||=", "??=", "===", "!==", ">>>", "=>", "?.", "...", "++", "--", "==", "!=", "<=", ">=", "&&", "||", "??", "**", "<<", ">>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="),
    "rust": ("<<=", ">>=", "..=", "::", "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", ".."),
    "go": ("<<=", ">>=", "&^=", "...", ":=", "<-", "++", "--", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "&^", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="),
}
_PUNCTUATION = frozenset("(){}[];,:.@?~+-*/%&|^!=<>\\")
_DOC_WORD = re.compile(r"[A-Za-z]+|[0-9]+", re.ASCII)


class SimilarityError(ValueError):
    """Similarity input is ambiguous or crosses a frozen boundary."""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_similarity_policy(path=POLICY_PATH):
    """Load the exact frozen policy and return it with its raw-byte identity."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SimilarityError("similarity policy is unavailable or invalid") from None
    if value != _EXPECTED_POLICY:
        raise SimilarityError("similarity policy drifted from version 1")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        raise SimilarityError("similarity policy bytes drifted from version 1")
    return value, digest


def assign_split(key, lineage_id):
    """Select a split by keyed lineage identity; no repository name is inferred."""
    if type(key) is not bytes or len(key) < 16:
        raise SimilarityError("split key must contain at least 16 bytes")
    if type(lineage_id) is not str or not lineage_id.strip() or "\0" in lineage_id:
        raise SimilarityError("every seed requires a nonempty lineage id")
    digest = hmac.new(key, b"evergreen-oracle-split-v1\0" + lineage_id.encode(), hashlib.sha256)
    return "dev" if digest.digest()[0] < 128 else "holdout"


def _consume_block_comment(source, start, nested):
    position = start + 2
    depth = 1
    while position < len(source):
        if nested and source.startswith("/*", position):
            depth += 1
            position += 2
        elif source.startswith("*/", position):
            depth -= 1
            position += 2
            if depth == 0:
                return position
        else:
            position += 1
    raise SimilarityError("unterminated block comment")


def _consume_delimited(source, quote, start, *, raw=False):
    position = start + len(quote)
    while position < len(source):
        if source.startswith(quote, position):
            return position + len(quote)
        if not raw and source[position] == "\\":
            position += 2
        else:
            position += 1
    raise SimilarityError("unterminated string or character literal")


def _string_literal(language, source, position):
    tail = source[position:]
    if language == "python":
        match = re.match(r"(?i:(?:br|rb|fr|rf|r|u|b|f)?)(\"\"\"|'''|\"|')", tail)
        if match:
            token = match.group(0)
            quote = match.group(1)
            raw = "r" in token[:-len(quote)].casefold()
            return _consume_delimited(source, quote, position + len(token) - len(quote), raw=raw)
    elif language == "rust":
        raw_match = re.match(r"(?:br|rb|r)(?P<hashes>#{0,255})\"", tail)
        if raw_match:
            hashes = raw_match.group("hashes")
            closing = '"' + hashes
            content = position + raw_match.end()
            end = source.find(closing, content)
            if end < 0:
                raise SimilarityError("unterminated raw string literal")
            return end + len(closing)
        for prefix in ('b"', 'c"', '"'):
            if tail.startswith(prefix):
                return _consume_delimited(source, '"', position + len(prefix) - 1)
        if tail.startswith("b'"):
            return _consume_delimited(source, "'", position + 1)
        if tail.startswith("'"):
            closing = position + 1
            if closing < len(source) and source[closing] == "\\":
                closing += 2
            else:
                closing += 1
            if closing < len(source) and source[closing] == "'":
                return closing + 1
    else:
        candidates = ('"""', '"', "'") if language == "java" else ('`', '"', "'")
        for quote in candidates:
            if tail.startswith(quote):
                raw = quote == '`'
                return _consume_delimited(source, quote, position, raw=raw)
    return None


def code_tokens(language, source, structural=False):
    """Tokenize supported code without executing or guessing ambiguous syntax."""
    if language not in LANGUAGES or type(source) is not str:
        raise SimilarityError("unknown language or invalid source")
    if len(source.encode()) > MAX_TEXT_BYTES:
        raise SimilarityError("similarity text byte limit exceeded")
    tokens = []
    position = 0
    while position < len(source):
        character = source[position]
        if character.isspace():
            position += 1
            continue
        if language == "python" and character == "#":
            newline = source.find("\n", position)
            position = len(source) if newline < 0 else newline + 1
            continue
        if language != "python" and source.startswith("//", position):
            newline = source.find("\n", position)
            position = len(source) if newline < 0 else newline + 1
            continue
        if language != "python" and source.startswith("/*", position):
            position = _consume_block_comment(source, position, nested=language == "rust")
            continue
        literal_end = _string_literal(language, source, position)
        if literal_end is not None:
            tokens.append("<str>")
            position = literal_end
            continue
        if character.isascii() and (character.isalpha() or character == "_" or
                                    character == "$" and language in ("java", "typescript")):
            end = position + 1
            while end < len(source) and source[end].isascii() and (
                    source[end].isalnum() or source[end] == "_" or
                    source[end] == "$" and language in ("java", "typescript")):
                end += 1
            token = source[position:end]
            tokens.append("<id>" if structural and token not in _KEYWORDS[language] else token)
            position = end
            continue
        if character.isascii() and character.isdigit():
            end = position + 1
            while end < len(source):
                item = source[end]
                if item.isascii() and (item.isalnum() or item in "_."):
                    end += 1
                elif item in "+-" and source[end - 1] in "eEpP":
                    end += 1
                else:
                    break
            tokens.append("<num>")
            position = end
            continue
        operator = next((item for item in _OPERATORS[language]
                         if source.startswith(item, position)), None)
        if operator is not None:
            tokens.append(operator)
            position += len(operator)
            continue
        if character in _PUNCTUATION:
            tokens.append(character)
            position += 1
            continue
        raise SimilarityError("code tokenization is ambiguous")
    if len(tokens) > MAX_TOKENS_PER_FIELD:
        raise SimilarityError("similarity token limit exceeded")
    return tuple(tokens)


def documentation_tokens(value):
    if type(value) is not str:
        raise SimilarityError("documentation must be text")
    if len(value.encode()) > MAX_TEXT_BYTES:
        raise SimilarityError("similarity text byte limit exceeded")
    tokens = tuple("<num>" if token[0].isdigit() else token.lower()
                   for token in _DOC_WORD.findall(value))
    if len(tokens) > MAX_TOKENS_PER_FIELD:
        raise SimilarityError("similarity token limit exceeded")
    return tokens


def _shingles(tokens, policy):
    minimum = policy["minimum_tokens_each"]
    if len(tokens) < minimum:
        return None
    width = policy["shingle_tokens"]
    return {tokens[index:index + width] for index in range(len(tokens) - width + 1)}


def _fuzzy_sets(shingles_a, shingles_b, policy):
    if shingles_a is None or shingles_b is None:
        return False
    union = shingles_a | shingles_b
    return bool(union) and len(shingles_a & shingles_b) / len(union) >= policy["threshold"]


def _fuzzy(tokens_a, tokens_b, policy):
    return _fuzzy_sets(_shingles(tokens_a, policy), _shingles(tokens_b, policy), policy)


def fuzzy_token_overlap(tokens_a, tokens_b):
    """Apply the frozen shingle metric directly to two token sequences."""
    if not isinstance(tokens_a, (tuple, list)) or not isinstance(tokens_b, (tuple, list)):
        raise SimilarityError("fuzzy token inputs are invalid")
    if len(tokens_a) > MAX_TOKENS_PER_FIELD or len(tokens_b) > MAX_TOKENS_PER_FIELD:
        raise SimilarityError("similarity token limit exceeded")
    policy, _digest = load_similarity_policy()
    return _fuzzy(tuple(tokens_a), tuple(tokens_b), policy["fuzzy"])


def _profile(value, policy):
    language = value.get("language")
    code = value.get("code")
    documentation = value.get("documentation")
    if type(code) is not str or type(documentation) is not str:
        raise SimilarityError("similarity row fields are invalid")
    normalized = code_tokens(language, code)
    structural = code_tokens(language, code, structural=True)
    docs = documentation_tokens(documentation)
    return {
        "language": language,
        "code": code,
        "documentation": documentation,
        "normalized": normalized,
        "structural": structural,
        "documentation_tokens": docs,
        "structural_shingles": _shingles(structural, policy["fuzzy"]),
        "documentation_shingles": _shingles(docs, policy["fuzzy"]),
    }


def _profile_field_overlap(first, second, field, policy):
    if first[field] == second[field]:
        return True
    if field == "documentation":
        left_tokens = first["documentation_tokens"]
        right_tokens = second["documentation_tokens"]
        return left_tokens == right_tokens or _fuzzy_sets(
            first["documentation_shingles"], second["documentation_shingles"], policy["fuzzy"]
        )
    if first["language"] != second["language"]:
        return False
    if first["normalized"] == second["normalized"]:
        return True
    return first["structural"] == second["structural"] or _fuzzy_sets(
        first["structural_shingles"], second["structural_shingles"], policy["fuzzy"]
    )


def rows_overlap(first, second):
    """Return whether code or documentation independently overlaps under policy v1."""
    policy, _digest = load_similarity_policy()
    first_profile = _profile(first, policy)
    second_profile = _profile(second, policy)
    return any(_profile_field_overlap(first_profile, second_profile, field, policy)
               for field in ("code", "documentation"))


def _reference_profile(reference, policy):
    if (not isinstance(reference, dict) or
            set(reference) != {"category", "source", "field", "language", "text"}):
        raise SimilarityError("reference corpus declaration is invalid")
    if reference["category"] not in REFERENCE_CATEGORIES:
        raise SimilarityError("reference corpus category is invalid")
    if reference["field"] not in ("code", "documentation"):
        raise SimilarityError("reference corpus field is invalid")
    if type(reference["source"]) is not str or not reference["source"]:
        raise SimilarityError("reference corpus source is invalid")
    if reference["field"] == "code":
        value = {"language": reference["language"], "code": reference["text"],
                 "documentation": "reference-documentation-placeholder"}
    else:
        value = {"language": "python", "code": "reference_code_placeholder",
                 "documentation": reference["text"]}
    return reference["field"], _profile(value, policy)


def validate_split_isolation(rows, references):
    """Reject lineage, project, row, or reference leakage before package admission."""
    if not isinstance(rows, list) or not isinstance(references, list):
        raise SimilarityError("split isolation inputs are invalid")
    if len(rows) > MAX_SIMILARITY_ROWS:
        raise SimilarityError("similarity row limit exceeded")
    if len(references) > MAX_REFERENCE_ITEMS:
        raise SimilarityError("similarity reference limit exceeded")
    comparisons = len(rows) * (len(rows) - 1) // 2 + len(rows) * len(references)
    if comparisons > MAX_COMPARISONS:
        raise SimilarityError("similarity comparison limit exceeded")
    deadline = time.monotonic() + MAX_SIMILARITY_SECONDS
    policy, _digest = load_similarity_policy()
    seen_ids = set()
    project_splits = {}
    lineage_splits = {}
    profiles = []
    for row in rows:
        if time.monotonic() > deadline:
            raise SimilarityError("similarity deadline exceeded")
        if not isinstance(row, dict):
            raise SimilarityError("split row is invalid")
        row_id = row.get("id")
        lineage = row.get("lineage_id")
        project = row.get("project")
        split = row.get("split")
        if type(row_id) is not str or not row_id or row_id in seen_ids:
            raise SimilarityError("split rows contain an invalid or duplicate id")
        seen_ids.add(row_id)
        if type(lineage) is not str or not lineage.strip():
            raise SimilarityError("every seed requires a nonempty lineage id")
        if split not in ("dev", "holdout") or type(project) is not str or not project:
            raise SimilarityError("split row identity is invalid")
        if project in project_splits and project_splits[project] != split:
            raise SimilarityError("project appears in both splits")
        if lineage in lineage_splits and lineage_splits[lineage] != split:
            raise SimilarityError("lineage appears in both splits")
        project_splits[project] = split
        lineage_splits[lineage] = split
        profiles.append(_profile(row, policy))
    reference_profiles = []
    for reference in references:
        if time.monotonic() > deadline:
            raise SimilarityError("similarity deadline exceeded")
        reference_profiles.append(_reference_profile(reference, policy))
    for index, first in enumerate(rows):
        if time.monotonic() > deadline:
            raise SimilarityError("similarity deadline exceeded")
        for second_index, second in enumerate(rows[index + 1:], start=index + 1):
            if time.monotonic() > deadline:
                raise SimilarityError("similarity deadline exceeded")
            if (first["split"] != second["split"] and any(
                    _profile_field_overlap(profiles[index], profiles[second_index], field, policy)
                    for field in ("code", "documentation"))):
                raise SimilarityError("row overlap crosses development and holdout")
        for field, reference_profile in reference_profiles:
            if time.monotonic() > deadline:
                raise SimilarityError("similarity deadline exceeded")
            if _profile_field_overlap(profiles[index], reference_profile, field, policy):
                raise SimilarityError("row overlaps a reference corpus")
    return True
