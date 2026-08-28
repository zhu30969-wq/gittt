#!/usr/bin/env python3
"""Read-only audit of a CUMCM contract project.

This program verifies structure, identifiers, file hashes, dependency freshness
and evidence reachability.  It intentionally does *not* execute experiments or
claim that a mathematical model is correct.  Human reviews remain mandatory
for scientific suitability and interpretation when a project is released.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    from _contract_support import (
        SHA256_RE,
        TYPED_ID_RE,
        VALIDATION_STATUSES,
        aggregate_status,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_text_exclusive,
    )
except ImportError as exc:  # pragma: no cover - used for a clean ENV_BLOCK failure
    print(
        json.dumps(
            {"status": "ENV_BLOCK", "message": f"missing script dependency: {exc}"},
            ensure_ascii=False,
        )
    )
    raise SystemExit(11)


SCHEMA_BY_KIND = {
    "manifest": "manifest.schema.json",
    "problem_spec": "problem_spec.schema.json",
    "model_spec": "model_spec.schema.json",
    "experiment": "experiment.schema.json",
    "results": "results.schema.json",
    "claims": "claims.schema.json",
    "figures": "figures.schema.json",
    "gate_review": "gate_review.schema.json",
}

GATE_BY_KIND = {
    "manifest": "G7",
    "problem_spec": "G1",
    "model_spec": "G2",
    "experiment": "G3",
    "results": "G4",
    "claims": "G5",
    "figures": "G5",
    "gate_review": "G7",
}

GATES = tuple(f"G{number}" for number in range(8))
ZERO_HASH = "0" * 64


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit CUMCM schemas, cross-file references, hashes and stage gates without modifying the project."
    )
    parser.add_argument("project_root", type=Path, help="Directory containing manifest.yaml")
    parser.add_argument(
        "--schema-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "references" / "schemas",
        help="Directory containing the bundled JSON schemas",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optionally create a new report file; an existing path is never overwritten",
    )
    return parser.parse_args()


class Audit:
    """Collect findings and the parsed artifact graph for one project."""

    def __init__(self, root: Path, schema_root: Path) -> None:
        self.root = root.resolve()
        self.schema_root = schema_root.resolve()
        self.findings: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, Any]] = {}
        self.document_paths: dict[str, Path] = {}
        self.manifest: dict[str, Any] | None = None
        self.manifest_artifacts: dict[str, dict[str, Any]] = {}
        self.current_hashes: dict[str, str] = {}
        self.id_definitions: dict[str, tuple[str, str, str]] = {}
        self.result_eligibility: dict[str, bool] = {}
        self.stale_roots: set[str] = set()
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_registry = Registry()

    def add(
        self,
        gate: str,
        status: str,
        code: str,
        message: str,
        *,
        path: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        if gate not in GATES:
            raise ValueError(f"unknown gate: {gate}")
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"unknown validation status: {status}")
        finding: dict[str, Any] = {
            "gate": gate,
            "status": status,
            "code": code,
            "message": message,
        }
        if path is not None:
            finding["path"] = path
        if artifact_id is not None:
            finding["artifact_id"] = artifact_id
        self.findings.append(finding)

    def load_schemas(self) -> bool:
        """Load every schema once and pre-check each schema itself."""

        if not self.schema_root.is_dir():
            self.add("G0", "ENV_BLOCK", "SCHEMA_DIR_MISSING", f"schema directory not found: {self.schema_root}")
            return False
        try:
            for path in sorted(self.schema_root.glob("*.schema.json")):
                schema = json.loads(path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                # The distributed schemas use portable relative $id values.
                # At runtime we give each schema an absolute local retrieval
                # URI so relative refs such as common.schema.json cannot fall
                # through to jsonschema's remote retriever.
                schema["$id"] = path.resolve().as_uri()
                self.schemas[path.name] = schema
                self.schema_registry = self.schema_registry.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
        except Exception as exc:
            self.add("G0", "ENV_BLOCK", "SCHEMA_LOAD_FAILED", str(exc))
            return False

        required = {"common.schema.json", *SCHEMA_BY_KIND.values()}
        missing = sorted(required.difference(self.schemas))
        if missing:
            self.add("G0", "ENV_BLOCK", "SCHEMA_MISSING", f"missing schemas: {', '.join(missing)}")
            return False
        self.add("G0", "PASS", "SCHEMAS_READY", "all required schemas loaded and passed schema self-check")
        return True

    def validate_schema(self, document: dict[str, Any], path: Path, gate: str) -> bool:
        kind = document.get("kind")
        schema_name = SCHEMA_BY_KIND.get(kind)
        relative = self._display_path(path)
        if schema_name is None:
            self.add(gate, "BLOCK", "UNKNOWN_KIND", f"unsupported contract kind: {kind!r}", path=relative)
            return False

        schema = self.schemas[schema_name]
        # The local store makes relative $ref values deterministic and avoids
        # network retrieval.  This does not restrict the modeling workflow's
        # use of online sources; it only keeps schema validation self-contained.
        validator = Draft202012Validator(
            schema,
            registry=self.schema_registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
        for error in errors:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            self.add(
                gate,
                "BLOCK",
                "SCHEMA_INVALID",
                f"{location}: {error.message}",
                path=relative,
                artifact_id=document.get("id"),
            )
        if not errors:
            self.add(gate, "PASS", "SCHEMA_VALID", "contract matches its schema", path=relative, artifact_id=document.get("id"))
        return not errors

    def _display_path(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def load_manifest(self) -> bool:
        path = self.root / "manifest.yaml"
        if not path.is_file():
            self.add("G7", "BLOCK", "MANIFEST_MISSING", "manifest.yaml is required")
            return False
        try:
            document = load_yaml(path)
        except Exception as exc:
            self.add("G7", "BLOCK", "YAML_INVALID", str(exc), path="manifest.yaml")
            return False
        if not isinstance(document, dict):
            self.add("G7", "BLOCK", "DOCUMENT_NOT_OBJECT", "manifest must be a YAML mapping", path="manifest.yaml")
            return False
        if not self.validate_schema(document, path, "G7"):
            # Semantic traversal assumes a structurally valid manifest.  Do
            # not continue through missing or wrongly typed collections and
            # risk turning a useful schema finding into a traceback.
            return False
        self.manifest = document
        return True

    def load_artifacts(self) -> None:
        """Verify manifest rows and load each declared contract document."""

        assert self.manifest is not None
        seen_paths: dict[str, str] = {}
        seen_casefold: dict[str, str] = {}
        for entry in self.manifest.get("artifacts", []):
            if not isinstance(entry, dict):
                continue
            artifact_id = entry.get("id")
            relative = entry.get("path")
            if not isinstance(artifact_id, str) or not isinstance(relative, str):
                continue
            if artifact_id in self.manifest_artifacts:
                self.add("G7", "BLOCK", "DUPLICATE_MANIFEST_ID", f"duplicate manifest artifact ID: {artifact_id}")
                continue
            self.manifest_artifacts[artifact_id] = entry

            if relative in seen_paths:
                self.add("G7", "BLOCK", "DUPLICATE_PATH", f"path used by {seen_paths[relative]} and {artifact_id}", path=relative)
            else:
                seen_paths[relative] = artifact_id
            folded = relative.casefold()
            if folded in seen_casefold and seen_casefold[folded] != relative:
                self.add("G7", "BLOCK", "CASE_COLLISION", f"paths collide on case-insensitive filesystems: {seen_casefold[folded]} and {relative}")
            else:
                seen_casefold[folded] = relative

            try:
                path = safe_project_path(self.root, relative)
            except ValueError as exc:
                self.add("G7", "BLOCK", "PATH_UNSAFE", str(exc), path=relative, artifact_id=artifact_id)
                continue
            if not path.is_file():
                status = "BLOCK" if entry.get("required", True) else "WARN"
                self.add("G7", status, "ARTIFACT_MISSING", "declared artifact file does not exist", path=relative, artifact_id=artifact_id)
                continue

            actual_hash = sha256_file(path)
            self.current_hashes[artifact_id] = actual_hash
            expected_hash = entry.get("sha256")
            if expected_hash == ZERO_HASH:
                self.add("G7", "STALE", "HASH_PLACEHOLDER", "manifest still contains a placeholder hash", path=relative, artifact_id=artifact_id)
                self.stale_roots.add(artifact_id)
            elif expected_hash != actual_hash:
                self.add("G7", "STALE", "ARTIFACT_HASH_MISMATCH", f"expected {expected_hash}, got {actual_hash}", path=relative, artifact_id=artifact_id)
                self.stale_roots.add(artifact_id)
            else:
                self.add("G7", "PASS", "ARTIFACT_HASH_MATCH", "manifest hash matches file bytes", path=relative, artifact_id=artifact_id)

            try:
                document = load_yaml(path)
            except Exception as exc:
                self.add(GATE_BY_KIND.get(entry.get("kind"), "G7"), "BLOCK", "YAML_INVALID", str(exc), path=relative, artifact_id=artifact_id)
                continue
            if not isinstance(document, dict):
                self.add(GATE_BY_KIND.get(entry.get("kind"), "G7"), "BLOCK", "DOCUMENT_NOT_OBJECT", "contract must be a YAML mapping", path=relative, artifact_id=artifact_id)
                continue

            gate = GATE_BY_KIND.get(entry.get("kind"), "G7")
            schema_valid = self.validate_schema(document, path, gate)
            if document.get("id") != artifact_id:
                self.add(gate, "BLOCK", "ID_MISMATCH", f"manifest ID {artifact_id!r} differs from document ID {document.get('id')!r}", path=relative, artifact_id=artifact_id)
                schema_valid = False
            if document.get("kind") != entry.get("kind"):
                self.add(gate, "BLOCK", "KIND_MISMATCH", f"manifest kind {entry.get('kind')!r} differs from document kind {document.get('kind')!r}", path=relative, artifact_id=artifact_id)
                schema_valid = False
            if not schema_valid:
                # Keep manifest/hash findings, but exclude the malformed
                # document from all semantic checks.
                continue
            manifest_dependencies = set(entry.get("depends_on", []))
            document_dependencies = set(document.get("depends_on", []))
            if manifest_dependencies != document_dependencies:
                self.add(
                    "G7",
                    "BLOCK",
                    "DEPENDENCY_DECLARATION_MISMATCH",
                    (
                        f"manifest dependencies {sorted(manifest_dependencies)} differ from "
                        f"{artifact_id} document dependencies {sorted(document_dependencies)}"
                    ),
                    path=relative,
                    artifact_id=artifact_id,
                )
            self.documents[artifact_id] = document
            self.document_paths[artifact_id] = path

    def validate_manifest_files(self) -> None:
        """Validate environment, entrypoint and deliverable files in manifest.

        Unlike artifact files, these rows are not YAML contracts.  They still
        receive stable typed IDs and current hashes so fingerprint-bound G6/G7
        reviews become stale whenever the actual paper or release bytes change.
        Environment drift marks every experiment root stale; the artifact DAG
        then propagates that state to results, claims, figures and reviews.
        """

        if not self.manifest:
            return

        experiment_ids = {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") == "experiment"
        }
        for item in self.manifest.get("environment_files", []):
            self._verify_manifest_file_ref(
                item,
                role="environment",
                missing_status="BLOCK",
                stale_targets=experiment_ids,
            )

        for name, relative in self.manifest.get("entrypoints", {}).items():
            entrypoint_id = f"entrypoint:{name}"
            try:
                path = safe_project_path(self.root, relative)
            except (TypeError, ValueError) as exc:
                self.add("G7", "BLOCK", "ENTRYPOINT_PATH_UNSAFE", str(exc), path=relative, artifact_id=entrypoint_id)
                continue
            if not path.is_file():
                self.add("G7", "BLOCK", "ENTRYPOINT_MISSING", "declared entrypoint does not exist", path=relative, artifact_id=entrypoint_id)
                continue
            self.current_hashes[entrypoint_id] = sha256_file(path)
            self.add("G7", "PASS", "ENTRYPOINT_PRESENT", "declared entrypoint exists and was fingerprinted", path=relative, artifact_id=entrypoint_id)

        for item in self.manifest.get("deliverables", []):
            self._verify_manifest_file_ref(
                item,
                role="deliverable",
                missing_status="BLOCK" if item.get("required") else "WARN",
                stale_targets=set(),
            )

    def _verify_manifest_file_ref(
        self,
        item: Any,
        *,
        role: str,
        missing_status: str,
        stale_targets: set[str],
    ) -> None:
        if not isinstance(item, dict):
            self.add("G7", "BLOCK", f"{role.upper()}_ROW_INVALID", f"manifest {role} row is not a mapping")
            return
        item_id = item.get("id")
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(item_id, str) or not TYPED_ID_RE.fullmatch(item_id):
            self.add("G7", "BLOCK", f"{role.upper()}_ID_INVALID", f"manifest {role} requires a typed id")
            return
        try:
            path = safe_project_path(self.root, relative)
        except (TypeError, ValueError) as exc:
            self.add("G7", "BLOCK", f"{role.upper()}_PATH_UNSAFE", str(exc), path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
            return
        if not path.is_file():
            self.add("G7", missing_status, f"{role.upper()}_MISSING", f"declared {role} does not exist", path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
            return
        actual = sha256_file(path)
        self.current_hashes[item_id] = actual
        if expected == ZERO_HASH:
            self.add("G7", "STALE", f"{role.upper()}_HASH_PLACEHOLDER", f"{role} hash is still a placeholder", path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
        elif expected != actual:
            self.add("G7", "STALE", f"{role.upper()}_HASH_MISMATCH", f"expected {expected}, got {actual}", path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
        else:
            self.add("G7", "PASS", f"{role.upper()}_HASH_MATCH", f"{role} hash matches file bytes", path=relative, artifact_id=item_id)

    def verify_embedded_files(self) -> None:
        """Check every nested object shaped like a {path, sha256} file ref."""

        for artifact_id, document in self.documents.items():
            kind = document.get("kind")
            gate = "G0" if kind == "problem_spec" else GATE_BY_KIND.get(kind, "G7")
            for location, file_ref in iter_file_refs(document):
                relative = file_ref.get("path")
                expected = file_ref.get("sha256")
                if not isinstance(relative, str) or not isinstance(expected, str):
                    continue
                try:
                    path = safe_project_path(self.root, relative)
                except ValueError as exc:
                    self.add(gate, "BLOCK", "FILE_PATH_UNSAFE", str(exc), path=relative, artifact_id=artifact_id)
                    continue
                if not path.is_file():
                    self.add(gate, "BLOCK", "REFERENCED_FILE_MISSING", f"referenced at {location}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                    continue
                actual = sha256_file(path)
                if expected == ZERO_HASH:
                    self.add(gate, "STALE", "FILE_HASH_PLACEHOLDER", f"placeholder hash at {location}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                elif expected != actual:
                    self.add(gate, "STALE", "FILE_HASH_MISMATCH", f"expected {expected}, got {actual}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                else:
                    self.add(gate, "PASS", "FILE_HASH_MATCH", f"referenced file matches at {location}", path=relative, artifact_id=artifact_id)

    def validate_ids_and_refs(self) -> None:
        """Build a nested-ID registry and reject dangling cross-file refs."""

        definitions: dict[str, tuple[str, str, str]] = {}
        for artifact_id, document in self.documents.items():
            for location, value in iter_defined_ids(document):
                if value in definitions:
                    first_artifact, first_location, _first_kind = definitions[value]
                    self.add(
                        GATE_BY_KIND.get(document.get("kind"), "G7"),
                        "BLOCK",
                        "DUPLICATE_ID",
                        f"{value} already defined in {first_artifact} at {first_location}; repeated at {location}",
                        artifact_id=artifact_id,
                    )
                else:
                    definitions[value] = (artifact_id, location, document.get("kind", "unknown"))

        manifest_id = self.manifest.get("id") if self.manifest else None
        if isinstance(manifest_id, str):
            definitions.setdefault(manifest_id, (manifest_id, "<root>", "manifest"))

        # Manifest-owned files are first-class review evidence even though
        # they are not contract artifacts.  Their typed IDs participate in
        # reference resolution and their current bytes live in current_hashes.
        if self.manifest:
            virtual_definitions: list[tuple[str, str]] = []
            virtual_definitions.extend((item.get("id"), "environment_file") for item in self.manifest.get("environment_files", []))
            virtual_definitions.extend((item.get("id"), "deliverable") for item in self.manifest.get("deliverables", []))
            virtual_definitions.extend((f"entrypoint:{name}", "entrypoint") for name in self.manifest.get("entrypoints", {}))
            for virtual_id, virtual_kind in virtual_definitions:
                if not isinstance(virtual_id, str):
                    continue
                if virtual_id in definitions:
                    first_artifact, first_location, _first_kind = definitions[virtual_id]
                    self.add("G7", "BLOCK", "DUPLICATE_ID", f"{virtual_id} already defined in {first_artifact} at {first_location}")
                else:
                    definitions[virtual_id] = ("manifest:virtual", f"manifest/{virtual_kind}", virtual_kind)

        self.id_definitions = definitions

        for artifact_id, document in self.documents.items():
            gate = GATE_BY_KIND.get(document.get("kind"), "G7")
            # A structured reference such as evidence_refs[*].ref is found by
            # both its container rule and the generic ``ref`` rule during the
            # recursive walk.  Report each concrete location only once so a
            # valid contract does not produce duplicated PASS findings.
            seen_references: set[tuple[str, str]] = set()
            for location, reference in iter_references(document):
                reference_key = (location, reference)
                if reference_key in seen_references:
                    continue
                seen_references.add(reference_key)
                if reference not in definitions:
                    self.add(gate, "BLOCK", "DANGLING_REFERENCE", f"{reference} at {location} has no local definition", artifact_id=artifact_id)
                else:
                    target_kind = definitions[reference][2]
                    allowed_kinds = expected_reference_kinds(document.get("kind"), location)
                    if allowed_kinds is not None and target_kind not in allowed_kinds:
                        self.add(
                            gate,
                            "BLOCK",
                            "REFERENCE_KIND_MISMATCH",
                            f"{reference} at {location} resolves to {target_kind}, expected one of {sorted(allowed_kinds)}",
                            artifact_id=artifact_id,
                        )
                    else:
                        self.add(gate, "PASS", "REFERENCE_RESOLVED", f"{reference} at {location} resolves to {target_kind}", artifact_id=artifact_id)

        # Manifest dependency refs are checked even if an artifact file failed
        # to parse, because these refs define stale propagation and release order.
        declared = set(self.manifest_artifacts)
        for artifact_id, entry in self.manifest_artifacts.items():
            for dependency in entry.get("depends_on", []):
                if dependency not in declared:
                    self.add("G7", "BLOCK", "DANGLING_MANIFEST_DEPENDENCY", f"{artifact_id} depends on undeclared {dependency}")

    def validate_dag_and_propagate_stale(self) -> None:
        graph = {
            artifact_id: sorted(
                {
                    *entry.get("depends_on", []),
                    *self.documents.get(artifact_id, {}).get("depends_on", []),
                }.intersection(self.manifest_artifacts)
            )
            for artifact_id, entry in self.manifest_artifacts.items()
        }
        state: dict[str, int] = {node: 0 for node in graph}
        stack: list[str] = []

        def visit(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for dependency in graph[node]:
                if state[dependency] == 0:
                    visit(dependency)
                elif state[dependency] == 1:
                    cycle_start = stack.index(dependency)
                    cycle = stack[cycle_start:] + [dependency]
                    self.add("G7", "BLOCK", "DEPENDENCY_CYCLE", " -> ".join(cycle))
            stack.pop()
            state[node] = 2

        for node in graph:
            if state[node] == 0:
                visit(node)

        reverse: dict[str, set[str]] = defaultdict(set)
        for child, parents in graph.items():
            for parent in parents:
                reverse[parent].add(child)
        queue: deque[str] = deque(self.stale_roots)
        affected = set(self.stale_roots)
        while queue:
            changed = queue.popleft()
            for dependent in reverse.get(changed, set()):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)
                    self.add(
                        GATE_BY_KIND.get(self.manifest_artifacts[dependent].get("kind"), "G7"),
                        "STALE",
                        "UPSTREAM_STALE",
                        f"{dependent} depends transitively on changed or missing {changed}",
                        artifact_id=dependent,
                    )
        if not any(finding["code"] == "DEPENDENCY_CYCLE" for finding in self.findings):
            self.add("G7", "PASS", "DEPENDENCY_DAG", "manifest dependency graph is acyclic")

    def validate_scientific_invariants(self) -> None:
        """Validate executable evidence invariants without proving mathematics.

        The checks here deliberately distinguish a historical failed run from
        an invalid evidentiary result: failed history may remain in a release,
        but only a successful, current, internally consistent and
        acceptance-passing result may support a final claim.
        """

        problems = [doc for doc in self.documents.values() if doc.get("kind") == "problem_spec"]
        models = [doc for doc in self.documents.values() if doc.get("kind") == "model_spec"]
        experiments = [doc for doc in self.documents.values() if doc.get("kind") == "experiment"]
        results = [doc for doc in self.documents.values() if doc.get("kind") == "results"]
        claims_docs = [doc for doc in self.documents.values() if doc.get("kind") == "claims"]
        figures_docs = [doc for doc in self.documents.values() if doc.get("kind") == "figures"]

        addressed = {reference for model in models for reference in model.get("addresses", [])}
        for problem in problems:
            for ambiguity in problem.get("ambiguities", []):
                if ambiguity.get("severity") == "high" and ambiguity.get("status") == "open":
                    self.add("G1", "BLOCK", "HIGH_AMBIGUITY_OPEN", ambiguity.get("text", "high ambiguity remains open"), artifact_id=problem.get("id"))
            for question in problem.get("questions", []):
                question_id = question.get("id")
                if question_id not in addressed:
                    self.add("G2", "BLOCK", "QUESTION_NOT_MODELED", f"no model addresses {question_id}", artifact_id=problem.get("id"))
                else:
                    self.add("G2", "PASS", "QUESTION_MODELED", f"at least one model addresses {question_id}", artifact_id=problem.get("id"))

        for model in models:
            symbol_ids = {symbol.get("id") for symbol in model.get("symbols", [])}
            for section in ("equations", "objectives", "constraints"):
                for formula in model.get("formulation", {}).get(section, []):
                    for symbol_ref in [*formula.get("defines", []), *formula.get("uses", [])]:
                        if symbol_ref not in symbol_ids:
                            self.add("G2", "BLOCK", "FORMULA_SYMBOL_UNDECLARED", f"{formula.get('id')} refers to undeclared {symbol_ref}", artifact_id=model.get("id"))
            entrypoint = model.get("algorithm", {}).get("entrypoint")
            if isinstance(entrypoint, str):
                try:
                    code_path = safe_project_path(self.root, entrypoint)
                    if not code_path.is_file():
                        self.add("G2", "BLOCK", "MODEL_ENTRYPOINT_MISSING", "algorithm entrypoint does not exist", path=entrypoint, artifact_id=model.get("id"))
                except ValueError as exc:
                    self.add("G2", "BLOCK", "MODEL_ENTRYPOINT_UNSAFE", str(exc), path=entrypoint, artifact_id=model.get("id"))

        experiments_by_id = {document.get("id"): document for document in experiments}
        results_by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in results:
            results_by_experiment[result.get("experiment_ref")].append(result)
        for experiment in experiments:
            if not results_by_experiment.get(experiment.get("id")):
                self.add("G4", "BLOCK", "EXPERIMENT_WITHOUT_RESULT", f"no result artifact refers to {experiment.get('id')}")
            if experiment.get("mode") != "exploratory" and not experiment.get("acceptance_rules"):
                self.add("G4", "BLOCK", "ACCEPTANCE_RULES_REQUIRED", f"{experiment.get('id')} is {experiment.get('mode')} but has no acceptance rule")
            for rule in experiment.get("acceptance_rules", []):
                if rule.get("registration_timing") != "post_result":
                    continue
                if experiment.get("mode") == "exploratory":
                    self.add(
                        "G4",
                        "WARN",
                        "POST_HOC_ACCEPTANCE_RULE",
                        f"{experiment.get('id')}/{rule.get('metric_ref')} was registered after results and is exploratory only",
                        artifact_id=experiment.get("id"),
                    )
                else:
                    self.add(
                        "G4",
                        "BLOCK",
                        "CONFIRMATORY_RULE_POST_HOC",
                        f"{experiment.get('id')}/{rule.get('metric_ref')} cannot be confirmatory because it was registered after results",
                        artifact_id=experiment.get("id"),
                    )

        for result in results:
            result_id = result.get("id")
            experiment = experiments_by_id.get(result.get("experiment_ref"))
            eligible = True
            if experiment is None:
                self.add("G4", "BLOCK", "RESULT_EXPERIMENT_MISSING", f"{result_id} does not resolve to an experiment", artifact_id=result_id)
                self.result_eligibility[result_id] = False
                continue

            run = result.get("run", {})
            if run.get("argv") != experiment.get("command", {}).get("argv"):
                self.add("G4", "BLOCK", "RUN_ARGV_MISMATCH", f"{result_id} argv differs from its experiment", artifact_id=result_id)
                eligible = False
            if run.get("cwd") != experiment.get("command", {}).get("cwd"):
                self.add("G4", "BLOCK", "RUN_CWD_MISMATCH", f"{result_id} cwd differs from its experiment", artifact_id=result_id)
                eligible = False
            if run.get("seeds") != experiment.get("seeds"):
                self.add("G4", "BLOCK", "RUN_SEEDS_MISMATCH", f"{result_id} seeds differ from its experiment", artifact_id=result_id)
                eligible = False
            try:
                started = parse_rfc3339(run.get("started_at"))
                finished = parse_rfc3339(run.get("finished_at"))
                if finished < started:
                    raise ValueError("finished_at precedes started_at")
            except ValueError as exc:
                self.add("G4", "BLOCK", "RUN_TIME_INVALID", f"{result_id}: {exc}", artifact_id=result_id)
                eligible = False

            successful = result.get("run_status") == "success"
            if successful:
                if run.get("exit_code") != 0:
                    self.add("G4", "BLOCK", "SUCCESS_EXIT_CODE_NONZERO", f"{result_id} is successful but exit_code={run.get('exit_code')}", artifact_id=result_id)
                    eligible = False
                else:
                    self.add("G4", "PASS", "RUN_SUCCESSFUL", f"{result_id} records success with exit_code 0", artifact_id=result_id)
            else:
                self.add("G4", "NOT_APPLICABLE", "HISTORICAL_RESULT_NOT_SUCCESSFUL", f"{result_id} is retained as {result.get('run_status')} history and cannot support final claims", artifact_id=result_id)
                eligible = False

            output_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for output in result.get("outputs", []):
                output_rows[output.get("output_ref")].append(output)
                if successful and output.get("comparison_status") != "PASS":
                    self.add("G4", "BLOCK", "OUTPUT_COMPARISON_NOT_PASS", f"{result_id}/{output.get('output_ref')} comparison is {output.get('comparison_status')}", artifact_id=result_id)
                    eligible = False
            for declared in experiment.get("outputs", []):
                matches = output_rows.get(declared.get("id"), [])
                if declared.get("required") and len(matches) != 1:
                    self.add("G4", "BLOCK", "REQUIRED_OUTPUT_AMBIGUOUS", f"{result_id} has {len(matches)} rows for required {declared.get('id')}", artifact_id=result_id)
                    eligible = False

            metric_specs = {metric.get("id"): metric for metric in experiment.get("metrics", [])}
            metric_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for metric in result.get("metrics", []):
                metric_ref = metric.get("metric_ref")
                metric_rows[metric_ref].append(metric)
                measurement = metric.get("measurement", {})
                value = measurement.get("value")
                if value is not None and not is_finite_number(value):
                    self.add("G4", "BLOCK", "RESULT_METRIC_NONFINITE", f"{result_id}/{metric_ref} is non-finite", artifact_id=result_id)
                    eligible = False
                spec = metric_specs.get(metric_ref)
                if spec is None:
                    self.add("G4", "BLOCK", "RESULT_METRIC_UNDECLARED", f"{result_id}/{metric_ref} is not declared by the experiment", artifact_id=result_id)
                    eligible = False
                elif measurement.get("unit") != spec.get("unit"):
                    self.add("G4", "BLOCK", "RESULT_METRIC_UNIT_MISMATCH", f"{result_id}/{metric_ref} unit differs from the experiment", artifact_id=result_id)
                    eligible = False
            for metric_ref, rows in metric_rows.items():
                if len(rows) != 1:
                    self.add("G4", "BLOCK", "RESULT_METRIC_AMBIGUOUS", f"{result_id} has {len(rows)} rows for {metric_ref}", artifact_id=result_id)
                    eligible = False

            required_fingerprints = {experiment.get("id"), *self.artifact_dependency_closure(experiment.get("id"))}
            recorded_fingerprints = result.get("fingerprints", {})
            missing_fingerprints = required_fingerprints.difference(recorded_fingerprints)
            if missing_fingerprints:
                self.add("G4", "BLOCK", "RESULT_FINGERPRINT_CLOSURE_MISSING", f"{result_id} lacks fingerprints for {sorted(missing_fingerprints)}", artifact_id=result_id)
                eligible = False
            for dependency_id, recorded_hash in recorded_fingerprints.items():
                current = self.current_hashes.get(dependency_id)
                if current is None:
                    self.add("G4", "BLOCK", "FINGERPRINT_TARGET_MISSING", f"no current hash for {dependency_id}", artifact_id=result_id)
                    eligible = False
                elif recorded_hash != current:
                    self.add("G4", "STALE", "RESULT_FINGERPRINT_STALE", f"{dependency_id} changed since {result_id}", artifact_id=result_id)
                    self.stale_roots.add(result_id)
                    eligible = False
                else:
                    self.add("G4", "PASS", "RESULT_FINGERPRINT_CURRENT", f"{dependency_id} fingerprint is current", artifact_id=result_id)
            if result_id in self.stale_roots or required_fingerprints.intersection(self.stale_roots):
                eligible = False

            if successful:
                for rule in experiment.get("acceptance_rules", []):
                    if not self.validate_acceptance_rule(result, experiment, rule, metric_rows, metric_specs):
                        eligible = False

            self.result_eligibility[result_id] = eligible and successful

        self.validate_claims(claims_docs, results)
        self.validate_question_evidence_paths(problems, models, experiments, results, claims_docs)

        for registry in figures_docs:
            for figure in registry.get("figures", []):
                if figure.get("provenance_type") == "derived" and not figure.get("source_result_refs"):
                    self.add("G5", "BLOCK", "DERIVED_FIGURE_WITHOUT_RESULT", f"{figure.get('id')} has no source result", artifact_id=registry.get("id"))

    def artifact_dependency_closure(self, artifact_id: str) -> set[str]:
        closure: set[str] = set()
        queue: deque[str] = deque([artifact_id])
        while queue:
            current = queue.popleft()
            entry = self.manifest_artifacts.get(current, {})
            dependencies = {
                *entry.get("depends_on", []),
                *self.documents.get(current, {}).get("depends_on", []),
            }
            for dependency in dependencies:
                if dependency in self.manifest_artifacts and dependency not in closure:
                    closure.add(dependency)
                    queue.append(dependency)
        closure.discard(artifact_id)
        return closure

    def validate_acceptance_rule(
        self,
        result: dict[str, Any],
        experiment: dict[str, Any],
        rule: dict[str, Any],
        metric_rows: dict[str, list[dict[str, Any]]],
        metric_specs: dict[str, dict[str, Any]],
    ) -> bool:
        result_id = result.get("id")
        metric_ref = rule.get("metric_ref")
        rows = metric_rows.get(metric_ref, [])
        if len(rows) != 1:
            self.add("G4", "BLOCK", "ACCEPTANCE_METRIC_AMBIGUOUS", f"{result_id} has {len(rows)} values for acceptance metric {metric_ref}", artifact_id=result_id)
            return False
        spec = metric_specs.get(metric_ref)
        measurement = rows[0].get("measurement", {})
        value = measurement.get("value")
        threshold = rule.get("threshold")
        if not is_finite_number(value) or not is_finite_number(threshold):
            self.add("G4", "BLOCK", "ACCEPTANCE_VALUE_NONFINITE", f"{result_id}/{metric_ref} or its threshold is non-finite", artifact_id=result_id)
            return False
        if spec is None or measurement.get("unit") != rule.get("unit") or spec.get("unit") != rule.get("unit"):
            self.add("G4", "BLOCK", "ACCEPTANCE_UNIT_MISMATCH", f"{result_id}/{metric_ref} acceptance units do not agree", artifact_id=result_id)
            return False
        operator = rule.get("operator")
        operations = {
            "==": lambda left, right: left == right,
            "!=": lambda left, right: left != right,
            "<": lambda left, right: left < right,
            "<=": lambda left, right: left <= right,
            ">": lambda left, right: left > right,
            ">=": lambda left, right: left >= right,
        }
        operation = operations.get(operator)
        if operation is None:
            self.add("G4", "BLOCK", "ACCEPTANCE_OPERATOR_INVALID", f"unsupported acceptance operator {operator!r}", artifact_id=result_id)
            return False
        if not operation(float(value), float(threshold)):
            self.add("G4", "BLOCK", "ACCEPTANCE_RULE_FAILED", f"{result_id}: {value} {operator} {threshold} is false", artifact_id=result_id)
            return False
        self.add("G4", "PASS", "ACCEPTANCE_RULE_PASS", f"{result_id}: {metric_ref} satisfies {operator} {threshold}", artifact_id=result_id)
        return True

    def validate_claims(self, claims_docs: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
        result_by_id = {result.get("id"): result for result in results}
        for registry in claims_docs:
            for claim in registry.get("claims", []):
                evidence_result_ids = [
                    item.get("ref")
                    for item in claim.get("evidence_refs", [])
                    if self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
                ]
                for assertion in claim.get("numeric_assertions", []):
                    metric_ref = assertion.get("metric_ref")
                    matches: list[tuple[str, dict[str, Any]]] = []
                    for result_id in evidence_result_ids:
                        for metric in result_by_id.get(result_id, {}).get("metrics", []):
                            if metric.get("metric_ref") == metric_ref:
                                matches.append((result_id, metric))
                    if len(matches) != 1:
                        code = "NUMERIC_ASSERTION_UNSUPPORTED" if not matches else "NUMERIC_ASSERTION_AMBIGUOUS"
                        self.add("G5", "BLOCK", code, f"{claim.get('id')} has {len(matches)} directly evidenced values for {metric_ref}", artifact_id=registry.get("id"))
                        continue
                    result_id, metric = matches[0]
                    measurement = metric.get("measurement", {})
                    values = (
                        measurement.get("value"),
                        assertion.get("reported_value"),
                        assertion.get("absolute_tolerance"),
                        assertion.get("relative_tolerance"),
                    )
                    if not all(is_finite_number(value) for value in values):
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_NONFINITE", f"{claim.get('id')} has a non-finite numeric assertion", artifact_id=registry.get("id"))
                        continue
                    if measurement.get("unit") != assertion.get("unit"):
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_UNIT_MISMATCH", f"{claim.get('id')} unit differs from {result_id}", artifact_id=registry.get("id"))
                        continue
                    actual, reported, absolute_tolerance, relative_tolerance = map(float, values)
                    allowed = absolute_tolerance + relative_tolerance * abs(actual)
                    if abs(reported - actual) > allowed:
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_OUT_OF_TOLERANCE", f"{claim.get('id')} exceeds its declared tolerance", artifact_id=registry.get("id"))
                    else:
                        self.add("G5", "PASS", "NUMERIC_ASSERTION_MATCH", f"{claim.get('id')} matches {result_id}/{metric_ref}", artifact_id=registry.get("id"))

                if claim.get("publication_status") != "final":
                    continue
                eligible_evidence = [result_id for result_id in evidence_result_ids if self.result_eligibility.get(result_id)]
                has_proof = isinstance(claim.get("proof_artifact"), dict)
                if not eligible_evidence and not (claim.get("claim_type") == "theoretical" and has_proof):
                    self.add("G5", "BLOCK", "FINAL_CLAIM_WITHOUT_ELIGIBLE_RESULT", f"{claim.get('id')} lacks a successful, current, acceptance-passing result", artifact_id=registry.get("id"))
                for result_id in evidence_result_ids:
                    if not self.result_eligibility.get(result_id):
                        self.add("G5", "BLOCK", "FINAL_CLAIM_INELIGIBLE_RESULT", f"{claim.get('id')} cites ineligible {result_id}", artifact_id=registry.get("id"))
                if claim.get("human_review", {}).get("status") != "PASS":
                    self.add("G5", "BLOCK", "FINAL_CLAIM_NOT_HUMAN_REVIEWED", f"{claim.get('id')} lacks a PASS human review", artifact_id=registry.get("id"))
                if claim.get("epistemic_status") == "formally_proved" and not has_proof:
                    self.add("G5", "BLOCK", "PROOF_ARTIFACT_MISSING", f"{claim.get('id')} declares formal proof without a proof file", artifact_id=registry.get("id"))

    def validate_question_evidence_paths(
        self,
        problems: list[dict[str, Any]],
        models: list[dict[str, Any]],
        experiments: list[dict[str, Any]],
        results: list[dict[str, Any]],
        claims_docs: list[dict[str, Any]],
    ) -> None:
        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        final_claim_results: list[set[str]] = []
        for registry in claims_docs:
            for claim in registry.get("claims", []):
                if claim.get("publication_status") == "final":
                    final_claim_results.append(
                        {
                            item.get("ref")
                            for item in claim.get("evidence_refs", [])
                            if self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
                        }
                    )
        for problem in problems:
            for question in problem.get("questions", []):
                question_id = question.get("id")
                model_ids = {model.get("id") for model in models if question_id in model.get("addresses", [])}
                experiment_ids = {
                    experiment.get("id")
                    for experiment in experiments
                    if experiment.get("model_ref") in model_ids and question_id in experiment.get("question_refs", [])
                }
                eligible_results = {
                    result.get("id")
                    for result in results
                    if result.get("experiment_ref") in experiment_ids and self.result_eligibility.get(result.get("id"))
                }
                if eligible_results and any(eligible_results.intersection(evidence) for evidence in final_claim_results):
                    self.add("G5", "PASS", "QUESTION_EVIDENCE_PATH_COMPLETE", f"{question_id} reaches a final claim through model, experiment and eligible result", artifact_id=problem.get("id"))
                else:
                    self.add("G5", "BLOCK", "QUESTION_EVIDENCE_PATH_MISSING", f"{question_id} lacks a complete question→model→experiment→eligible result→final claim path", artifact_id=problem.get("id"))

    def validate_reviews_and_profile(self) -> None:
        review_docs = [doc for doc in self.documents.values() if doc.get("kind") == "gate_review"]
        by_gate_and_time: dict[str, dict[datetime, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for document in review_docs:
            for review in document.get("reviews", []):
                gate = review.get("gate")
                if gate not in GATES:
                    continue
                try:
                    reviewed_at = parse_rfc3339(review.get("reviewed_at"))
                except ValueError as exc:
                    self.add(gate, "BLOCK", "REVIEW_TIME_INVALID", str(exc), artifact_id=document.get("id"))
                    continue
                by_gate_and_time[gate][reviewed_at].append(review)

        latest: dict[str, dict[str, Any]] = {}
        for gate, reviews_by_time in by_gate_and_time.items():
            latest_time = max(reviews_by_time)
            tied = reviews_by_time[latest_time]
            canonical = {json.dumps(review, sort_keys=True, ensure_ascii=False) for review in tied}
            if len(canonical) > 1:
                self.add(
                    gate,
                    "BLOCK",
                    "AMBIGUOUS_LATEST_REVIEW",
                    f"{len(tied)} different reviews share the same latest absolute timestamp {latest_time.isoformat()}",
                )
                # Do not let array order choose a PASS over a same-time BLOCK.
                continue
            latest[gate] = tied[0]

        for gate, review in latest.items():
            decision = review.get("decision")
            if decision in VALIDATION_STATUSES:
                self.add(gate, decision, "HUMAN_REVIEW", review.get("rationale", "human review recorded"))
            evidence_refs = set(review.get("evidence_refs", []))
            fingerprints = review.get("artifact_fingerprints", {})
            fingerprint_ids = set(fingerprints)
            if not fingerprint_ids.issubset(evidence_refs):
                self.add(
                    gate,
                    "BLOCK",
                    "REVIEW_FINGERPRINT_NOT_EVIDENCE",
                    f"fingerprinted IDs are not all cited as evidence: {sorted(fingerprint_ids.difference(evidence_refs))}",
                )
            for artifact_id, fingerprint in fingerprints.items():
                current = self.current_hashes.get(artifact_id)
                if current is None or current != fingerprint:
                    self.add(gate, "STALE", "REVIEW_FINGERPRINT_STALE", f"review no longer matches {artifact_id}")

        release_mode = self.manifest and self.manifest.get("manifest_type") == "release"
        # Draft projects surface only the four highest-risk pending reviews as
        # warnings.  A release is stricter: every G0-G7 transition requires a
        # current explicit PASS, matching the published workflow state machine.
        required_review_gates = GATES if release_mode else ("G2", "G4", "G5", "G7")
        for gate in required_review_gates:
            if gate not in latest:
                self.add(
                    gate,
                    "BLOCK" if release_mode else "WARN",
                    "HUMAN_REVIEW_MISSING",
                    "release requires an explicit current human/hybrid review" if release_mode else "human review is pending before release",
                )
                continue

            review = latest[gate]
            # A critical scientific gate is affirmative: NOT_APPLICABLE or a
            # warning cannot be used to bypass model/result/final review in a
            # release manifest.  BLOCK/ENV_BLOCK/STALE already dominate, but
            # this explicit finding also makes the release requirement clear.
            if release_mode and review.get("decision") != "PASS":
                self.add(gate, "BLOCK", "CRITICAL_REVIEW_NOT_PASS", "release requires the latest critical review decision to be PASS")
            if review.get("decision") == "PASS" and (
                not review.get("evidence_refs") or not review.get("artifact_fingerprints")
            ):
                self.add(
                    gate,
                    "BLOCK" if release_mode else "WARN",
                    "PASS_REVIEW_UNBOUND",
                    "PASS review must cite evidence and bind artifact fingerprints",
                )
            if release_mode and review.get("decision") == "PASS":
                required_bindings = self.required_review_bindings(gate)
                fingerprint_ids = set(review.get("artifact_fingerprints", {}))
                evidence_refs = set(review.get("evidence_refs", []))
                missing = required_bindings.difference(fingerprint_ids.intersection(evidence_refs))
                if missing:
                    self.add(
                        gate,
                        "BLOCK",
                        "GATE_REVIEW_REQUIRED_BINDING_MISSING",
                        f"{gate} PASS review lacks current gate-specific evidence bindings: {sorted(missing)}",
                    )

        profile = self.manifest.get("competition_profile", {}) if self.manifest else {}
        if not profile.get("enabled"):
            self.add("G6", "NOT_APPLICABLE", "FORMAT_PROFILE_DISABLED", "paper format profile is optional and not enabled")
        else:
            profile_path = profile.get("path")
            try:
                resolved = safe_project_path(self.root, profile_path, must_exist=True)
                self.add("G6", "PASS", "FORMAT_PROFILE_PRESENT", f"configured profile exists: {self._display_path(resolved)}")
            except (TypeError, ValueError, FileNotFoundError) as exc:
                self.add("G6", "BLOCK", "FORMAT_PROFILE_INVALID", str(exc))

    def required_review_bindings(self, gate: str) -> set[str]:
        """Return release evidence IDs that a PASS review must bind."""

        artifacts_of_kind = lambda *kinds: {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") in kinds
        }
        if gate in {"G0", "G1"}:
            return artifacts_of_kind("problem_spec")
        if gate == "G2":
            return artifacts_of_kind("model_spec")
        if gate == "G3":
            environment_ids = {item.get("id") for item in (self.manifest or {}).get("environment_files", [])}
            return artifacts_of_kind("experiment").union(environment_ids)
        if gate == "G4":
            return {result_id for result_id, eligible in self.result_eligibility.items() if eligible}
        if gate == "G5":
            return artifacts_of_kind("claims", "figures")
        if gate == "G6":
            paper_path = (self.manifest or {}).get("entrypoints", {}).get("paper")
            bindings = {"entrypoint:paper"} if paper_path else set()
            bindings.update(
                item.get("id")
                for item in (self.manifest or {}).get("deliverables", [])
                if item.get("required") and (
                    item.get("path") == paper_path or str(item.get("path", "")).lower().endswith(".pdf")
                )
            )
            return bindings
        if gate == "G7":
            return {
                item.get("id")
                for item in (self.manifest or {}).get("deliverables", [])
                if item.get("required")
            }
        return set()

    def validate_release_deliverables(self) -> None:
        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        deliverables = self.manifest.get("deliverables", [])
        if not deliverables:
            self.add("G7", "BLOCK", "RELEASE_WITHOUT_DELIVERABLES", "release manifest must declare final deliverables")

        entrypoints = self.manifest.get("entrypoints", {})
        paper_relative = entrypoints.get("paper")
        run_relative = entrypoints.get("run")
        if not isinstance(paper_relative, str) or Path(paper_relative).suffix.lower() not in {".tex", ".typ"}:
            self.add("G6", "BLOCK", "RELEASE_PAPER_ENTRYPOINT_INVALID", "release entrypoints.paper must be a .tex or .typ source")
            return
        matching_paper_deliverables = [
            item
            for item in deliverables
            if item.get("required") and item.get("path") == paper_relative
        ]
        if not matching_paper_deliverables:
            self.add("G7", "BLOCK", "PAPER_ENTRYPOINT_NOT_HASHED_DELIVERABLE", "entrypoints.paper must be the exact path of a required hashed deliverable", path=paper_relative)

        registered_run_paths = {
            document.get("algorithm", {}).get("entrypoint")
            for document in self.documents.values()
            if document.get("kind") == "model_spec"
        }
        registered_run_paths.update(
            file_ref.get("path")
            for document in self.documents.values()
            if document.get("kind") == "experiment"
            for file_ref in document.get("code_files", [])
        )
        if not isinstance(run_relative, str) or run_relative not in registered_run_paths:
            self.add("G7", "BLOCK", "RUN_ENTRYPOINT_UNREGISTERED", "release entrypoints.run must exactly match a selected model/experiment code entrypoint", path=run_relative)
        else:
            self.add("G7", "PASS", "RUN_ENTRYPOINT_REGISTERED", "release run entrypoint matches registered model/experiment code", path=run_relative)

        try:
            from lint_paper import PaperLint
        except ImportError as exc:
            self.add("G6", "ENV_BLOCK", "PAPER_LINT_UNAVAILABLE", str(exc), path=paper_relative)
            return

        engine = "latex" if paper_relative.lower().endswith(".tex") else "typst"
        lint = PaperLint(self.root, engine)
        lint.load_source_tree(paper_relative)
        lint.lint_text()
        lint.lint_claim_markers("claims/claims.yaml")
        lint.lint_figure_registry("figures/figures.yaml")

        pdf_paths = {
            item.get("path")
            for item in deliverables
            if item.get("required") and str(item.get("path", "")).lower().endswith(".pdf")
        }
        if isinstance(entrypoints.get("pdf"), str):
            pdf_paths.add(entrypoints["pdf"])
        for pdf_relative in sorted(path for path in pdf_paths if isinstance(path, str)):
            lint.lint_pdf(pdf_relative, None)

        lint_report = lint.report(strict=True)
        for finding in lint_report.get("findings", []):
            self.add(
                "G6",
                finding.get("status", "BLOCK"),
                f"PAPER_{finding.get('code', 'LINT_FINDING')}",
                finding.get("message", "paper lint finding"),
                path=finding.get("path"),
            )

    def enforce_release_gate_passes(self) -> None:
        """Require every computed gate to be exactly PASS for a release.

        This is evaluated after automatic checks, current fingerprint-bound
        reviews, the optional format profile, and deliverable checks.  A
        disabled format profile remains legitimate during drafting; for a
        release, G6 still needs another current PASS basis such as an explicit
        paper review or lint result recorded in the gate log.
        """

        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        # Snapshot every gate before adding any release-summary finding.  If
        # we recomputed G7 inside the loop, a BLOCK appended for (say) G5 would
        # make G7 become BLOCK and spuriously generate a second, self-induced
        # "G7 is not PASS" finding.  The snapshot preserves the actual gate
        # states that existed when release readiness was evaluated.
        gate_snapshot = {
            gate: aggregate_status(
                [finding["status"] for finding in self.findings if finding["gate"] == gate]
            )
            for gate in GATES
        }
        for gate, current in gate_snapshot.items():
            if current != "PASS":
                self.add(
                    "G7",
                    "BLOCK",
                    "RELEASE_GATE_NOT_PASS",
                    f"release requires {gate}=PASS, current status is {current}",
                )

    def report(self) -> dict[str, Any]:
        gate_reports: list[dict[str, Any]] = []
        for gate in GATES:
            gate_findings = [finding for finding in self.findings if finding["gate"] == gate]
            gate_status = aggregate_status([finding["status"] for finding in gate_findings])
            gate_reports.append({"gate": gate, "status": gate_status, "findings": gate_findings})
        overall = aggregate_status([item["status"] for item in gate_reports])
        return {
            "status": overall,
            # Persist a portable logical root. Absolute host paths can expose
            # usernames or workstation layouts when reports enter a release.
            "project_root": ".",
            "gates": gate_reports,
            "disclaimer": (
                "PASS means the declared evidence structure and registered bytes passed these checks; "
                "it is not proof of mathematical correctness, model suitability, or truth of conclusions."
            ),
        }


def iter_file_refs(value: Any, location: str = "<root>") -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield nested mappings that explicitly contain both path and SHA-256."""

    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield location, value
        for key, child in value.items():
            yield from iter_file_refs(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_file_refs(child, f"{location}/{index}")


def iter_defined_ids(value: Any, location: str = "<root>") -> Iterable[tuple[str, str]]:
    """Collect IDs only from definition fields, not from reference fields."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}/{key}"
            if key in {"id", "run_id"} and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
            yield from iter_defined_ids(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_defined_ids(child, f"{location}/{index}")


REFERENCE_LIST_KEYS = {
    "depends_on",
    "source_refs",
    "required_outputs",
    "addresses",
    "question_refs",
    "assumption_refs",
    "fallback_models",
    "data_refs",
    "baseline_refs",
    "source_result_refs",
    "claim_refs",
    "evidence_refs",
}


def expected_reference_kinds(source_kind: str | None, location: str) -> set[str] | None:
    """Return explicit target kinds for maintained core reference fields.

    Unknown fields return ``None`` and therefore receive existence checking
    only.  Extensions are skipped entirely by ``iter_references`` so a future
    namespaced extension is not rejected merely because the core auditor does
    not yet know its type system.
    """

    if location.endswith("/problem_ref"):
        return {"problem_spec"}
    if location.endswith("/model_ref") or "/baseline_refs/" in location or "/fallback_models/" in location:
        return {"model_spec"}
    if location.endswith("/experiment_ref"):
        return {"experiment"}
    if location.endswith("/symbol_ref"):
        return {"model_spec"}
    if location.endswith("/data_ref") or "/data_refs/" in location:
        return {"problem_spec"}
    if location.endswith("/metric_ref") or location.endswith("/output_ref"):
        return {"experiment"}
    if "/question_refs/" in location or "/addresses/" in location or "/required_outputs/" in location:
        return {"problem_spec"}
    if "/assumption_refs/" in location:
        return {"problem_spec"}
    if "/source_result_refs/" in location:
        return {"results"}
    if "/claim_refs/" in location:
        return {"claims"}
    if source_kind == "claims" and ("/evidence_refs/" in location or "/counterevidence/" in location):
        return {"results", "model_spec"}
    if "/depends_on/" in location:
        return {
            "model_spec": {"problem_spec", "model_spec"},
            "experiment": {"model_spec", "problem_spec"},
            "results": {"experiment"},
            "claims": {"results", "model_spec"},
            "figures": {"results", "claims"},
            "gate_review": set(SCHEMA_BY_KIND).difference({"manifest"}),
        }.get(source_kind)
    return None


RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def parse_rfc3339(value: Any) -> datetime:
    """Parse an RFC3339 timestamp and normalize it to absolute UTC time."""

    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise ValueError(f"invalid or timezone-free RFC3339 timestamp: {value!r}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid RFC3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp has no absolute timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def iter_references(value: Any, location: str = "<root>") -> Iterable[tuple[str, str]]:
    """Collect typed references using field semantics rather than every string."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}/{key}"
            if key == "extensions":
                continue
            if key.endswith("_ref") and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
                continue
            elif key in REFERENCE_LIST_KEYS and isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, str) and TYPED_ID_RE.fullmatch(item):
                        yield f"{child_location}/{index}", item
                    elif isinstance(item, dict) and isinstance(item.get("ref"), str):
                        yield f"{child_location}/{index}/ref", item["ref"]
                continue
            elif key == "ref" and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
                continue
            elif key in {"fingerprints", "artifact_fingerprints"} and isinstance(child, dict):
                for reference in child:
                    if TYPED_ID_RE.fullmatch(reference):
                        yield f"{child_location}/{reference}", reference
                continue
            yield from iter_references(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_references(child, f"{location}/{index}")


def exit_code(status: str) -> int:
    return {
        "BLOCK": 10,
        "ENV_BLOCK": 11,
        "STALE": 12,
        "PASS": 0,
        "WARN": 0,
        "NOT_APPLICABLE": 0,
    }[status]


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    audit = Audit(root, args.schema_root)
    internal_error = False
    try:
        if not root.is_dir():
            # The caller already knows the supplied argument. Keep structured
            # reports portable instead of embedding a workstation path.
            audit.add("G0", "BLOCK", "PROJECT_ROOT_MISSING", "project directory not found")
        elif audit.load_schemas() and audit.load_manifest():
            audit.load_artifacts()
            audit.validate_manifest_files()
            audit.verify_embedded_files()
            audit.validate_ids_and_refs()
            audit.validate_scientific_invariants()
            # Run dependency propagation only after scientific checks have
            # added every stale root, keeping the report idempotent.
            audit.validate_dag_and_propagate_stale()
            audit.validate_release_deliverables()
            audit.validate_reviews_and_profile()
            audit.enforce_release_gate_passes()
    except Exception as exc:  # Last-resort structured failure, not a traceback.
        internal_error = True
        audit.add("G0", "ENV_BLOCK", "AUDIT_INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")

    report = audit.report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report is not None:
        try:
            write_text_exclusive(args.json_report.resolve(), rendered + "\n")
        except FileExistsError:
            print(json.dumps({"status": "BLOCK", "message": f"report already exists: {args.json_report}"}, ensure_ascii=False))
            return 13
        except Exception as exc:
            print(json.dumps({"status": "BLOCK", "message": f"cannot create report: {exc}"}, ensure_ascii=False))
            return 13
    print(rendered)
    if internal_error:
        return 14
    return exit_code(report["status"])


if __name__ == "__main__":
    sys.exit(main())
