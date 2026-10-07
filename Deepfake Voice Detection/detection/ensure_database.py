"""
Create the PostgreSQL database if it is not there yet.

Called by run.bat before `migrate`. Django will happily create *tables* in a
database, but it will not create the database itself - so a first run against
a fresh Postgres fails with "database does not exist" and stops the launcher
dead. This is the missing step.

Uses psycopg, which is already installed for Django, rather than shelling out
to psql - psql is not always on PATH, and it prompts for a password on stdin
in a way a .bat cannot answer.

Exit codes, because run.bat branches on them:

    0  the database exists (created now, or already there)
    1  something unexpected went wrong
    2  could not connect - wrong password, or the server is not accepting
       (run.bat falls back to SQLite on this one)
"""

import os
import sys

try:
    import psycopg
except ImportError:
    print("  psycopg is not installed - skipping, the app will use SQLite.")
    sys.exit(2)


def main():

    name = os.environ.get("CYBERSHIELD_DB_NAME", "cybershield")
    user = os.environ.get("CYBERSHIELD_DB_USER", "postgres")
    password = os.environ.get("CYBERSHIELD_DB_PASSWORD", "")
    host = os.environ.get("CYBERSHIELD_DB_HOST", "localhost")
    port = os.environ.get("CYBERSHIELD_DB_PORT", "5432")

    if not password:
        print("  No password set - the app will use SQLite.")
        return 2

    # connect to the maintenance database; you cannot create a database from
    # inside the one you are creating
    try:
        conn = psycopg.connect(
            dbname="postgres", user=user, password=password,
            host=host, port=port, connect_timeout=8,
        )
    except psycopg.OperationalError as e:
        detail = str(e).strip().splitlines()[0] if str(e).strip() else e
        print("  Cannot connect to PostgreSQL: %s" % detail)
        return 2

    try:
        # CREATE DATABASE cannot run inside a transaction block
        conn.autocommit = True

        with conn.cursor() as cur:

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))

            if cur.fetchone():
                print('  Database "%s" is already there.' % name)
                return 0

            # the name comes from the environment, not from a request, but
            # quoting it costs one import and removes the question entirely
            cur.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(
                    psycopg.sql.Identifier(name)))

            print('  Created database "%s".' % name)
            return 0

    except psycopg.Error as e:
        print("  PostgreSQL refused to create it: %s" % e)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
