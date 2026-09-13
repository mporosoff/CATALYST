"""Bounded, unambiguous JSON for files, saved provenance, and API responses."""
import json
import math
from decimal import Decimal


class JSONInputError(ValueError):
    """A diagnostic that never includes input contents."""


def strict_loads(content, *, lexical_numbers=False, max_bytes=20 * 1024 * 1024):
    """Reject duplicate keys, non-finite numbers, invalid Unicode and deep trees."""
    def reject(*_):
        raise JSONInputError('Unsupported JSON number.')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise JSONInputError('Duplicate JSON key.')
            result[key] = value
        return result

    def numeric(text, convert):
        if len(text) > 128:
            raise JSONInputError('JSON number exceeds the supported precision.')
        value = text if lexical_numbers else convert(text)
        if convert is float and not lexical_numbers and value == 0 and Decimal(text) != 0:
            raise JSONInputError('JSON number is too small to preserve; export it as explicit decimal text.')
        return value

    try:
        if isinstance(content, bytes):
            if len(content) > max_bytes:
                raise JSONInputError('JSON exceeds the supported size.')
            text = content.decode('utf-8-sig')
        elif isinstance(content, str) and len(content.encode('utf-8')) <= max_bytes:
            text = content
        else:
            raise JSONInputError('JSON exceeds the supported size.')
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject,
            parse_int=lambda s: numeric(s, int), parse_float=lambda s: numeric(s, float))
        stack, nodes = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 64 or nodes > 1_000_000:
                raise JSONInputError('JSON structure exceeds the supported limits.')
            if isinstance(item, dict):
                if len(item) + len(stack) + nodes > 1_000_000:
                    raise JSONInputError('JSON structure exceeds the supported limits.')
                for key, child in item.items():
                    key.encode('utf-8')
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                if len(item) + len(stack) + nodes > 1_000_000:
                    raise JSONInputError('JSON structure exceeds the supported limits.')
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode('utf-8')
            elif isinstance(item, float) and not math.isfinite(item):
                reject()
        return value
    except (UnicodeError, RecursionError):
        raise JSONInputError('JSON contains invalid Unicode or excessive nesting.') from None
    except json.JSONDecodeError:
        raise JSONInputError('Malformed JSON.') from None
