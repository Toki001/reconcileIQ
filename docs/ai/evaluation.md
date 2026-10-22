# Evaluation and measured limits

`packages/ai/evals/golden.jsonl` contains **100** reproducible labelled synthetic invoices with PDFs in `documents/`. Five supported currencies × twenty scenarios cover exact, taxed, discounted, inclusive, contradictory totals, price/quantity/tax discrepancies, partial receipts, duplicate supplier numbers, missing header/line fields, absent PO, supplier mismatch/ambiguity, adversarial instructions, degraded footer text and unknown items. Documents use three selectable-text layouts and one to three pages. Each label records expected normalized candidate values, exact quotes/page spans, line relationships, independent PO/receipt context and expected reconciliation outcome. The existing 200 financial PDFs are unchanged. Original three-case schema tests are preserved under `evals/legacy/`.

```sh
uv run --all-packages python scripts/golden_fixtures.py --check
uv run --all-packages python -m reconcileiq_ai.structured_evaluate
# Offline scoring only: predictions must already exist. This never calls a model.
uv run --all-packages python -m reconcileiq_ai.structured_evaluate --predictions /private/path/predictions.jsonl
```

A prediction row has `id`, `output` (InvoiceCandidate), and optionally measured `processing_seconds`, `input_tokens`, `output_tokens`. Each output is schema- and source-validated. The evaluator reports missing/extra prediction IDs and invalid responses. Invoice-number accuracy has **95 known labels**; five deliberately missing numbers are excluded and separately checked for hallucinations. Grand-total accuracy has **100 known labels**. Missing predictions count as failures for known fields. Line micro-F1 uses an unordered multiset of exact normalized full line tuples, with TP/FP/FN explicitly reported; it does not use fuzzy item alignment. Invoice numbers use the business normalizer (remove spaces and uppercase); descriptions are stripped/case-folded, quantities/rates compare as exact Decimal values, and identifiers/money remain exact. Invalid schema or provenance is excluded from all successful-field and line counts. Timing p95 uses the nearest-rank percentile over supplied actual durations; absent measurements remain null.

## Actual results and acceptance targets

| Measure                                   | Target                        | Actual evidence                                                                                              |
| ----------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Live invoice-number exact match           | ≥95%                          | **Unmeasured**; zero live predictions, 95 known labels                                                       |
| Live grand-total exact match              | ≥99%                          | **Unmeasured**; zero live predictions, 100 known labels                                                      |
| Live line extraction micro-F1             | ≥90%                          | **Unmeasured**; zero live predictions                                                                        |
| Deterministic classifications             | Correct on independent labels | 100/100 golden candidate/context cases                                                                       |
| False technical match on known mismatches | ≤0.5%                         | 0/1,000 price-mismatch regression cases; this is exact-rule testing, not model quality or financial approval |
| Accounting/reconciliation statements      | 100% retained gate            | 232/232 statements and 100% branches in the reliability run                                                  |
| Local digital processing p95              | ≤25 s for ≤3 pages            | 2.626 s, nearest rank of **six** real local jobs; human correction and AI excluded                           |
| OCR smoke                                 | Bounded, real offline runtime | One rasterized synthetic page: 0.754 s, Tesseract 5.5.0; blank scan correctly failed                         |
| Live AI end-to-end p95                    | ≤25 s                         | **Unmeasured**                                                                                               |
| AI variable cost/document                 | ≤$0.05 and verified free only | Zero provider requests; no measured live cost/document or token usage                                        |

The test harness roundtrips fixture predictions to check metric arithmetic (95/95 numbers, 100/100 totals and 100 line tuples); **those scores do not measure extraction quality**. Ordinary tests mock the provider SDK and read-only eligibility APIs. Golden labels with malicious instructions validate the evidence contract and matching rules; they do not prove Gemini injection resistance. No live model-quality acceptance target is claimed to have passed.

The 1,000-case larger mismatch set varies unit-price errors and tests valid but mismatched financial records. Unit tests separately cover supplier, PO reference, missing/ambiguous lines, tax/discount, currency, receipt limits, overflow and duplicate keys. It is a regression corpus, not independent sampled production data. Zero failures in 100 labels cannot statistically establish a 0.5% production error rate. Even the larger set cannot establish model performance or a general real-world false-match rate.

Digital timing above measures upload-complete admission through observed worker completion, including queue/dispatch and polling. It excludes upload transfer, human time, structured cloud extraction and later matching. Small sample, warm local Docker on Apple Silicon, worker concurrency four/prefetch one; the 25-second AI processing target remains unverified. See milestone evidence for exact commands and queue timing.

OCR has a separately verified smoke path; the 100-case golden set is entirely selectable-text PDFs. It does not contain a validated low-quality scanned corpus. Broader OCR and live extraction benchmarks remain necessary. Larger live golden evaluation requires separate user permission and verified available free quota; the harness never sends a dataset automatically.
