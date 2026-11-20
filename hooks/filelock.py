"""Lock exclusivo de archivo portátil: fcntl.flock en POSIX, msvcrt.locking en Windows (issue #3, T-24w).

Sin esto Synapse no se podía importar en Windows nativo (ModuleNotFoundError: fcntl) y sus hooks fallaban abierto.
"""

from __future__ import annotations

import os

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt


def lock_exclusive(fd: int, *, blocking: bool = True) -> None:
    """Toma el lock exclusivo del archivo. Sin blocking y con el lock tomado por otro: BlockingIOError.

    En Windows se bloquea el primer byte del archivo (msvcrt bloquea rangos desde la posición actual); las
    escrituras con O_APPEND siguen yendo al final.
    """
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if blocking:
            raise
        raise BlockingIOError(str(exc)) from exc
    finally:
        os.lseek(fd, position, os.SEEK_SET)
