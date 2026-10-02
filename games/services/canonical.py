from __future__ import annotations

import json
import math
import hashlib
import unicodedata

from typing          import Any
from collections.abc import Mapping, Sequence


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by canonical JSON."""


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN e infinito nao pertencem ao contrato JSON.")

        return int(value) if value.is_integer() else value

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}

        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("Todas as chaves JSON devem ser strings.")

            normalized[unicodedata.normalize("NFC", key)] = _normalize(item)

        return normalized

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]

    raise CanonicalizationError(
        f"Tipo {type(value).__name__!r} nao pode ser serializado no contrato."
    )


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON suitable for hashing and signatures."""

    return json.dumps(
        _normalize(value),

        ensure_ascii=False     ,
        allow_nan   =False     ,
        separators  =(",", ":"),
        sort_keys   =True      ,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonicalize_topology(topology: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize order-insensitive graph collections without assuming numeric IDs."""

    normalized = _normalize(topology)
    if not isinstance(normalized, dict):
        raise CanonicalizationError("A topologia deve ser um objeto JSON.")

    nodes = normalized.get("nodes")
    if isinstance(nodes, list) and all(isinstance(node, dict) for node in nodes):
        normalized["nodes"] = sorted(nodes, key=lambda node: str(node.get("id", "")))

    edges = normalized.get("edges")
    if isinstance(edges, list) and all(isinstance(edge, dict) for edge in edges):
        canonical_edges = []

        for original in edges:
            edge   = dict    (original)
            source = edge.get("source")
            target = edge.get("target")

            if isinstance(source, str) and isinstance(target, str) and target < source:
                edge["source"], edge["target"] = target, source

            canonical_edges.append(edge)

        normalized["edges"] = sorted(
            canonical_edges,

            key=lambda edge: (
                str(edge.get("id"    , "")),
                str(edge.get("source", "")),
                str(edge.get("target", "")),
            ),
        )

    return normalized


def revision_content_hash(
    *,

    topology : Mapping[str, Any],
    rules    : Mapping[str, Any],

    schema_version      : int,
    formulation_version : str,
) -> str:
    """Hash only semantics that can change a solution, not labels or layout."""

    return canonical_sha256(
        {
            "schema_version"      : schema_version                 ,
            "formulation_version" : formulation_version            ,
            "topology"            : canonicalize_topology(topology),
            "rules"               : rules                          ,
        }
    )
