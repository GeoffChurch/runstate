"""Test helpers the staged 0.4.0 tests (``tests/test_next_*.py``) share."""

from runstate._next.claims import CAS_ATTEMPTS
from runstate.channel import Channel


class Wrapping(Channel):
    """A channel handle that passes every operation to ``inner``: the base of a
    test channel that changes one."""

    def __init__(self, inner):
        self._inner = inner

    def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
        return self._inner.send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )

    def read(self, after=0, *, topics=None, name=None, request_ids=None, limit=None):
        return self._inner.read(
            after, topics=topics, name=name, request_ids=request_ids, limit=limit
        )

    def latest(self, topic, name=None):
        return self._inner.latest(topic, name)

    def last_seq(self):
        return self._inner.last_seq()

    def close(self):
        self._inner.close()


class Interloping(Wrapping):
    """A channel handle on which, once ``arm``ed, a record lands through ``other``,
    another handle on the same run (or ``inner`` itself), just before the next
    compare-and-append: that CAS loses, and its writer must read again.
    ``times=None`` arms it for every CAS, a writer that always outruns its reader.
    ``cas`` counts the compare-and-appends tried; past ``10 * CAS_ATTEMPTS`` it
    raises, so an unbounded CAS loop fails rather than hangs."""

    def __init__(self, inner, other):
        super().__init__(inner)
        self._other, self._armed, self._times = other, None, 0
        self.fired = False
        self.cas = 0

    def arm(self, body, *, topic, name=None, request_id=None, times=1):
        self._armed = (body, topic, name, request_id)
        self._times = times

    def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
        if expected_seq is not None:
            self.cas += 1
            if self.cas > 10 * CAS_ATTEMPTS:
                raise AssertionError("an unbounded CAS loop")
            if self._armed is not None:
                armed, armed_topic, armed_name, armed_id = self._armed
                if self._times is not None:
                    self._times -= 1
                    if self._times == 0:
                        self._armed = None
                self._other.send(
                    armed, topic=armed_topic, name=armed_name, request_id=armed_id
                )
                self.fired = True
        return super().send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )
