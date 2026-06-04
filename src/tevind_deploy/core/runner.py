"""Subprocess helper used by all core modules.

``capture=False`` (default) streams output live to the terminal (used by the
CLI for ``logs -f``, builds, etc.). ``capture=True`` collects combined
stdout+stderr and returns it as text (used by the MCP server so a tool call can
return the output to the caller).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class Result:
    returncode: int
    output: str = ""


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, output: str = ""):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.output = output
        pretty = " ".join(self.cmd)
        msg = f"Command failed ({returncode}): {pretty}"
        if output:
            msg += f"\n{output}"
        super().__init__(msg)


def run(
    cmd: Sequence[str],
    env: Optional[dict[str, str]] = None,
    capture: bool = False,
    check: bool = True,
    input_text: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Result:
    if capture:
        proc = subprocess.run(
            list(cmd),
            env=env,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
        )
        output = proc.stdout or ""
        if check and proc.returncode != 0:
            raise CommandError(cmd, proc.returncode, output)
        return Result(proc.returncode, output)

    proc = subprocess.run(
        list(cmd),
        env=env,
        text=True,
        input=input_text,
        cwd=cwd,
    )
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, "")
    return Result(proc.returncode, "")


@dataclass
class OutputCollector:
    """Accumulates human-readable status lines and captured command output.

    Multi-step operations use this so that in capture mode the MCP server gets a
    single combined transcript, while in stream mode lines are printed live.
    """

    capture: bool = False
    _parts: list[str] = field(default_factory=list)

    def status(self, message: str) -> None:
        if self.capture:
            self._parts.append(message)
        else:
            print(message, flush=True)

    def add(self, result: Result) -> None:
        if self.capture and result.output:
            self._parts.append(result.output.rstrip("\n"))

    def text(self) -> str:
        return "\n".join(p for p in self._parts if p)
