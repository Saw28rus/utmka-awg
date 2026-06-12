from dataclasses import dataclass


@dataclass(frozen=True)
class SshCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class SshClient:
    """Paramiko wrapper placeholder for Phase 1."""

    async def run(self, command: str, *, timeout_seconds: int = 30) -> SshCommandResult:
        raise NotImplementedError("SSH commands will be implemented in Phase 1.")
