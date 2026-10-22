"""Disposable parsing process, clean environment, bounded output, wall/CPU/memory limits."""

import json
import os
import signal
import subprocess
import sys
import tempfile

from reconcileiq_worker.providers import DocumentText, ProcessingError


class RestrictedPdfProvider:
    def __init__(self, *, ocr_enabled: bool = False) -> None:
        self.ocr_enabled = ocr_enabled

    def extract(self, data: bytes) -> DocumentText:
        if len(data) > 10_485_760:
            raise ProcessingError("PDF_SIZE_LIMIT")
        command = [sys.executable, "-m", "reconcileiq_worker.parser_process"]
        if self.ocr_enabled:
            command.append("--ocr")
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "OMP_THREAD_LIMIT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            try:
                child.communicate(data, timeout=25)
            except BaseException as error:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                if isinstance(error, subprocess.TimeoutExpired):
                    raise ProcessingError("PARSER_TIMEOUT", True) from None
                raise
            output.seek(0)
            raw = output.read(1_000_001)
        if child.returncode != 0 or len(raw) > 1_000_000:
            raise ProcessingError("PARSER_RESOURCE_LIMIT")
        try:
            result = json.loads(raw)
            if "error" in result:
                raise ProcessingError(str(result["error"]))
            return DocumentText.model_validate(result["content"])
        except (ValueError, KeyError, TypeError):
            raise ProcessingError("PARSER_INVALID_RESPONSE") from None
