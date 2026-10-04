# Authenticated and confidential records

**Status:** DIRECTION, untested (opened 2026-10-04 with the owner). Nothing is adopted.

This entry covers two separate properties. The first closes a known gap.

## Authenticity: who wrote this, and were they allowed to?

Today, forgery and authority are an honor system ([identity-in-records](identity-in-records.md), "What
this does not do"). Names make attribution checkable, but they do not authenticate.

- **Signed control records** would let a worker refuse a stop or a subscription from an unauthorized
  observer.
- **Signed lifecycle records** (claims, heartbeats, `stopped`) would make a forged claim or a forged verdict
  *detectable*.
- **Signatures cover content, not position.** A record is signed before it is appended, when its seq is not
  yet known.
- **No round trips.** Verification is local.

## Confidentiality: who may read this?

**Ciphertext in the open log fits the substrate exactly,** because the substrate never parses bodies.

- **Structure stays plaintext.** Anything a fold reads to decide liveness, verdicts or answers must stay
  readable. That covers `claim_seq`, `honored`, `commits`, `parent`, and the envelope's names and request ids. Otherwise a third
  party could neither age a run nor compute its verdict.
- **Data is encrypted per audience:** the `value` bodies, including those that answer subscriptions.
- **With several observers,** each record is encrypted with a fresh key, which is then wrapped for each
  recipient, as age and PGP do. Group key agreement (MLS, RFC 9420) is needed only if membership changes
  and revocation matter.
- **A private channel inside the open log.** A shipped program encrypted for the worker, with its answers
  encrypted for the requester ([programmable-subscriptions](programmable-subscriptions.md)).
- **What ciphertext cannot hide.** Who talks, when, record sizes and topics all stay visible. Where that
  matters, use separate logs with storage-level access control. Each log then keeps its own claim CAS, and
  references between logs are by name.

## Order

Authenticity first, as signed control records, since it closes a known gap. Then confidentiality, as an
opt-in convention on the value plane.
