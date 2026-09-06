"""Bounded public-corpus acquisition and evaluator-owned holdout manifests.

The fetcher never downloads a holdout. Catalogs are provenance, not evaluation
manifests: only manifests with SHA-256-pinned uncompressed CNFs are executable.
Everything here uses the Python standard library.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import html.parser
import json
import lzma
import os
from pathlib import Path
import re
import tempfile
import urllib.request


SCHEMA_VERSION = 1
MIB = 1024 * 1024
GBD = "https://benchmark-database.de"
RESULTS = {"sat", "unsat", "unknown"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal and symlinks escaping the data root."""
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError(f"unsafe corpus path: {relative!r}")
    resolved = (root / name).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"corpus path escapes its root: {relative!r}")
    return resolved


def load_manifest(path: str | Path, *, allow_unpinned: bool = False) -> dict:
    manifest = json.loads(Path(path).read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported corpus manifest schema_version")
    if manifest.get("split") not in {"development", "smoke", "validation", "final"}:
        raise ValueError("invalid corpus split")
    instances = manifest.get("instances")
    if not isinstance(instances, list) or not instances:
        raise ValueError("manifest must contain at least one instance")
    identifiers, paths, hashes = set(), set(), set()
    for item in instances:
        if not isinstance(item, dict):
            raise ValueError("instance must be an object")
        identity = item.get("id", "")
        if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
            raise ValueError("invalid instance id")
        if identity in identifiers:
            raise ValueError(f"duplicate instance id: {identity}")
        identifiers.add(identity)
        path_value = item.get("path", "")
        if not isinstance(path_value, str):
            raise ValueError("instance path must be a string")
        safe_path(Path("/corpus"), path_value)
        if path_value in paths:
            raise ValueError(f"duplicate instance path: {path_value}")
        paths.add(path_value)
        for field in ("family", "lineage"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"missing {field}: {identity}")
        if item.get("expected_result", "unknown") not in RESULTS:
            raise ValueError(f"invalid expected_result: {identity}")
        digest = item.get("sha256")
        if not digest and allow_unpinned:
            continue
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError(f"missing/invalid uncompressed SHA-256: {identity}")
        if digest in hashes:
            raise ValueError(f"duplicate CNF contents: {identity}")
        hashes.add(digest)
    return manifest


def _write_json(path: Path, document: dict) -> None:
    """Atomic generated-data output, never edits source or evaluators."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(document, output, indent=2, sort_keys=True)
        output.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _TableParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"}:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row:
            self.rows.append(self.row)

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def _request(url: str, method: str = "GET"):
    if not url.startswith("https://"):
        raise ValueError("corpus downloads require HTTPS")
    return urllib.request.urlopen(
        urllib.request.Request(url, method=method, headers={"User-Agent": "sat-solver-eval/1"}),
        timeout=60,
    )


def _metadata(year: int) -> list[dict]:
    url = f"{GBD}/?context=cnf&track=main_{year}"
    with _request(url) as response:
        payload = response.read(4 * MIB + 1)
    if len(payload) > 4 * MIB:
        raise ValueError("metadata response exceeds 4 MiB")
    parser = _TableParser()
    parser.feed(payload.decode("utf-8"))
    if not parser.rows or parser.rows[0][0] != "hash":
        raise ValueError("GBD table schema changed; do not silently change the corpus")
    rows = [dict(zip(parser.rows[0], row)) for row in parser.rows[1:]]
    if len(rows) != 400:
        raise ValueError(f"expected 400 main-track rows for {year}, found {len(rows)}")
    return rows


def _canonical_family(name: str) -> str:
    # A documented metadata typo, not a distinction between independent families.
    return name.strip().lower().replace("circuit-equialence-checking", "circuit-equivalence-checking")


def _lineage(family: str) -> str:
    # Preserve source families but join obvious aliases conservatively. This is
    # not a substitute for the evaluator curator's generator/source review.
    aliases = {"minimal-disagreement-parity": "minimum-disagreement-parity",
               "hardware-bmc": "hardware-model-checking"}
    return f"gbd-family:{aliases.get(family, family)}"


def create_catalog(output: Path, count: int = 300, seed: str = "20260906") -> dict:
    """Family-balanced public development candidates; no holdout downloads.

    Deterministically reserve approximately a third of named families by hash.
    They are not a ready hidden test: curator review of generator lineage is still
    required. 2026 main-track exact/isomorphic overlap is excluded from dev.
    """
    if count < 1:
        raise ValueError("catalog count must be positive")
    rows = _metadata(2024) + _metadata(2025)
    by_family: dict[str, list] = collections.defaultdict(list)
    reserved_hashes = {
        row[key]
        for row in rows if "main_2026" in row.get("track", "").split(",")
        for key in ("hash", "isohash", "isohash2") if row.get(key) not in {None, "", "empty"}
    }
    seen: set[str] = set()
    for row in sorted(rows, key=lambda row: row["hash"]):
        family = _canonical_family(row.get("family", ""))
        if family in {"", "empty", "unknown"} or "," in family:
            continue
        bucket = int(hashlib.sha256(f"{seed}:family:{family}".encode()).hexdigest(), 16) % 3
        if bucket == 0:
            continue
        identities = {row[key] for key in ("hash", "isohash", "isohash2") if row.get(key) not in {None, "", "empty"}}
        if identities & (seen | reserved_hashes):
            continue
        seen.update(identities)
        result = row.get("result", "unknown")
        if result not in RESULTS:
            result = "unknown"
        by_family[family].append({
            "id": f"gbd-{row['hash']}",
            "path": f"development/{row['hash']}.cnf",
            "family": family,
            "lineage": _lineage(family),
            "expected_result": result,
            "source": {
                "url": f"{GBD}/file/{row['hash']}",
                "metadata_url": f"{GBD}/?context=cnf&hash={row['hash']}",
                "gbd_hash": row["hash"],
                "isohash": row.get("isohash"),
                "isohash2": row.get("isohash2"),
                "author": row.get("author"),
                "gbd_family": row.get("family"),
                "filenames": row.get("filename", "").split(","),
                "tracks": row.get("track", "").split(","),
                "proceedings": row.get("proceedings"),
                "license": "Not asserted by GBD metadata; obtain from original submitter before redistribution.",
            },
        })
    for family, items in by_family.items():
        items.sort(key=lambda item: hashlib.sha256(f"{seed}:{item['id']}".encode()).hexdigest())
    families = sorted(by_family, key=lambda family: hashlib.sha256(f"{seed}:{family}".encode()).hexdigest())
    selected = []
    while len(selected) < count and any(by_family.values()):
        for family in families:
            if by_family[family] and len(selected) < count:
                selected.append(by_family[family].pop(0))
    if len(selected) != count:
        raise ValueError(f"only {len(selected)} eligible development candidates; requested {count}")
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "split": "development",
        "kind": "acquisition_catalog",
        "selection": {
            "seed": seed,
            "years": [2024, 2025],
            "rule": "Reserve family hash bucket 0/3; deduplicate hash/isohash/isohash2; exclude metadata-listed main_2026 overlap; round-robin remaining families.",
            "lineage_warning": "GBD family is a conservative proxy, not independently audited generator lineage. Holdout curators must check aliases and related generators.",
            "metadata_source": [f"{GBD}/?context=cnf&track=main_{year}" for year in (2024, 2025)],
        },
        "instances": selected,
    }
    _write_json(output, catalog)
    return catalog


def _download_cnf(item: dict, root: Path, network_budget: int, unpacked_budget: int) -> tuple[dict, int, int]:
    target = safe_path(root, item["path"])
    if target.exists():
        digest = sha256_file(target)
        if not item.get("sha256"):
            raise ValueError(f"unlocked existing file {target}; remove or use its locked manifest")
        if digest != item["sha256"]:
            raise ValueError(f"existing CNF hash mismatch: {target}")
        return dict(item), 0, 0
    url = item.get("source", {}).get("url", "")
    if not url.startswith(GBD + "/file/"):
        raise ValueError("development fetch accepts only the pinned GBD host")
    target.parent.mkdir(parents=True, exist_ok=True)
    network_size = unpacked_size = 0
    compressed_digest = hashlib.sha256()
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="sat-corpus-", dir=target.parent) as directory:
        archive = Path(directory) / "download.xz"
        temporary = Path(directory) / "input.cnf"
        with _request(url) as response, archive.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > network_budget:
                raise ValueError(f"download budget exceeded before fetching {item['id']}")
            for chunk in iter(lambda: response.read(min(MIB, network_budget - network_size + 1)), b""):
                network_size += len(chunk)
                if network_size > network_budget:
                    raise ValueError(f"download budget exceeded for {item['id']}")
                compressed_digest.update(chunk)
                output.write(chunk)
        if item.get("source", {}).get("archive_sha256") not in {None, compressed_digest.hexdigest()}:
            raise ValueError(f"compressed archive hash mismatch: {item['id']}")
        # LZMAFile supports concatenated XZ streams. Incremental reads cap expansion.
        with lzma.open(archive, "rb") as source, temporary.open("wb") as output:
            prefix = b""
            for chunk in iter(lambda: source.read(min(MIB, unpacked_budget - unpacked_size + 1)), b""):
                unpacked_size += len(chunk)
                if unpacked_size > unpacked_budget:
                    raise ValueError(f"uncompressed budget exceeded for {item['id']}")
                if len(prefix) < MIB:
                    prefix += chunk[:MIB - len(prefix)]
                digest.update(chunk)
                output.write(chunk)
        if not re.search(rb"(?m)^p\s+cnf\s+\d+\s+\d+\s*$", prefix):
            raise ValueError(f"not a DIMACS CNF with an early header: {item['id']}")
        if item.get("sha256") not in {None, digest.hexdigest()}:
            raise ValueError(f"uncompressed CNF hash mismatch: {item['id']}")
        result = dict(item)
        result["sha256"] = digest.hexdigest()
        result["bytes"] = unpacked_size
        result["source"] = dict(item["source"], archive_sha256=compressed_digest.hexdigest(), archive_bytes=network_size)
        os.replace(temporary, target)
    return result, network_size, unpacked_size


def fetch(manifest_path: Path, output: Path, output_manifest: Path, limit: int | None = None,
          max_download_mib: int = 512, max_unpacked_mib: int = 2048) -> dict:
    catalog = load_manifest(manifest_path, allow_unpinned=True)
    if catalog["split"] != "development":
        raise ValueError("fetch only acquires public development data; keep holdouts off the agent machine")
    if max_download_mib < 1 or max_unpacked_mib < 1 or (limit is not None and limit < 1):
        raise ValueError("download, unpacking, and count limits must be positive")
    if manifest_path.resolve() == output_manifest.resolve() and catalog.get("kind") == "acquisition_catalog":
        raise ValueError("do not overwrite the acquisition catalog; write a separate locked manifest")
    selected = catalog["instances"][:limit]
    if limit is not None and limit > len(catalog["instances"]):
        raise ValueError("requested limit exceeds the catalog size")
    prior = {}
    if output_manifest.exists():
        previous = load_manifest(output_manifest)
        if previous["split"] != "development":
            raise ValueError("cannot overwrite a holdout manifest")
        prior = {item["id"]: item for item in previous["instances"]}
    acquired = []
    network_size = unpacked_size = 0
    locked = {key: value for key, value in catalog.items() if key != "instances"}
    locked["kind"] = "locked_corpus"
    if catalog.get("kind") == "acquisition_catalog":
        locked["acquisition_catalog_sha256"] = sha256_file(manifest_path)
    for original in selected:
        item = original
        if item["id"] in prior:
            old = prior[item["id"]]
            if item.get("sha256") not in {None, old["sha256"]}:
                raise ValueError(f"locked SHA-256 changed for {item['id']}")
            for key in ("path", "family", "lineage"):
                if old.get(key) != item.get(key):
                    raise ValueError(f"manifest metadata changed for {item['id']}: {key}")
            if not item.get("sha256"):
                item = dict(item, sha256=old["sha256"], source={**item.get("source", {}), **old.get("source", {})})
                if "bytes" in old:
                    item["bytes"] = old["bytes"]
        result, used_network, used_unpacked = _download_cnf(
            item, output, max_download_mib * MIB - network_size,
            max_unpacked_mib * MIB - unpacked_size,
        )
        network_size += used_network
        unpacked_size += used_unpacked
        acquired.append(result)
        locked["instances"] = acquired
        # Checkpoint each successful download so interrupted fetches are resumable.
        _write_json(output_manifest, locked)
        print(f"[{len(acquired)}/{len(selected)}] {item['family']}: {item['id']}", flush=True)
    verify(output_manifest, output)
    return {"instances": len(acquired), "download_bytes": network_size,
            "unpacked_new_bytes": unpacked_size, "manifest": str(output_manifest)}


def verify(manifest_path: Path, root: Path) -> dict:
    manifest = load_manifest(manifest_path)
    total = 0
    for item in manifest["instances"]:
        path = safe_path(root, item["path"])
        if not path.is_file():
            raise ValueError(f"missing CNF: {path}")
        if sha256_file(path) != item["sha256"]:
            raise ValueError(f"CNF SHA-256 mismatch: {item['id']}")
        total += path.stat().st_size
    return {"instances": len(manifest["instances"]), "bytes": total,
            "families": len({item['family'] for item in manifest['instances']})}


def import_holdout(input_manifest: Path, output_manifest: Path, root: Path,
                   development_manifest: Path, *, repo_root: Path = REPO_ROOT) -> dict:
    """Curator-side only; refuse visible-workspace holdouts and lineage overlap."""
    for path in (input_manifest, output_manifest, root):
        if path.resolve().is_relative_to(repo_root.resolve()):
            raise ValueError("holdout files/manifests must be outside the agent repository (prefer a separate machine)")
    holdout = load_manifest(input_manifest, allow_unpinned=True)
    if holdout["split"] not in {"validation", "final"}:
        raise ValueError("holdout import requires split validation or final")
    development = load_manifest(development_manifest, allow_unpinned=True)
    if not holdout.get("curator_lineage_review"):
        raise ValueError("holdout requires curator_lineage_review documenting manual source/generator independence")
    exclusions = {field: {item[field] for item in development["instances"] if item.get(field)}
                  for field in ("family", "lineage", "sha256")}
    iso = {item.get("source", {}).get(key) for item in development["instances"]
           for key in ("gbd_hash", "isohash", "isohash2")}
    iso -= {None, "", "empty"}
    locked_instances = []
    seen = set()
    seen_iso = set()
    for original in holdout["instances"]:
        item = dict(original)
        if not item.get("source") or not item["source"].get("provenance"):
            raise ValueError(f"holdout requires source.provenance: {item['id']}")
        for field in ("family", "lineage"):
            if item[field] in exclusions[field]:
                raise ValueError(f"development/holdout {field} overlap: {item[field]}")
        identities = {item.get("source", {}).get(key) for key in ("gbd_hash", "isohash", "isohash2")}
        identities -= {None, "", "empty"}
        if identities & iso:
            raise ValueError(f"isomorphic development/holdout overlap: {item['id']}")
        if identities & seen_iso:
            raise ValueError(f"duplicate/isomorphic holdout instances: {item['id']}")
        seen_iso.update(identities)
        path = safe_path(root, item["path"])
        digest = sha256_file(path)
        if item.get("sha256") not in {None, digest}:
            raise ValueError(f"holdout hash mismatch: {item['id']}")
        if digest in exclusions["sha256"] or digest in seen:
            raise ValueError(f"duplicate holdout/development contents: {item['id']}")
        seen.add(digest)
        item.update(sha256=digest, bytes=path.stat().st_size)
        locked_instances.append(item)
    holdout["instances"] = locked_instances
    holdout["kind"] = "locked_corpus"
    holdout["development_manifest_sha256"] = sha256_file(development_manifest)
    _write_json(output_manifest, holdout)
    return verify(output_manifest, root)


def make_smoke(output: Path) -> dict:
    """Tiny exact-known examples: correctness infrastructure, never scored data."""
    examples = {
        "empty": ("p cnf 0 0\n", "sat"),
        "empty-clause": ("p cnf 0 1\n0\n", "unsat"),
        "unit-sat": ("p cnf 3 3\n1 0\n-1 2 0\n-2 3 0\n", "sat"),
        "unit-unsat": ("p cnf 1 2\n1 0\n-1 0\n", "unsat"),
        "xor-sat": ("p cnf 2 2\n1 2 0\n-1 -2 0\n", "sat"),
        "xor-unsat": ("p cnf 2 4\n1 2 0\n-1 2 0\n1 -2 0\n-1 -2 0\n", "unsat"),
    }
    output.mkdir(parents=True, exist_ok=True)
    instances = []
    for name, (text, result) in examples.items():
        path = output / f"{name}.cnf"
        if path.exists() and path.read_text() != text:
            raise ValueError(f"refusing to overwrite changed smoke input: {path}")
        path.write_text(text)
        instances.append({"id": name, "path": path.name, "sha256": sha256_file(path),
                          "family": "synthetic-smoke", "lineage": "synthetic-smoke-v1",
                          "expected_result": result, "source": {"provenance": "Hand-written exact-known smoke cases; not application benchmarks."}})
    manifest = {"schema_version": SCHEMA_VERSION, "split": "smoke", "kind": "locked_corpus", "instances": instances}
    _write_json(output / "manifest.json", manifest)
    return {"instances": len(instances), "manifest": str(output / "manifest.json")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("catalog", help="Curator preparation: refresh public catalog (do not run during optimization)")
    command.add_argument("--output", type=Path, default=Path("data/manifests/dev-catalog.json"))
    command.add_argument("--count", type=int, default=300)
    command.add_argument("--seed", default="20260906")
    command = sub.add_parser("fetch", help="Download only public development CNFs with cumulative size caps")
    command.add_argument("--manifest", type=Path, default=Path("data/manifests/dev.json"))
    command.add_argument("--output", type=Path, default=Path("data/corpus"))
    command.add_argument("--output-manifest", type=Path, default=Path("data/manifests/dev.json"))
    command.add_argument("--limit", type=int, default=None)
    command.add_argument("--max-download-mib", type=int, default=512)
    command.add_argument("--max-unpacked-mib", type=int, default=2048)
    command = sub.add_parser("verify")
    command.add_argument("--manifest", type=Path, default=Path("data/manifests/dev.json"))
    command.add_argument("--root", type=Path, default=Path("data/corpus"))
    command = sub.add_parser("smoke")
    command.add_argument("--output", type=Path, default=Path("data/smoke"))
    command = sub.add_parser("import", help="Run on curator/evaluation host, not the optimization agent host")
    command.add_argument("--input-manifest", required=True, type=Path)
    command.add_argument("--output-manifest", required=True, type=Path)
    command.add_argument("--root", required=True, type=Path)
    command.add_argument("--development-manifest", type=Path, default=Path("data/manifests/dev-catalog.json"))
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            result = create_catalog(args.output, args.count, args.seed)
            result = {"instances": len(result["instances"]), "catalog": str(args.output)}
        elif args.command == "fetch":
            result = fetch(args.manifest, args.output, args.output_manifest, args.limit, args.max_download_mib, args.max_unpacked_mib)
        elif args.command == "verify":
            result = verify(args.manifest, args.root)
        elif args.command == "smoke":
            result = make_smoke(args.output)
        else:
            result = import_holdout(args.input_manifest, args.output_manifest, args.root, args.development_manifest)
    except (ValueError, OSError, lzma.LZMAError) as error:
        parser.exit(1, f"corpus: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
