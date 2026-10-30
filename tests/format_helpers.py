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
