"""Shared VQE pieces: the problem, the circuits, and the job backends.

The experiment: minimize the energy of the 2-qubit transverse-field Ising
model H = -J Z0Z1 - h (X0 + X1) over the 2-parameter ansatz

    |psi(t0, t1)> = CX(0,1) . (RY(t0) x RY(t1)) |00>

which contains the exact ground state, so a converged run should approach
``exact_ground(J, h)`` (= -sqrt(5) at J = h = 1). The landscape also has a
local minimum near E = -1 that traps some seeds -- honest physics; the
driver's plateau policy stops those runs there, and a different VQE_SEED is
a different run (new run_id, fresh log).

One energy evaluation = two measurement circuits (computational basis for
<Z0Z1>; X basis for <X0>, <X1>) folded by ``energy``. Jobs go through a tiny
submit / poll / counts / cancel seam with two implementations:

- ``LocalJobs`` -- qiskit's StatevectorSampler: credential-free and instant
  (the default, so anyone can run the example);
- ``IonQJobs`` -- IonQ Cloud via qiskit-ionq (``VQE_BACKEND=simulator`` or
  ``qpu.aria-1``, ...; needs $IONQ_API_KEY). One multi-circuit API job per
  SPSA iteration; ``poll`` is non-blocking on purpose -- the worker owns the
  wait so it can tick runstate between polls (see worker.py).

Config comes from VQE_* env vars so the driver and the worker subprocess
derive identical values (the launcher passes the environment through), and
``run_id`` hashes the physics identity -- everything but the iteration
budget, the extend axis -- so the same experiment always meets the same log
(docs/specs/run-id-recipe.md).
"""

import hashlib
import json
import os

try:
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.primitives import StatevectorSampler
    from qiskit.quantum_info import SparsePauliOp
except ImportError as exc:
    raise ImportError(
        "examples/vqe needs qiskit (pip install qiskit); the IonQ Cloud path "
        "additionally needs qiskit-ionq"
    ) from exc

ANSATZ = "ry-cx"  # part of the run identity: change the circuit, change the run


def config():
    """The experiment's knobs, from VQE_* env (defaults = the local demo)."""
    return {
        "j": float(os.environ.get("VQE_J", "1.0")),
        "h": float(os.environ.get("VQE_H", "1.0")),
        "shots": int(os.environ.get("VQE_SHOTS", "1024")),
        "backend": os.environ.get("VQE_BACKEND", "local"),
        "seed": int(os.environ.get("VQE_SEED", "11")),
        "budget": int(os.environ.get("VQE_BUDGET", "120")),
    }


def run_id(cfg):
    """Content-addressed identity: hash the inputs that change the physics,
    EXCLUDING the budget -- more iterations extend the same run rather than
    naming a new one (the extend axis, docs/specs/run-id-recipe.md)."""
    ident = {k: cfg[k] for k in ("j", "h", "shots", "backend", "seed")}
    ident["ansatz"] = ANSATZ
    canon = json.dumps(ident, sort_keys=True, allow_nan=False)
    return "vqe-" + hashlib.sha256(canon.encode()).hexdigest()[:12]


def circuits(params):
    """The two measurement circuits for one energy evaluation at ``params``."""
    t0, t1 = params
    prep = QuantumCircuit(2)
    prep.ry(t0, 0)
    prep.ry(t1, 1)
    prep.cx(0, 1)
    zz = prep.copy()
    zz.measure_all()  # computational basis -> <Z0Z1>
    xx = prep.copy()
    xx.h(0)
    xx.h(1)
    xx.measure_all()  # X basis -> <X0>, <X1>
    return [zz, xx]


def _expval(counts, eig):
    total = sum(counts.values())
    return sum(eig(key) * n for key, n in counts.items()) / total


def energy(zz_counts, xx_counts, *, j, h):
    """E = -J <Z0Z1> - h (<X0> + <X1>). Qiskit bitstrings are little-endian:
    ``key[-1]`` is qubit 0."""
    zz = _expval(zz_counts, lambda k: 1.0 if k[-1] == k[-2] else -1.0)
    x0 = _expval(xx_counts, lambda k: 1.0 if k[-1] == "0" else -1.0)
    x1 = _expval(xx_counts, lambda k: 1.0 if k[-2] == "0" else -1.0)
    return -j * zz - h * (x0 + x1)


def exact_ground(j, h):
    """The exact ground energy by diagonalizing the 4x4 Hamiltonian
    (-sqrt(5) at J = h = 1) -- the yardstick the driver prints against."""
    ham = SparsePauliOp.from_list([("ZZ", -j), ("XI", -h), ("IX", -h)])
    return float(min(np.linalg.eigvalsh(ham.to_matrix())))


class LocalJobs:
    """Credential-free jobs: qiskit's StatevectorSampler, evaluated eagerly at
    submit (local sampling is instant, so ``poll`` is terminal immediately and
    the worker's wait loop runs zero sleeps)."""

    def __init__(self, shots, seed):
        self._sampler = StatevectorSampler(default_shots=shots, seed=seed)

    def submit(self, circs):
        job = self._sampler.run([(c,) for c in circs])
        return [r.data.meas.get_counts() for r in job.result()]

    def poll(self, handle):
        return "DONE"

    def counts(self, handle):
        return handle

    def cancel(self, handle):
        pass


class IonQJobs:
    """IonQ Cloud jobs via qiskit-ionq: one multi-circuit API job per submit,
    polled non-blockingly (``job.result()`` would block -- the worker owns the
    wait so a cooperative stop can land mid-queue)."""

    def __init__(self, backend_name, shots, seed):
        import warnings

        from qiskit_ionq import IonQProvider  # lazy: only the cloud path needs it
        from qiskit_ionq.exceptions import IonQTranspileLevelWarning

        # Inapplicable advice here: we never transpile locally -- the circuits
        # are already in the QIS gateset and IonQ's server compiler does the
        # native synthesis.
        warnings.filterwarnings("ignore", category=IonQTranspileLevelWarning)
        self._backend = IonQProvider().get_backend(backend_name)  # $IONQ_API_KEY
        self._opts = {"shots": shots}
        if "simulator" in backend_name:
            self._opts["sampler_seed"] = seed  # deterministic client-side sampling

    def submit(self, circs):
        return self._backend.run(circs, **self._opts)

    def poll(self, job):
        from qiskit.providers.jobstatus import JOB_FINAL_STATES

        status = job.status()
        return status.name if status in JOB_FINAL_STATES else None

    def counts(self, job):
        result = job.result()
        return [result.get_counts(i) for i in range(len(result.results))]

    def cancel(self, job):
        job.cancel()


def jobs_backend(cfg):
    if cfg["backend"] == "local":
        return LocalJobs(cfg["shots"], cfg["seed"])
    return IonQJobs(cfg["backend"], cfg["shots"], cfg["seed"])
