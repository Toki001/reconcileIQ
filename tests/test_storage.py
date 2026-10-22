import hashlib
from unittest.mock import MagicMock

import pytest
from reconcileiq_api.errors import ApiError
from reconcileiq_api.storage import MinioStorage
from reconcileiq_database.config import Settings


@pytest.mark.parametrize("failure", [None, "size", "mime", "digest", "signature", "trailing"])
def test_upload_is_verified_before_sealing(failure: str | None) -> None:
    original = b"%PDF-1.4\n%%EOF\n"
    data = b"not a PDF file!" if failure == "signature" else original
    digest = hashlib.sha256(data).hexdigest()
    sdk = MagicMock()
    sdk.stat_object.return_value.size = len(data) + (1 if failure == "size" else 0)
    sdk.stat_object.return_value.content_type = (
        "text/plain" if failure == "mime" else "application/pdf"
    )
    sdk.get_object.return_value.read.return_value = data + (b"x" if failure == "trailing" else b"")
    storage = MinioStorage(Settings())
    storage.client = MagicMock(return_value=sdk)  # type: ignore[method-assign]
    if failure:
        with pytest.raises(ApiError) as error:
            storage.verify_and_seal(
                "demo/uploads/x.pdf", "demo", len(data), "0" * 64 if failure == "digest" else digest
            )
        assert error.value.code == "UPLOAD_INVALID"
        sdk.put_object.assert_not_called()
    else:
        sealed = storage.verify_and_seal("demo/uploads/x.pdf", "demo", len(data), digest)
        assert sealed.startswith("demo/sealed/")
        assert sdk.put_object.call_args.args[1] == sealed
        assert sdk.put_object.call_args.args[2].getvalue() == data
        sdk.get_object.return_value.close.assert_called_once()
        sdk.get_object.return_value.release_conn.assert_called_once()
