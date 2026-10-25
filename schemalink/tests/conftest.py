"""Shared fixtures for schemalink tests."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def tmp_db(tmp_path):
    """Create a small test sqlite database."""
    db = tmp_path / "test.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE singer (
            singer_id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER
        );
        CREATE TABLE concert (
            concert_id INTEGER PRIMARY KEY,
            singer_id INTEGER,
            title TEXT,
            FOREIGN KEY (singer_id) REFERENCES singer(singer_id)
        );
        INSERT INTO singer VALUES (1, 'Alice', 30), (2, 'Bob', 25), (3, 'Carol', 35);
        INSERT INTO concert VALUES (10, 1, 'Jazz Night'), (11, 2, 'Rock Fest'), (12, 1, 'Pop Show');
    """)
    conn.commit()
    conn.close()
    return str(db)
