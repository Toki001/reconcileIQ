from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class UntrustedExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    invoice_number: Annotated[str, Field(min_length=1, max_length=80)]
    currency: Literal["USD", "EUR", "PHP", "JPY", "KWD"]
    total_minor: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    source_pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1, max_length=100)]
