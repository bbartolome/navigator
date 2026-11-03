# HTTP stack and OpenAPI generation for the admin API

Research for wayfinder ticket `.scratch/admin-api/issues/05-http-stack-and-openapi.md` (2026-11-03). Options, not a
decision. `SP` below means `server/.venv/lib/python3.12/site-packages/`, the installed sources read for this note
(nothing was imported or executed from them).

## 1. What is installed, and how it gets installed

### Pinned versions (`server/uv.lock`)

| Package | Locked | Who requires it (constraint) |
|---|---|---|
| mcp | 2.3.0 | `server/pyproject.toml`: `mcp>=2.3.0` |
| starlette | 1.10.0 | mcp: `starlette>=0.27` (py<3.14), `>=0.48.0` (py>=3.14); sse-starlette: `>=0.49.1` |
| uvicorn | 0.54.0 | mcp: `uvicorn>=0.31.1; sys_platform != 'emscripten'` |
| sse-starlette | 3.6.0 | mcp: `>=3.0.0` |
| pydantic | 2.14.2 (pydantic-core 2.50.2) | mcp: `>=2.12.0` |
| anyio | 4.16.0 | mcp: `>=4.9` / `>=4.10`; starlette: `<5,>=4.0.0` |
| python-multipart | 0.0.33 | mcp: `>=0.0.9` |
| httpx2 | 2.16.0 | mcp: `>=2.10.0` (also what `starlette.testclient` prefers) |
| pyyaml | 6.0.3 | dev group only (`pyyaml>=6.0.3`) |
| jsonschema | 4.26.0 | dev group (`>=4.26.0`) and mcp (`>=4.20.0`) |
| annotated-doc | (in lock) | already present transitively (via typer); relevant to FastAPI below |

Sources: `server/uv.lock` package entries; constraints from `SP/mcp-2.3.0.dist-info/METADATA`,
`SP/sse_starlette-3.6.0.dist-info/METADATA`, `SP/starlette-1.10.0.dist-info/METADATA` (`Requires-Dist` lines).
uvicorn 0.54.0 needs only `click>=7.0` and `h11>=0.8` (`SP/uvicorn-0.54.0.dist-info/METADATA`). No `fastapi` in
the lock or venv.

### The lock does not govern the plugin install (surprise)

`scripts/launch.sh` builds the runtime venv with `uv pip install "$R/server"` (or `pip install "$R/server"` as
fallback). That resolves from `pyproject.toml` metadata, **not** from `uv.lock`; the lock only pins `uv sync`/dev
environments. So in the field, starlette is whatever the newest release satisfying mcp's floor (`>=0.27`) is at
install time, and uvicorn whatever satisfies `>=0.31.1`. The reinstall is triggered only when `pyproject.toml`
changes (the `cmp -s ... "$STAMP"` check).

### Existing precedent: undeclared direct imports

`server/src/navigator_mcp/*.py` already import `pydantic` (`session.py`, `schemas.py`, ...) and `anyio` directly,
yet `pyproject.toml` declares only `httpx` and `mcp`. Relying on transitive deps is already the de facto practice;
the admin process would extend it.

## 2. Transitive vs declared starlette/uvicorn

It works today: both are installed, and the admin process would just `import starlette` / `import uvicorn`. The
risks of not declaring them:

- **Floor too low.** mcp only guarantees `starlette>=0.27`. Starlette 1.x APIs (e.g. `schemas.py` shown below with
  `Mount | Host` isinstance and `QUERY` method support) differ from 0.27. Nothing in our metadata stops a resolver
  that, for some other reason, back-tracks to an old starlette. Our code would be written against 1.10.
- **Removal upstream.** `uvicorn` is conditional in mcp (`sys_platform != 'emscripten'`) and mcp could move its HTTP
  transports to an extra in a future major; the admin entry point would then fail at import with no resolver error.
- **No lock in the field** (above), so we cannot rely on `uv.lock` to freeze a known-good set for users.

Recommendation: declare what the admin process imports, with floors matching the API it is written against, e.g.
`starlette>=1.10`, `uvicorn>=0.54` (and, while touching the file, `pydantic>=2.12` and `anyio>=4.9`, which
`session.py` etc. already import). Cost: none at install time (all are already pulled in), and `uv lock` resolution
is unchanged as long as floors are at or below the locked versions. Changing `pyproject.toml` triggers one
reinstall per plugin venv via `launch.sh`.

## 3. Serving: starlette + uvicorn as installed

- App: `starlette.applications.Starlette(routes=[Route(...)], middleware=[Middleware(...)])`. Starlette always wraps
  user middleware between `ServerErrorMiddleware` (outermost) and `ExceptionMiddleware` (innermost)
  (`SP/starlette/applications.py` lines 63-78, `build_middleware_stack`).
- Server: `uvicorn.run(app, host=..., port=..., log_level=..., access_log=...)`; defaults `host="127.0.0.1"`,
  `port=8000` (`SP/uvicorn/main.py` line 503 onward). `uvicorn.Config` + `uvicorn.Server` exist for finer control.
  A `navigator-admin = "navigator_mcp.admin:main"` entry in `[project.scripts]` would read
  `NAVIGATOR_ADMIN_HOST`/`PORT` and call `uvicorn.run`.
- Tests: `starlette.testclient.TestClient` imports `httpx2` first, falls back to `httpx` with a
  `StarletteDeprecationWarning` (`SP/starlette/testclient.py` lines 24-41). `httpx2` is installed via mcp, so no new
  dev dependency.

## 4. OpenAPI document options

Common ground for every option: commit the document (e.g. `docs/openapi/admin.json` or `.yaml`) and test it like
`server/tests/test_render.py` does goldens, i.e. `assert produced == (GOLDEN / "...").read_text()`
(`test_render.py` lines 24, 89-99). Generated options compare generator output to the committed file; the
hand-authored option instead checks the document *against* the app.

### A. Hand-authored YAML/JSON

- New deps: none. YAML needs `pyyaml` to load in tests (already a dev dep); JSON needs nothing.
- Fidelity: as good as the author; schemas can drift from the pydantic models and routes silently unless tested.
- Drift test: not a golden comparison but a structural check. (1) Walk `app.routes` (paths + methods, converter
  syntax stripped as starlette does with `:\w+}` -> `}`, `SP/starlette/schemas.py` lines 33, 89-96) and assert the
  set equals `paths` x methods in the document. (2) Validate real responses from `TestClient` against the documented
  response schemas with `jsonschema` (dev dep; OpenAPI 3.1 schema objects are JSON Schema 2020-12 dialect). Catches
  route drift and shape drift on the cases the tests exercise, not exhaustive.
- Best fit if the contract is meant to be designed first and the UI repo treats it as the source of truth.

### B. pydantic `model_json_schema()` / `models_json_schema()` + an explicit route table

- New deps: none (pydantic is already used; session state is already pydantic models,
  `server/src/navigator_mcp/session.py` line 13 onward).
- How: a small module holds a route table (path, method, request model, response model(s), status codes, summary),
  used both to build the starlette `routes` list and to emit `paths`. Component schemas come from
  `pydantic.json_schema.models_json_schema([(Model, "serialization"), ...], ref_template="#/components/schemas/{model}")`
  (`SP/pydantic/json_schema.py` lines 2775-2784; default `ref_template` is `'#/$defs/{model}'`, line 116, so it must
  be overridden). `mode="serialization"` matters for response shapes (`BaseModel.model_json_schema(..., mode=...)`,
  `SP/pydantic/main.py` lines 607-615). Pydantic emits JSON Schema 2020-12, which OpenAPI 3.1 accepts directly; target
  `openapi: 3.1.x`, not 3.0.
- Fidelity: high for bodies (same models that serialize the responses); routes cannot drift because routes are built
  from the same table. Security scheme, error responses and descriptions are hand-written in the generator.
- Drift test: true golden. `assert json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n" ==
  committed.read_text()`, with a regeneration command (or env flag) to rewrite the file. Pydantic-version changes in
  schema output show up as golden diffs, which is the point but also churn on upgrades (unpinned in the field, though
  the test runs against the lock).
- Roughly 50-100 lines of glue we own.

### C. starlette `SchemaGenerator`

- Present in starlette 1.10.0 (`SP/starlette/schemas.py` lines 128-152) and documented
  (https://github.com/Kludex/starlette/blob/1.10.0/docs/schemas.md).
- How it works: walks `app.routes`, and for each endpoint parses the text after the last `---` in the docstring as
  YAML and drops it in as the operation object (`parse_docstring`, lines 98-120; `get_schema`, lines 132-152).
  Endpoints with no YAML docstring are silently omitted (line 144). `include_in_schema=False` routes are skipped
  (line 70).
- New deps: **pyyaml becomes a runtime dependency** if the schema is built or served at runtime: `parse_docstring`
  and `OpenAPIResponse` both `assert yaml is not None` (lines 12-15, 22, 106). pyyaml is only a dev dep today. If the
  document is only generated in tests, the dev dep suffices.
- Fidelity: low. Paths and methods come from routes, but every operation body is hand-written YAML in a docstring,
  with no link to the pydantic models; `components` must be supplied in `base_schema` by hand (or merged from
  option B's pydantic output, which makes it B with extra steps).
- Drift test: golden of `SchemaGenerator(base).get_schema(app.routes)` vs committed file. Catches added/removed
  routes that carry docstrings; misses a route whose docstring was forgotten (it is omitted, not flagged) and any
  body-shape drift.

### D. FastAPI

- New deps: `fastapi` itself. FastAPI 0.162.0 requires `starlette>=0.46.0`, `pydantic>=2.9.0`,
  `typing-extensions>=4.8.0`, `typing-inspection>=0.4.2`, `annotated-doc>=0.0.2`, `opentelemetry-api>=1.44.0`
  (PyPI JSON for fastapi 0.162.0, `requires_dist` without extras), all already in `uv.lock` (opentelemetry-api is
  pulled in by mcp). So the install delta is one pure-Python package, but it is a new direct dependency with its own
  release cadence that must stay compatible with the starlette mcp pulls.
- How: routes declared with type annotations; `app.openapi()` builds the document via
  `fastapi.openapi.utils.get_openapi(..., openapi_version="3.1.0", ...)`
  (https://github.com/fastapi/fastapi/blob/0.162.0/fastapi/openapi/utils.py line 577).
- Fidelity: highest out of the box: params, bodies, responses, `HTTPBearer` security scheme, and 422 validation
  errors all derived from code. Generated operation IDs and 422 schemas are FastAPI's choices unless overridden.
- Drift test: golden of `app.openapi()` vs committed file. Output may change on FastAPI upgrades (field ordering,
  `operationId` style), causing golden churn.
- Auth: `fastapi.security.HTTPBearer` raises 401 (`HTTP_401_UNAUTHORIZED`) when the header is missing
  (https://github.com/fastapi/fastapi/blob/0.162.0/fastapi/security/http.py lines 13, 89, 224); CORS is starlette's
  `CORSMiddleware` re-exported.

### Comparison

| Option | New runtime deps | Body fidelity | Route drift caught by | Notes |
|---|---|---|---|---|
| A hand-authored | none | manual | route-set test + response validation | contract-first |
| B pydantic + route table | none | high (same models) | construction (one table) + golden | ~50-100 lines glue |
| C starlette SchemaGenerator | pyyaml if built at runtime | low (docstring YAML) | golden, misses undocumented routes | weakest |
| D FastAPI | fastapi | highest | golden | adds a framework dependency |

## 5. Bearer auth and CORS without new installs

### CORS: `starlette.middleware.cors.CORSMiddleware`

`SP/starlette/middleware/cors.py`. Parameters: `allow_origins`, `allow_methods` (default `("GET",)`),
`allow_headers`, `allow_credentials`, `allow_origin_regex`, `allow_private_network`, `expose_headers`, `max_age`
(lines 16-27). `"*"` in `allow_origins` sends `Access-Control-Allow-Origin: *` (lines 35-41). Preflight is
answered by the middleware itself when the request is `OPTIONS` with `Origin` and `Access-Control-Request-Method`
(lines 84-88), so it never reaches inner middleware **if CORS is placed outside auth**. Things to set for this API:
`allow_methods` must include whatever `end` uses (e.g. `POST`/`DELETE`; default is GET only) and
`allow_headers=["Authorization"]` (not safelisted, lines 12, 58-60, 104-110). Default allow-all matches the
charting-round decision; with `*` origins and `allow_credentials=False` a bearer header still works because it is
not a cookie credential.

### Bearer token

Three no-install routes:

1. **starlette `AuthenticationMiddleware` + a custom `AuthenticationBackend`** (`SP/starlette/middleware/authentication.py`,
   `SP/starlette/authentication.py`). The backend's `authenticate(conn)` returns `(AuthCredentials, user)` or
   raises `AuthenticationError`. Caveats from the source: the default `on_error` returns **400**, not 401 (lines
   50-52), so pass `on_error` returning 401 with `WWW-Authenticate: Bearer`; and returning `None` lets the request
   through as `UnauthenticatedUser` (lines 45-48), so either raise for missing tokens or add `@requires("authenticated")`
   on endpoints, whose default failure is **403** (`authentication.py` line 25-28).
2. **A ~15-line pure ASGI middleware** that checks `Authorization: Bearer <token>` with `secrets.compare_digest`
   (stdlib) and returns 401. Simplest; no starlette auth machinery. (No `compare_digest` exists anywhere in
   starlette, so constant-time comparison is on us in options 1 and 2.)
3. **mcp's `BearerAuthBackend` / `RequireAuthMiddleware`** (`SP/mcp/server/auth/middleware/bearer_auth.py` lines
   44, 91, 119) return a proper 401 with `WWW-Authenticate`, but they are built around OAuth `TokenVerifier` /
   `AccessToken` objects and RFC 8707 resource checks: heavier than a static token, and couples the admin API to
   mcp internals.

Ordering: `middleware=[Middleware(CORSMiddleware, ...), Middleware(Auth...)]` (first listed is outermost inside
`ServerErrorMiddleware`) so preflights and the CORS headers on 401 responses work; otherwise a browser sees a
401 preflight with no CORS headers and reports a CORS error instead of an auth error. If the OpenAPI document is
also served over HTTP, decide whether that route is exempt from auth.

## 6. Recommendation (for the human to weigh)

- Serve with **starlette + uvicorn**, and **declare them** in `server/pyproject.toml` with floors at the locked
  versions (field installs ignore `uv.lock`).
- OpenAPI: **option B** (pydantic schemas + one route table, golden-tested) gives near-FastAPI fidelity with no new
  dependency and a true golden test in the `test_render.py` style. Choose **D (FastAPI)** if the team prefers less
  glue and accepts one new dependency; choose **A** if the contract should be designed by hand first. Avoid **C**.
- Auth as a small ASGI middleware with `secrets.compare_digest`, placed inside `CORSMiddleware`; CORS via
  starlette's `CORSMiddleware` with `Authorization` allowed and the `end` method listed.
- Target `openapi: 3.1.0` so pydantic's JSON Schema 2020-12 output is valid without translation.
