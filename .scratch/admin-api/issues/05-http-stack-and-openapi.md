Type: research
Status: resolved
Blocked by:

# HTTP stack and OpenAPI generation

## Question

With `mcp` 2.3.0 already pulling in `starlette`, `uvicorn`, `sse-starlette` and `pydantic`, what are the options
for serving the admin API and producing its OpenAPI document, and what does each cost?

- Can the admin process use the transitive `starlette` + `uvicorn` safely, or must they be declared in
  `server/pyproject.toml`? What versions does `server/uv.lock` pin?
- Options for the OpenAPI document: hand-authored YAML/JSON; built from pydantic `model_json_schema()` plus a route
  table; `starlette`'s `SchemaGenerator`; adding FastAPI. For each: new dependencies, fidelity, and how a
  golden-file test would detect drift between the document and the routes.
- How bearer-token auth and CORS are done with the chosen stack (middleware available without new installs).

## Answer

Full findings: branch `research/http-stack-and-openapi`, file `docs/research/http-stack-and-openapi.md` (never
merged; read with `git show origin/research/http-stack-and-openapi:docs/research/http-stack-and-openapi.md`).

- **Pins (`server/uv.lock`).** starlette 1.10.0, uvicorn 0.54.0, sse-starlette 3.6.0, pydantic 2.14.2, anyio 4.16.0,
  python-multipart 0.0.33, httpx2 2.16.0. mcp 2.3.0 only guarantees `starlette>=0.27` and
  `uvicorn>=0.31.1; sys_platform != 'emscripten'`. No fastapi installed.
- **The lock does not apply in the field.** `scripts/launch.sh` runs `uv pip install "$R/server"`, which resolves
  from `pyproject.toml`, not `uv.lock`. The code already imports `pydantic` and `anyio` without declaring them.
  **Declare what the admin process imports** (`starlette>=1.10`, `uvicorn>=0.54`; also `pydantic` and `anyio`). No
  install cost.
- **Serving.** A `Starlette` app run by `uvicorn.run(app, host, port)` from a new `[project.scripts]` entry. For tests,
  `starlette.testclient.TestClient` uses `httpx2`, which is already installed.
- **OpenAPI options.**

  | Option | New deps | Body fidelity | Drift test |
  |---|---|---|---|
  | A hand-authored | none | manual | route-set check plus `jsonschema` validation of responses |
  | B pydantic `models_json_schema` + route table | none | high | true golden; routes built from the same table |
  | C starlette `SchemaGenerator` | pyyaml at runtime if served | low (YAML in docstrings) | golden; silently skips routes without a YAML docstring |
  | D FastAPI 0.162.0 | `fastapi` only (its other deps are already in the lock) | highest | golden of `app.openapi()`; churns on upgrades |

  For B, override `ref_template="#/components/schemas/{model}"`, use `mode="serialization"` and target OpenAPI 3.1.
- **CORS.** starlette `CORSMiddleware`. It allows only GET by default, so list the method `end` uses and allow the
  `Authorization` header. Put it outermost so preflights and 401 responses get CORS headers.
- **Auth.** Use a small ASGI middleware with `secrets.compare_digest` that returns 401. starlette's
  `AuthenticationMiddleware` also works, but its default error is 400 and `@requires` fails with 403, so both need
  overriding. mcp's `BearerAuthBackend` is built for OAuth and is too heavy for a static token.
- **Recommendation (for the human to weigh).** starlette + uvicorn, declared in `pyproject.toml`. Option B for
  OpenAPI. D if less glue is worth one new dependency, A if the contract should be designed by hand first. Avoid C.
