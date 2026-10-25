#!/usr/bin/env python3
"""Layered eval of the 考研 agent on data/gold/kaoyan_eval_v2.jsonl.

L0 (intent) needs nothing but the code. L1 (tools) needs a kaoyan DB; pass --db to
read another file, e.g. one built by seed_kaoyan.py + extract_kaoyan.py in CI.
L2 (retrieval) searches a 考研 index: --build-index makes a BM25 one from the
git-tracked source files (what CI has), --index reuses an existing one. No layer
calls an LLM; the hybrid backend calls the embedding model.

    python scripts/eval_kaoyan.py --layers L0,L1 --out docs/eval
    python scripts/eval_kaoyan.py --baseline docs/eval/baseline.json --max-drop 0.02
    python scripts/eval_kaoyan.py --layers L2 --build-index /tmp/kaoyan_bm25
    python scripts/eval_kaoyan.py --layers L2 --index data/chroma_kaoyan --backends bm25,hybrid
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GOLD = ROOT / "data" / "gold" / "kaoyan_eval_v2.jsonl"


def _commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def tracked_raw_files(raw: Path) -> list[Path]:
    from kaoyan.ingest.loaders import iter_source_files

    listed = subprocess.run(["git", "ls-files", "-z", "--", raw.relative_to(ROOT).as_posix()], cwd=ROOT,
                            capture_output=True, check=True).stdout.decode()
    tracked = {(ROOT / p).resolve() for p in listed.split("\0") if p}
    return [f for f in iter_source_files(raw) if f.resolve() in tracked]


def build_bm25_index(settings, out_dir: Path) -> dict:
    """BM25 index of the tracked source files with default chunking and no OCR, as CI builds it."""
    from kaoyan.config import Settings
    from kaoyan.index import ingest_kaoyan

    s = settings.model_copy(update={
        "kaoyan_chroma_dir": str(out_dir.resolve()),
        "embedding_base_url": "",
        "ocr_backend": "none",
        **{name: Settings.model_fields[name].default for name in ("chunk_size", "chunk_overlap")},
    })
    return ingest_kaoyan(s, paths=tracked_raw_files(s.kaoyan_data_path / "raw"), reindex=True)


def open_index(settings, index_dir: str, backend: str):
    """(search, indexed doc ids) over one index; bm25 ignores any dense vectors in it."""
    from kaoyan.rag.store import DocumentStore

    update = {"kaoyan_chroma_dir": index_dir}
    if backend == "bm25":
        update["embedding_base_url"] = ""
    s = settings.model_copy(update=update)
    store = DocumentStore(s)
    if backend == "hybrid":
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if not s.embedding_enabled:
            raise SystemExit("hybrid needs EMBEDDING_BASE_URL")
        client = chromadb.PersistentClient(path=str(s.chroma_path), settings=ChromaSettings(anonymized_telemetry=False))
        dense = client.get_collection(s.collection_name).count()
        if dense != store.chunk_count:
            raise SystemExit(f"hybrid needs a vector for every chunk: {dense} vectors, {store.chunk_count} chunks")
    indexed = {d["doc_id"] for d in store.list_documents() if d.get("doc_id")}
    return (lambda q, school, k: store.search(q, top_k=k, school=school)), indexed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=str(GOLD))
    parser.add_argument("--layers", default="L0,L1", help="comma list of L0, L1, L2")
    parser.add_argument("--split", default="",
                        help="comma list of splits; default every split except the frozen test split, "
                             "which is only run for milestone sign-off and reported as totals")
    parser.add_argument("--db", default="", help="kaoyan SQLite path for L1 (default: settings)")
    parser.add_argument("--index", default="", help="考研 index dir for L2 (default: settings)")
    parser.add_argument("--build-index", default="",
                        help="build a BM25 index of the git-tracked source files into this dir and use it for L2")
    parser.add_argument("--backends", default="bm25",
                        help="comma list of bm25, hybrid; bm25 reports as L2, hybrid as L2-hybrid")
    parser.add_argument("--out", default="", help="directory for <date>.md and <date>.json")
    parser.add_argument("--baseline", default="", help="report json to compare against")
    parser.add_argument("--max-drop", type=float, default=0.02)
    parser.add_argument("--write-baseline", default="",
                        help="write summaries and the known-failure list, for later --baseline")
    parser.add_argument("--accept-new-failures", action="store_true",
                        help="let --write-baseline add rows to the known-failure list")
    args = parser.parse_args()

    if args.db:
        os.environ["KAOYAN_DB"] = str(Path(args.db).resolve())

    from kaoyan.config import get_settings
    from kaoyan.eval.runner import (
        failure_changes,
        known_failures,
        load_rows,
        regressions,
        render_markdown,
        run_l0,
        run_l1,
    )

    layers = [x.strip().upper() for x in args.layers.split(",") if x.strip()]
    splits = {x.strip() for x in args.split.split(",") if x.strip()}
    gold = Path(args.gold)
    rows = load_rows(gold, splits or None)

    report: dict = {
        "meta": {
            "date": dt.date.today().isoformat(),
            "commit": _commit(),
            "gold": gold.relative_to(ROOT).as_posix() if gold.is_relative_to(ROOT) else str(gold),
            "splits": sorted(splits),
            "rows": len(rows),
        },
        "layers": {},
    }
    if "L0" in layers:
        report["layers"]["L0"] = run_l0(rows)
    if "L1" in layers:
        db = get_settings().kaoyan_db_path
        if not db.exists():
            print(f"L1 needs a kaoyan DB; {db} not found (run seed_kaoyan.py and extract_kaoyan.py)",
                  file=sys.stderr)
            return 2
        report["layers"]["L1"] = run_l1(rows)
    if "L2" in layers:
        from kaoyan.eval.retrieval import load_spec_sources, run_l2

        settings = get_settings()
        index_dir = args.index or settings.kaoyan_chroma_dir
        if args.build_index:
            built = build_bm25_index(settings, Path(args.build_index))
            print(f"indexed {built['docs_indexed']} docs, {built['chunks_total']} chunks into {args.build_index}")
            index_dir = str(Path(args.build_index).resolve())
        spec_sources = load_spec_sources(gold.parent)
        report["meta"]["backends"] = {}
        for backend in [x.strip() for x in args.backends.split(",") if x.strip()]:
            name = "L2" if backend == "bm25" else f"L2-{backend}"
            search, indexed = open_index(settings, index_dir, backend)
            report["layers"][name] = run_l2(rows, search, indexed, spec_sources)
            report["meta"]["backends"][name] = backend

    for name, layer in report["layers"].items():
        print(name, json.dumps(layer["summary"], ensure_ascii=False))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stem = out / report["meta"]["date"]
        stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
        stem.with_suffix(".md").write_text(render_markdown(report) + "\n", encoding="utf-8")
        print(f"wrote {stem}.md / .json")

    if args.write_baseline:
        path = Path(args.write_baseline)
        old_known = json.loads(path.read_text(encoding="utf-8")).get("known_failures", {}) if path.exists() else {}
        added, removed = failure_changes(report, old_known)
        if removed:
            print("dropped from the known-failure list (now pass):\n  " + "\n  ".join(removed))
        if added:
            print("new known failures:\n  " + "\n  ".join(added), file=sys.stderr)
            if not args.accept_new_failures:
                print("not written; pass --accept-new-failures and list them in the PR", file=sys.stderr)
                return 1
        summary = {**report, "layers": {k: {g: v[g] for g in v if g != "rows"}
                                        for k, v in report["layers"].items()},
                   "known_failures": known_failures(report)}
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.write_baseline}")

    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        problems = [f"metric drop {d}" for d in regressions(report, baseline, args.max_drop)]
        new, fixed = failure_changes(report, baseline.get("known_failures", {}))
        problems += [f"new failure {x}" for x in new]
        problems += [f"fixed but still listed {x} (rewrite the baseline to drop it)" for x in fixed]
        if problems:
            print("regression vs baseline:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
