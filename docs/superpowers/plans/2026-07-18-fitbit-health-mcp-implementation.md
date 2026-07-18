# Fitbit Health MCP Server Implementation Plan

> **For Codex:** Execute this plan task-by-task with test-driven development. Keep all work on `feat/fitbit-health-pipeline`; do not merge.

**Goal:** Add a local stdio MCP server that exposes the existing Fitbit/Google Health pipeline to ChatGPT/Codex through six structured-JSON tools.

**Architecture:** Add a non-interactive saved-token path to the existing authentication module, compose existing client/normalization/analysis APIs in a new `HealthMCPService`, and keep `FastMCP` registration and stdio startup in a thin `mcp_server` module. Production startup must perform no OAuth or API request; tests inject fake services and clients so automated runs never contact Google.

**Tech Stack:** Python 3.12, official MCP Python SDK v1 (`mcp>=1.27,<2`), FastMCP, google-auth, pytest, official MCP `ClientSession`/stdio client.

---

## Task 1: Add the MCP dependency and non-interactive saved-token authentication

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/fitbit_health/auth.py`
- Modify: `tests/test_auth.py`

### Step 1: Write failing saved-token tests

Add tests to `tests/test_auth.py` for `load_saved_credentials(token_path, scopes, request=None)`:

```python
def test_load_saved_credentials_returns_valid_token(tmp_path, monkeypatch):
    credentials = FakeCredentials(valid=True)
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *_: credentials)
    assert load_saved_credentials(tmp_path / "token.json", SCOPES) is credentials


def test_load_saved_credentials_refreshes_and_persists(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    credentials = FakeCredentials(valid=False, expired=True, refresh_token="refresh")
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *_: credentials)
    loaded = load_saved_credentials(token_path, SCOPES, request=object())
    assert loaded is credentials
    assert credentials.refresh_calls == 1
    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == "new-token"


@pytest.mark.parametrize("case", ["missing", "invalid", "no_refresh", "refresh_failure"])
def test_load_saved_credentials_returns_sanitized_auth_errors(case, token_case):
    token_path, patches = token_case(case)
    patches.apply()
    with pytest.raises(AuthError) as error:
        load_saved_credentials(token_path, SCOPES, request=object())
    assert "python -m fitbit_health sync --days 1" in str(error.value)
    assert "secret" not in str(error.value).lower()
```

Also capture stdout and assert the non-interactive loader prints nothing and never constructs `InstalledAppFlow`.

### Step 2: Run the targeted tests and confirm they fail

Run:

```powershell
python -m pytest tests/test_auth.py -q
```

Expected: FAIL because `load_saved_credentials` does not exist.

### Step 3: Implement the minimal non-interactive loader

In `src/fitbit_health/auth.py`:

- Add a single sanitized bootstrap message constant.
- Read only the existing token JSON with `Credentials.from_authorized_user_file`.
- Return immediately when valid.
- Refresh only when expired and a refresh token exists.
- Persist refreshed credentials using the existing `ensure_private_file` behavior.
- Convert missing, malformed, unrefreshable, and refresh-failure states to `AuthError` without including the underlying exception text.
- Do not call `resolve_credentials`, create `InstalledAppFlow`, open a browser, or write to stdout.
- Keep the existing interactive `load_credentials` behavior unchanged for the CLI.

Target interface:

```python
def load_saved_credentials(
    token_path: Path,
    scopes: tuple[str, ...],
    request: Request | None = None,
) -> Credentials:
    """Load or refresh saved OAuth credentials without user interaction."""
```

Add the official SDK dependency to `pyproject.toml`:

```toml
dependencies = [
  "google-auth>=2.40,<3",
  "google-auth-oauthlib>=1.2,<2",
  "mcp>=1.27,<2",
  "requests>=2.32,<3",
]
```

### Step 4: Run authentication and regression tests

Run:

```powershell
python -m pytest tests/test_auth.py -q
python -m pytest -q
```

Expected: all authentication tests and the existing suite pass.

### Step 5: Commit

```powershell
git add pyproject.toml src/fitbit_health/auth.py tests/test_auth.py
git commit -m "feat: add noninteractive MCP authentication"
```

---

## Task 2: Build the reusable MCP tool service

**Files:**
- Create: `src/fitbit_health/mcp_tools.py`
- Create: `tests/test_mcp_tools.py`

### Step 1: Write failing tests for the six tool schemas

Create synthetic `FetchResult` fixtures matching the payload shapes already covered by normalization tests. Inject a fake client whose `fetch_all(data_type, start_date)` records calls and returns those fixtures.

Test each public method:

```python
@pytest.mark.parametrize(
    ("method_name", "expected_type", "expected_fields"),
    [
        ("get_sleep", "sleep", {"date", "minutes_asleep", "minutes_awake", "deep_minutes", "rem_minutes", "light_minutes", "start_time", "end_time"}),
        ("get_steps", "steps", {"date", "steps"}),
        ("get_heart_rate", "heart-rate", {"date", "heart_rate_average"}),
        ("get_resting_heart_rate", "daily-resting-heart-rate", {"date", "resting_heart_rate"}),
        ("get_hrv", "daily-heart-rate-variability", {"date", "hrv_rmssd"}),
    ],
)
def test_metric_tools_return_fixed_json_schema(
    service, fake_client, method_name, expected_type, expected_fields
):
    result = getattr(service, method_name)(days=7)
    assert set(result) == {"requested_days", "available_days", "data", "missing_data", "diagnostics"}
    assert result["requested_days"] == 7
    assert set(result["data"][0]) == expected_fields
    assert fake_client.calls == [(expected_type, expected_start_date)]
    json.dumps(result)
```

For `get_health_summary`, assert the fake client is called once for each item in `pipeline.DATA_TYPES`, `data` contains the unchanged `analyze()` result, and the same five top-level envelope fields exist.

### Step 2: Add failing edge-case tests

Cover:

- `days=0`, `days=366`, and non-integer values return a validation diagnostic and do not invoke the client.
- Empty API data returns `available_days=0`, an empty `data` list for metric tools, and every requested ISO date in `missing_data`.
- A single `FetchResult.error` is reported in diagnostics without raising.
- Summary preserves successful types when one type fails.
- Authentication failure returns the fixed envelope with `diagnostics.authentication`, no exception, and no sensitive substrings.
- Every result can be serialized by `json.dumps`.
- The metric methods return only their own normalized fields, never raw minute samples or raw API responses.

### Step 3: Run the tests and confirm they fail

Run:

```powershell
python -m pytest tests/test_mcp_tools.py -q
```

Expected: FAIL because `fitbit_health.mcp_tools` does not exist.

### Step 4: Implement `HealthMCPService`

Create `src/fitbit_health/mcp_tools.py` with:

```python
class HealthMCPService:
    def __init__(
        self,
        root: Path,
        client_factory: Callable[[], GoogleHealthClient] | None = None,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        """Configure a lazy local health-data service."""

    def get_sleep(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily sleep data."""

    def get_steps(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily steps."""

    def get_heart_rate(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily average heart rate."""

    def get_resting_heart_rate(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily resting heart rate."""

    def get_hrv(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily HRV RMSSD."""

    def get_health_summary(self, days: int = 7) -> dict[str, Any]:
        """Return analysis over all supported health metrics."""
```

Implementation rules:

- The default client factory uses `find_installed_credentials(root)`, `load_saved_credentials(root / ".private" / "token.json", SCOPES)`, and `GoogleHealthClient(credentials)`.
- Client creation occurs inside a tool call, never during server import or startup.
- Calculate inclusive `start_date`/`end_date` exactly like `run_sync`.
- Each metric method calls only one existing `GoogleHealthClient.fetch_all` data type.
- `get_health_summary` imports and iterates `pipeline.DATA_TYPES`, then calls the existing `normalize_results` and `analyze` functions.
- A common envelope builder supplies all five required top-level fields for success and errors.
- `available_days` counts days whose primary field is non-null.
- `missing_data` is derived from the normalized requested date range.
- Diagnostics include only safe type/status messages; never include credentials, headers, exception reprs, or raw response bodies.
- Catch `AuthError` separately; catch unexpected tool-boundary exceptions and return a generic internal diagnostic so the MCP process remains alive.

### Step 5: Run service and regression tests

Run:

```powershell
python -m pytest tests/test_mcp_tools.py -q
python -m pytest -q
```

Expected: all new service tests and existing tests pass.

### Step 6: Commit

```powershell
git add src/fitbit_health/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: expose health pipeline as MCP tools"
```

---

## Task 3: Add the FastMCP stdio server and command entry point

**Files:**
- Create: `src/fitbit_health/mcp_server.py`
- Modify: `pyproject.toml`
- Create: `tests/test_mcp_server.py`

### Step 1: Write failing server-registration tests

Create a fake service implementing the six methods and a factory that records whether it was instantiated. Test:

```python
def test_create_server_is_lazy_and_registers_exactly_six_tools():
    server = create_server(service_factory)
    assert service_factory.calls == 0
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "get_sleep", "get_steps", "get_heart_rate",
        "get_resting_heart_rate", "get_hrv", "get_health_summary",
    }


def test_registered_tool_returns_service_json():
    result = asyncio.run(server.call_tool("get_steps", {"days": 3}))
    assert extract_structured_result(result)["requested_days"] == 3
```

Also assert every tool exposes an integer `days` argument with default `7` and descriptive text.

### Step 2: Run and confirm the tests fail

Run:

```powershell
python -m pytest tests/test_mcp_server.py -q
```

Expected: FAIL because `fitbit_health.mcp_server` does not exist.

### Step 3: Implement the thin FastMCP module

Create `src/fitbit_health/mcp_server.py`:

- Import `FastMCP` from `mcp.server.fastmcp`.
- Provide `create_server(service_factory=None) -> FastMCP`.
- Instantiate `FastMCP("Fitbit Health", json_response=True)`.
- Register six functions with `@server.tool()` and exact public names/signatures.
- Resolve the service lazily on the first tool call; reuse it for later calls in the same process.
- Each wrapper delegates directly to the matching `HealthMCPService` method and returns its dictionary.
- Do not print, configure stdout logging, authenticate, or call Google during import/startup.
- Provide `main()` that calls `create_server().run(transport="stdio")`.
- Include `if __name__ == "__main__": main()`.

Add the console entry point:

```toml
[project.scripts]
fitbit-health = "fitbit_health.__main__:main"
fitbit-health-mcp = "fitbit_health.mcp_server:main"
```

### Step 4: Run registration and entry-point tests

Run:

```powershell
python -m pytest tests/test_mcp_server.py -q
python -m fitbit_health.mcp_server
```

For the manual module-start smoke check, send an MCP `initialize` frame through the stdio integration test in Task 4 rather than leaving the process waiting at a terminal.

Expected: registration tests pass; the module is importable and exposes `main`.

### Step 5: Commit

```powershell
git add pyproject.toml src/fitbit_health/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add Fitbit Health stdio MCP server"
```

---

## Task 4: Verify real stdio protocol handshake and a mock tool call

**Files:**
- Create: `tests/fixture_mcp_server.py`
- Create: `tests/test_mcp_stdio.py`

### Step 1: Add a deterministic subprocess fixture server

In `tests/fixture_mcp_server.py`, define a fake service whose six methods return valid fixed envelopes, create the production server through `create_server(service_factory=lambda: FakeService())`, and run it with stdio. It must import no credentials and make no network calls.

### Step 2: Write the production handshake/list-tools test

Use the official SDK client:

```python
async def inspect_production_server():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fitbit_health.mcp_server"],
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
```

This test must stop after `list_tools`; it must not call a production tool or read the real token.

### Step 3: Write the mock subprocess tool-call test

Start `tests/fixture_mcp_server.py` with the same official client, initialize, list tools, then call:

```python
result = await session.call_tool("get_steps", {"days": 3})
assert result.isError is False
payload = result.structuredContent
assert payload["requested_days"] == 3
assert set(payload) == {
    "requested_days", "available_days", "data", "missing_data", "diagnostics"
}
```

If the SDK version wraps structured values under a stable result key, centralize extraction in one test helper and additionally verify text content parses as the same JSON object.

### Step 4: Run stdio integration tests

Run:

```powershell
python -m pytest tests/test_mcp_stdio.py -q -s
```

Expected: both subprocess tests pass: production `initialize/list_tools` and fixture `initialize/list_tools/call_tool`.

### Step 5: Run the complete test suite

Run:

```powershell
python -m pytest -q
python -m compileall -q src tests
```

Expected: all tests pass and compilation returns exit code 0.

### Step 6: Commit

```powershell
git add tests/fixture_mcp_server.py tests/test_mcp_stdio.py
git commit -m "test: verify MCP stdio handshake and tool call"
```

---

## Task 5: Document local ChatGPT/Codex connection and perform privacy verification

**Files:**
- Modify: `README.md`

### Step 1: Add local MCP usage documentation

Document:

- Install/update: `python -m pip install -e ".[test]"`.
- One-time interactive authorization outside MCP: `python -m fitbit_health sync --days 1`.
- Start commands: `fitbit-health-mcp` and `python -m fitbit_health.mcp_server`.
- Six tool names and their structured response envelope.
- Authentication recovery instructions.
- Explicit statement that MCP is local stdio only and exposes no HTTP endpoint.

Add a Codex configuration example using absolute paths and no secrets:

```toml
[mcp_servers.fitbit_health]
command = "D:\\anaconda\\python.exe"
args = ["-m", "fitbit_health.mcp_server"]
cwd = "E:\\CodeX_Lab"
```

Note that `command` must be replaced by the exact output of `(Get-Command python).Source` if different. For ChatGPT/Codex clients that use JSON configuration, include the equivalent:

```json
{
  "mcpServers": {
    "fitbit_health": {
      "command": "D:\\anaconda\\python.exe",
      "args": ["-m", "fitbit_health.mcp_server"],
      "cwd": "E:\\CodeX_Lab"
    }
  }
}
```

### Step 2: Install the editable package with MCP dependency

Run:

```powershell
python -m pip install -e ".[test]"
```

Expected: official `mcp` v1 installs and the `fitbit-health-mcp` console command resolves.

### Step 3: Execute final verification

Run:

```powershell
python -m pytest -q
python -m pytest tests/test_mcp_stdio.py -q -s
python -m compileall -q src tests
Get-Command fitbit-health-mcp
git status --short
git diff --check
git check-ignore .private/token.json reports/health_data.json reports/health_analysis.json reports/health_report.md
git ls-files | Select-String -Pattern 'client_secret|token.json|health_data.json|health_analysis.json|health_report.md'
```

Expected:

- Full unit suite passes.
- MCP stdio initialize/list-tools tests pass.
- At least one mock tool call passes.
- Console command is installed.
- No whitespace errors.
- Token, credentials, and generated reports remain ignored/untracked.
- `git ls-files` finds no sensitive or generated health files.

### Step 4: Review the final diff without merging

Run:

```powershell
git status --short
git diff --stat HEAD~4..HEAD
git log --oneline -6
```

Confirm only MCP implementation, tests, dependency metadata, authentication reuse, README, and approved planning documents changed. Do not merge, push, or modify the existing report-rendering logic.

### Step 5: Commit documentation

```powershell
git add README.md
git commit -m "docs: explain local Fitbit Health MCP setup"
```

---

## Final Report Checklist

Report only after the verification commands have actually run:

- Current branch and confirmation that nothing was merged.
- Exact modified/created files.
- New `fitbit-health-mcp` command plus module equivalent.
- Six MCP tool names.
- Test commands and exact pass counts/results.
- Production initialize/list-tools result and mock call result.
- Any remaining issue or required manual action.
- Sanitized ChatGPT/Codex stdio configuration example with the verified Python path.
