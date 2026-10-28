"""What the migration tests share beyond the golden logs (``migration_goldens.py``), by
public names, so that no test module imports another's private builders."""

from runstate.migrations.ambiguity import History

NO_HISTORY = History((), ())
"""A run with no manifest entry and no trace in the store: a run born where it is, or
copied in without its entries."""
