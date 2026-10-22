"""Resource-bounded parser child. Never receives service credentials or document paths."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from reconcileiq_ai.sandbox import SandboxUnavailable, restrict

from reconcileiq_worker.providers import DigitalPdfProvider, DocumentText, ProcessingError, TextPage


def ocr(data: bytes) -> DocumentText:
    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        raise ProcessingError("OCR_RUNTIME_UNAVAILABLE")
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ProcessingError("ENCRYPTED_PDF")
    if not 1 <= len(reader.pages) <= 3:
        raise ProcessingError("OCR_PAGE_LIMIT")
    pages = []
    with tempfile.TemporaryDirectory(prefix="reconcile-ocr-") as directory:
        source = Path(directory) / "input.pdf"
        source.write_bytes(data)
        for number in range(1, len(reader.pages) + 1):
            target = Path(directory) / "page"
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(number),
                    "-singlefile",
                    "-scale-to",
                    "1800",
                    "-png",
                    str(source),
                    str(target),
                ],
                check=True,
                timeout=8,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            output = subprocess.run(
                ["tesseract", str(target) + ".png", "stdout", "-l", "eng", "tsv"],
                check=True,
                timeout=8,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
            words, scores = [], []
            for line in output.decode("utf-8", errors="strict").splitlines()[1:]:
                columns = line.split("\t", 11)
                if len(columns) == 12 and columns[11].strip() and columns[10] != "-1":
                    words.append(columns[11])
                    scores.append(float(columns[10]))  # OCR confidence only; never monetary math.
            if len(words) < 3 or sum(scores) / len(scores) < 50:
                raise ProcessingError("OCR_LOW_CONFIDENCE")
            pages.append(TextPage(page=number, text=" ".join(words)))
    return DocumentText(provider="tesseract-eng-v1", pages=pages)


def main() -> None:
    try:
        restrict()
        data = sys.stdin.buffer.read(10_485_761)
        if not 5 <= len(data) <= 10_485_760 or not data.startswith(b"%PDF-"):
            raise ProcessingError("INVALID_PDF")
        try:
            content = DigitalPdfProvider().extract(data)
        except ProcessingError as error:
            if error.code != "OCR_REQUIRED" or "--ocr" not in sys.argv:
                raise
            content = ocr(data)
        print(json.dumps({"content": content.model_dump()}))
    except SandboxUnavailable:
        print(json.dumps({"error": "PARSER_SANDBOX_UNAVAILABLE"}))
    except ProcessingError as error:
        print(json.dumps({"error": error.code}))
    except Exception:
        print(json.dumps({"error": "PARSER_FAILED"}))


if __name__ == "__main__":
    main()
