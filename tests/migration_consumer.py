"""A consumer's own objects, as ``runstate migrate`` names them on its command line
(``--assume tests.migration_consumer:SETTLED``, ``--resolve
a=tests.migration_consumer:ASKED``). They live in a module of their own so that the
command line's import and the tests' share one copy."""


class ToyFacts:
    """A toy step's facts: whether its writers settle every fork."""

    def __init__(self, settled):
        self.settled = settled


SETTLED = ToyFacts(True)
UNSETTLED = ToyFacts(False)


class Asked:
    """A chooser that keeps what it was asked, and reads each fork as
    ``asked-<seq>``."""

    def __init__(self):
        self.questions = []

    def choose(self, question):
        self.questions.append(question)
        return {s: f"asked-{s}" for s in question.forks}


class Garbled:
    """A chooser that reads none of its forks."""

    def choose(self, question):
        return {}


ASKED = Asked()
GARBLED = Garbled()
NOT_A_CHOOSER = object()


class EarlierClaim:
    """``record-claim``: each fork speaks for the claim before the latest one before
    it, a displaced worker's (reference-by-name.md §6, H2)."""

    def choose(self, question):
        starts = [r.seq for r in question.rows if r.topic == "lifecycle.started"]
        return {s: [c for c in starts if c < s][-2] for s in question.sites}


class NoClaim:
    """``record-claim``: names a claim the log does not hold."""

    def choose(self, question):
        return {s: 999 for s in question.sites}


EARLIER_CLAIM = EarlierClaim()
NO_CLAIM = NoClaim()


class BeatsEarlier:
    """``record-claim``: a fork that is a heartbeat speaks for the claim before the
    latest one; a ``stopped`` for the latest."""

    def choose(self, question):
        starts = [r.seq for r in question.rows if r.topic == "lifecycle.started"]
        topic = {r.seq: r.topic for r in question.rows}
        return {
            s: [c for c in starts if c < s][
                -2 if topic[s] == "lifecycle.heartbeat" else -1
            ]
            for s in question.sites
        }


class AllStops:
    """``stop-honored``: every stop before the ``stopped``, the previous one's
    included, which the log excludes."""

    def choose(self, question):
        stops = [r.seq for r in question.rows if r.topic == "control.stop"]
        return {s: frozenset(x for x in stops if x < s) for s in question.sites}


class LatestClaim:
    """``lease-bound``: the latest claim, which the log excludes when another claim
    came first."""

    def choose(self, question):
        starts = [r.seq for r in question.rows if r.topic == "lifecycle.started"]
        return {s: starts[-1] for s in question.sites}


BEATS_EARLIER = BeatsEarlier()
ALL_STOPS = AllStops()
LATEST_CLAIM = LatestClaim()


class Raising:
    """A chooser with a bug."""

    def choose(self, question):
        raise RuntimeError("a consumer's bug")


class AskedElsewhere:
    """A chooser written for ``stop-honored``'s question, which reads ``claims``."""

    def choose(self, question):
        return {s: question.claims[s] for s in question.sites}


RAISING = Raising()
ASKED_ELSEWHERE = AskedElsewhere()


class FirstClaim:
    """``record-claim``: the run's first claim, which the log may exclude."""

    def choose(self, question):
        starts = [r.seq for r in question.rows if r.topic == "lifecycle.started"]
        return {s: starts[0] for s in question.sites}


class Listed:
    """A chooser that answers with a list."""

    def choose(self, question):
        return list(question.sites)


FIRST_CLAIM = FirstClaim()
LISTED = Listed()
