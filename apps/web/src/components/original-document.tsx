"use client";
import Image from "next/image";
import { useEffect, useState } from "react";
import { createApi } from "@reconcileiq/client";
const api = createApi("/api/review");
export function OriginalDocument({ invoiceId }: { invoiceId: string }) {
  const [page, setPage] = useState(1),
    [count, setCount] = useState(1);
  const [image, setImage] = useState<string | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true,
      objectUrl: string | undefined;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setImage(null);
      setError("");
      try {
        const result = await api.GET("/v1/invoices/{invoice_id}/pages/{page}", {
          params: { path: { invoice_id: invoiceId, page } },
          parseAs: "blob",
          signal: controller.signal,
        });
        if (!result.data || result.data.type !== "image/png")
          throw new Error(
            "Preview unavailable. Download the original PDF for local review.",
          );
        const pages = Number(result.response.headers.get("X-Document-Pages"));
        if (!Number.isInteger(pages) || pages < 1 || pages > 20)
          throw new Error("Invalid document page count.");
        if (active) {
          objectUrl = URL.createObjectURL(result.data);
          setImage(objectUrl);
          setCount(pages);
        }
      } catch (e) {
        if (active)
          setError(
            e instanceof Error ? e.message : "Document preview unavailable.",
          );
      }
    }, 0);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [invoiceId, page]);
  return (
    <div className="review-original">
      <div className="review-toolbar">
        <span>
          Original document · page {page} of {count}
        </span>
        <a
          href={`/api/review/v1/invoices/${invoiceId}/document`}
          download="invoice.pdf"
        >
          Download original PDF
        </a>
      </div>
      <div className="review-pdf" aria-busy={!image && !error}>
        {image ? (
          <Image
            src={image}
            width={1600}
            height={2200}
            unoptimized
            alt={`Original invoice page ${page}`}
          />
        ) : error ? (
          <p role="alert">{error}</p>
        ) : (
          <p role="status">Rendering original page…</p>
        )}
      </div>
      <div className="review-toolbar">
        <button disabled={page <= 1} onClick={() => setPage((n) => n - 1)}>
          Previous document page
        </button>
        <button disabled={page >= count} onClick={() => setPage((n) => n + 1)}>
          Next document page
        </button>
      </div>
    </div>
  );
}
