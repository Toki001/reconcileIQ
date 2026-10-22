"""Synthetic Linux parser/OCR verification. Does not connect to any backend service."""

import json
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from golden_fixtures import text_pdf
from reconcileiq_worker.providers import ProcessingError
from reconcileiq_worker.restricted_parser import RestrictedPdfProvider


def scan_pdf(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.pdf"
        target = Path(directory) / "scan"
        source.write_bytes(data)
        subprocess.run(
            ["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "1200", str(source), str(target)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        magic, width, height, maximum, pixels = (
            Path(str(target) + ".ppm").read_bytes().split(maxsplit=4)
        )
        assert magic == b"P6" and maximum == b"255"
        compressed = zlib.compress(pixels)
    content = b"q 612 0 0 842 0 0 cm /Im0 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /XObject /Subtype /Image /ColorSpace /DeviceRGB /BitsPerComponent 8 /Width "
        + width
        + b" /Height "
        + height
        + b" /Filter /FlateDecode /Length "
        + str(len(compressed)).encode()
        + b" >>\nstream\n"
        + compressed
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    result += (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(result)


def main() -> None:
    assert sys.platform == "linux", "Run in the local Linux image"
    digital = text_pdf(
        [["SYNTHETIC INVOICE NO PAYMENT", "Invoice SYN-SCAN-001", "Total USD 123.45"]], 1
    )
    at = time.perf_counter()
    parsed = RestrictedPdfProvider().extract(digital)
    digital_seconds = time.perf_counter() - at
    scanned = scan_pdf(digital)
    try:
        RestrictedPdfProvider().extract(scanned)
        raise AssertionError("Scanned PDF must require OCR")
    except ProcessingError as error:
        assert error.code == "OCR_REQUIRED"
    at = time.perf_counter()
    recognized = RestrictedPdfProvider(ocr_enabled=True).extract(scanned)
    ocr_seconds = time.perf_counter() - at
    assert "SYN-SCAN-001" in recognized.pages[0].text
    blank = scan_pdf(text_pdf([[""]], 0))
    try:
        RestrictedPdfProvider(ocr_enabled=True).extract(blank)
        raise AssertionError("Unreadable document must require manual review")
    except ProcessingError as error:
        assert error.code == "OCR_LOW_CONFIDENCE"
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from reconcileiq_worker.parser_process import restrict; "
                "restrict(); import socket; socket.socket()"
            ),
        ],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=10,
    )
    assert check.returncode != 0 and b"Operation not permitted" in check.stderr
    version = (
        subprocess.run(["tesseract", "--version"], capture_output=True, check=True, timeout=5)
        .stdout.decode()
        .splitlines()[0]
    )
    print(
        json.dumps(
            {
                "digital_provider": parsed.provider,
                "digital_seconds": round(digital_seconds, 3),
                "ocr_provider": recognized.provider,
                "ocr_seconds": round(ocr_seconds, 3),
                "pages": len(recognized.pages),
                "tesseract": version,
                "network_syscalls": "denied",
                "blank_scan": "OCR_LOW_CONFIDENCE",
                "scope": (
                    "one synthetic digital and one rasterized scanned document, "
                    "not an OCR quality benchmark"
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
