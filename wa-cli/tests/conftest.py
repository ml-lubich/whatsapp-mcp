"""Shared pytest fixtures for the wa-cli test suite.

This file is intentionally kept minimal at scaffold time. Section markers
below delimit ownership to avoid merge conflicts between work packages:

- WP4 (db.py) owns the "db schema fixtures" block.
- Fake-process fixtures for daemon tests live in tests/test_daemon.py
  instead of here, to keep this file single-writer (WP4) per the plan.
"""

# --- db schema fixtures ---
# (filled by WP4: sample_dbs(tmp_path) fixture building whatsapp.db / messages.db
#  from the exact CREATE TABLE statements in spec section 2.3)
