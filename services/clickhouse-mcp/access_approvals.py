"""Strict JSON parsing shared by the platform-verification and enrollment routes."""

import json


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _json(data):
    def invalid_constant(_):
        raise ValueError("Invalid JSON constant")

    return json.loads(data, object_pairs_hook=_unique, parse_constant=invalid_constant)
