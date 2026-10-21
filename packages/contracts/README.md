# Milestone 01 API contract

`openapi.yaml` is the reviewed, checked-in OpenAPI 3.1 snapshot of the implemented health and synthetic workspace endpoints. Run `make contracts` after changing API schemas/handlers. `make check` detects server schema drift; `make contracts-check` also detects generated TypeScript drift.

Only read operations exist in this milestone. Errors from unavailable persistence return HTTP 503 with `{ code, detail }`, without driver details or credentials. Readiness returns HTTP 503 with dependency states. The demo snapshot is explicitly bounded to 100 records per collection; it is not the future paginated business API.

Authentication, tenant scoping, mutation contracts, cursor pagination and GraphQL are milestone 02 work. Do not treat the local demo contract as a production financial API.
