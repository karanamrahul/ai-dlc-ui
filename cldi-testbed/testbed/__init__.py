"""Two-board Wi-Fi per-patch QA testbed (baseline vs candidate builds).

Stdlib only. Board access via SSH (subprocess `ssh`) or serial (pyserial, optional).
`--backend sim` runs the whole pipeline against a simulated pair of boards; every
artifact it writes is labelled SIMULATED.
"""
__version__ = "0.1.0"
