"""Raster-only original PDF preview. Restricted child receives bytes, never credentials."""

import os
import signal
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore

from reconcileiq_api.errors import ApiError

CAPACITY = BoundedSemaphore(2)


def render_page(data: bytes, page: int) -> tuple[bytes, int]:
    if not CAPACITY.acquire(blocking=False):
        raise ApiError(429, "ADMISSION_LIMIT", "PDF preview capacity is busy. Retry shortly.")
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [sys.executable, "-m", "reconcileiq_api.pdf_preview", str(page)],
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
                process.communicate(data, timeout=25)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ApiError(503, "DOCUMENT_UNAVAILABLE", "Document preview timed out.") from None
            output.seek(0)
            raw = output.read(6_000_001)
        count, separator, image = raw.partition(b"\n")
        if (
            process.returncode
            or len(raw) > 6_000_000
            or not separator
            or not count.isdigit()
            or not 1 <= int(count) <= 20
            or not image.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise ApiError(
                503,
                "DOCUMENT_UNAVAILABLE",
                "PDF preview unavailable. Download the original PDF for local review.",
            )
        return image, int(count)
    finally:
        CAPACITY.release()


def main() -> None:
    # Import after the child starts; no backend settings or credentials are passed.
    from io import BytesIO
    from pathlib import Path

    from pypdf import PdfReader
    from reconcileiq_ai.sandbox import restrict

    restrict()
    page = int(sys.argv[1])
    data = sys.stdin.buffer.read(10_485_761)
    if not 5 <= len(data) <= 10_485_760 or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF")
    reader = PdfReader(BytesIO(data), strict=True)
    if (
        reader.is_encrypted
        or not 1 <= len(reader.pages) <= 20
        or not 1 <= page <= len(reader.pages)
    ):
        raise ValueError("Preview bounds")
    with tempfile.TemporaryDirectory(prefix="reconcile-preview-") as directory:
        source = Path(directory) / "source.pdf"
        target = Path(directory) / "page"
        source.write_bytes(data)
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page),
                "-singlefile",
                "-scale-to",
                "1600",
                "-png",
                str(source),
                str(target),
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        image = Path(str(target) + ".png").read_bytes()
        sys.stdout.buffer.write(str(len(reader.pages)).encode() + b"\n" + image)


if __name__ == "__main__":
    main()
