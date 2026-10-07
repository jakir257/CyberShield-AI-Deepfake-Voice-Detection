"""
Fix the one migration state that cannot be migrated out of.

The project swapped in its own user model (cyber.User). Django allows that only
if cyber.0001_initial is applied BEFORE anything that depends on the user table
- admin, sessions, the rest. A database created before the swap has those
already applied, so inserting cyber.0001_initial underneath them is impossible
and `migrate` stops with:

    InconsistentMigrationHistory: Migration admin.0001_initial is applied
    before its dependency cyber.0001_initial

There is no forward fix. The old database has to be rebuilt.

That is only safe because of what this database holds: Django's own scaffolding
and nothing else. Every piece of real data in this project - uploads, SMS,
analysis results, LLM settings - lives in media/ as files. So this checks that
the database really is empty of user data, and rebuilds only then. If anything
is found, it refuses and says what to do, rather than deleting someone's users.

Exit codes:
    0  nothing to do, or safely rebuilt
    1  needs a rebuild but there is real data - a human should decide
"""

import os
import shutil
import sqlite3
import sys
import time


def table_count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except sqlite3.Error:
        return 0


def main():

    here = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(here, "db.sqlite3")

    if not os.path.exists(db):
        return 0                      # fresh start, migrate will handle it

    try:
        conn = sqlite3.connect(db)
    except sqlite3.Error:
        return 0                      # not readable as SQLite; leave it alone

    try:
        applied = {
            (row[0], row[1])
            for row in conn.execute("SELECT app, name FROM django_migrations")
        }
    except sqlite3.Error:
        conn.close()
        return 0                      # no migration table yet

    has_cyber = any(app == "cyber" for app, _ in applied)
    has_dependents = any(app in ("admin", "sessions") for app, _ in applied)

    if has_cyber or not has_dependents:
        conn.close()
        return 0                      # already consistent

    # This database predates the custom user model. Is anything in it?
    users = table_count(conn, "auth_user")
    sessions = table_count(conn, "django_session")
    logs = table_count(conn, "django_admin_log")
    conn.close()

    print("  This database was created before the project had its own user")
    print("  model, so Django cannot migrate it forward.")

    if users or logs:
        print()
        print("  It contains %d user(s) and %d admin log entries, so it is NOT"
              % (users, logs))
        print("  being touched. Move db.sqlite3 aside yourself and re-run, or")
        print("  export those users first - they cannot be migrated across.")
        return 1

    # nothing but empty scaffolding. Keep a copy anyway; disk is cheap and a
    # deletion nobody can undo is a bad trade for the two seconds it costs.
    backup = os.path.join(here, "db.sqlite3.replaced-%s" % time.strftime("%Y%m%d-%H%M%S"))

    try:
        shutil.copy2(db, backup)
        os.remove(db)
    except OSError as e:
        print()
        print("  Could not replace it: %s" % e)
        print("  If the dev server is running, stop it and try again.")
        return 1

    print("  It holds no users or sessions, so it has been rebuilt.")
    print("  The old file is kept as %s" % os.path.basename(backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
