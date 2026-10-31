"""What the format tests share beyond the fixtures: the toy formats several test modules
register, each at its own address with a real format's tables."""

from runstate.formats import DirectoryLayout, v0_2_0


def toy_layout(version):
    """A test-only format at ``version``'s address, with 0.2.0's tables: a format
    states its tables, and a toy has no reason to differ from the oldest real one."""
    return DirectoryLayout(
        version=version,
        sqlite_ddl=v0_2_0.SQLITE_SCRIPT,
        pg_ddl=v0_2_0.PG_STATEMENTS,
    )


def provision_pg(dsn, layout):
    """``layout``'s schema on the database at ``dsn``, with its tables, as a format
    no channel writes needs: a channel's birth creates only the current format's."""
    import psycopg
    from psycopg import sql

    schema = sql.Identifier(layout.pg_schema())
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
        with c.transaction():
            c.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
            for ddl in layout.pg_ddl:
                c.execute(ddl)
