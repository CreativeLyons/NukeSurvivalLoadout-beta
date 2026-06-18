"""NSL boot layer - version gate, self-recovery, sequence.

Submodules import ``nuke`` at module top (the canonical pattern inside a Plugin's
``init.py``). This package therefore does NOT auto-import its submodules -
that would trigger ``import nuke`` on every ``import nsl.boot`` and fail
outside a Nuke runtime. Consumers import each submodule explicitly:

    from nsl.boot.self_recovery import run_phase
    from nsl.boot.sequence import run_boot_sequence
    from nsl.boot.version_gate import check_nuke_version
"""
