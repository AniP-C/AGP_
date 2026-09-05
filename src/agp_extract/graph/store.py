"""Persist the knowledge graph as JSON and SQLite — deliberately NOT a graph DB.

A lightweight, dependency-free store keeps the POC portable and reviewable while
fully supporting the structural queries the evidence layer needs. Swapping in a
real graph DB later is a store change, not a schema change.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..schemas import DocumentGraph


def save_json(graph: DocumentGraph, path: str | Path) -> None:
    Path(path).write_text(graph.model_dump_json(indent=2), encoding="utf-8")


def load_json(path: str | Path) -> DocumentGraph:
    return DocumentGraph.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_sqlite(graph: DocumentGraph, path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        p.unlink()
    con = sqlite3.connect(p)
    try:
        con.executescript(
            """
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, kind TEXT, node_type TEXT, label TEXT, data TEXT
            );
            CREATE TABLE edges (
                src TEXT, dst TEXT, type TEXT, data TEXT
            );
            CREATE INDEX idx_edges_src ON edges(src, type);
            CREATE INDEX idx_edges_dst ON edges(dst, type);
            CREATE INDEX idx_nodes_type ON nodes(node_type);
            """
        )
        con.executemany(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?)",
            [(n.id, n.kind.value, n.node_type, n.label, json.dumps(n.data))
             for n in graph.nodes],
        )
        con.executemany(
            "INSERT INTO edges VALUES (?,?,?,?)",
            [(e.src, e.dst, e.type.value, json.dumps(e.data)) for e in graph.edges],
        )
        con.commit()
    finally:
        con.close()
