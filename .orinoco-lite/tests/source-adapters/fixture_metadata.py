"""Build adapter-neutral fixtures from reviewed downstream metadata."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path

from orinoco_lite.annotations import (
    compact_enrichment_view,
    split_enrichment_view,
)
from orinoco_lite.canonical import canonical_yaml_bytes
import yaml


def neutralize_reviewed_adapter_state(
    root: Path,
    *,
    adapter_agent_pids: Iterable[str],
    decision_caches: Iterable[Path],
) -> int:
    """Remove only exact adapter-owned assertions from a copied test fixture."""

    agents = frozenset(adapter_agent_pids)
    if not agents:
        raise ValueError("at least one adapter Agent PID is required")

    for relative in decision_caches:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("decision-cache paths must be repository-relative")
        (root / relative).unlink(missing_ok=True)

    annotations = root / "metadata/overlays/annotations"
    records = root / "metadata/records"
    if not annotations.is_dir():
        return 0

    removed = 0
    for companion_path in sorted(annotations.rglob("*.yaml")):
        companion = yaml.safe_load(companion_path.read_text(encoding="utf-8"))
        if not isinstance(companion, dict):
            raise AssertionError(f"invalid annotation companion: {companion_path}")
        entries = companion.get("assertions")
        if not isinstance(entries, list):
            raise AssertionError(f"invalid annotation assertions: {companion_path}")

        if not any(
            isinstance(entry, dict) and entry.get("pav:importedBy") in agents
            for entry in entries
        ):
            continue

        relative = companion_path.relative_to(annotations)
        record_path = records / relative
        record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise AssertionError(f"invalid canonical record: {record_path}")

        working = compact_enrichment_view(record, companion)

        removed_value = object()

        def without_owned_assertions(value: object) -> object:
            nonlocal removed
            if isinstance(value, dict):
                annotations_value = value.get("annotations")
                if (
                    isinstance(annotations_value, dict)
                    and annotations_value.get("pav:importedBy") in agents
                ):
                    removed += 1
                    return removed_value
                cleaned: dict[str, object] = {}
                for key, child in value.items():
                    if not isinstance(key, str):
                        raise AssertionError(
                            "canonical fixture mappings require string keys"
                        )
                    replacement = without_owned_assertions(child)
                    if replacement is not removed_value:
                        cleaned[key] = replacement
                return cleaned
            if isinstance(value, list):
                cleaned_list = []
                for child in value:
                    replacement = without_owned_assertions(child)
                    if replacement is not removed_value:
                        cleaned_list.append(replacement)
                if value and not cleaned_list:
                    return removed_value
                return cleaned_list
            return deepcopy(value)

        cleaned = without_owned_assertions(working)
        if not isinstance(cleaned, dict):
            raise AssertionError("the top-level canonical record cannot be imported")
        record, remaining_companion = split_enrichment_view(cleaned)
        if remaining_companion is not None:
            companion_path.write_bytes(canonical_yaml_bytes(remaining_companion))
        else:
            companion_path.unlink()
        record_path.write_bytes(canonical_yaml_bytes(record))

    return removed
