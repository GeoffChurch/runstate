"""Format 0.3.0: reference by name (docs/specs/reference-by-name.md). Its layout
is 0.2.0's, one directory and one schema further on, and its tables are 0.2.0's:
the format changed what the records say, not how they are stored."""

from . import v0_2_0
from ._layout import DirectoryLayout

LAYOUT = DirectoryLayout(
    version="0.3.0", sqlite_ddl=v0_2_0.SQLITE_SCRIPT, pg_ddl=v0_2_0.PG_STATEMENTS
)
