# Fitbit Health Four-Tier Fetch Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict Fitbit Health CLI, pipeline, and MCP fetch windows to exactly 1, 3, 7, or 14 days, with 14 days as the maximum and 7 days as the default.

**Architecture:** Add one focused fetch-window policy module and reuse it from the CLI, pipeline, MCP service, and MCP tool annotations. Preserve the existing MCP response envelope and Google Health data flow while exposing the four supported values as an MCP JSON-schema enum.

**Tech Stack:** Python 3.12, argparse, typing.Literal, FastMCP/MCP Python SDK, pytest.

## Global Constraints

- Supported fetch windows are exactly `1`, `3`, `7`, and `14` days.
- Present the supported values in priority order `14`, `7`, `3`, `1`.
- The maximum fetch window is 14 days.
- The default remains 7 days.
- Do not modify OAuth, Google Health data types, normalization, analytics, MCP transport, or Codex configuration.
- Invalid MCP input must return the existing structured envelope with `diagnostics.validation`; it must not escape as an unhandled exception.
- Automated tests must not perform real OAuth or Google Health network calls.

---

## File Structure

- Create `src/fitbit_health/fetch_window.py`: own the four allowed values, the MCP `Literal` type, the default, and validation/message helpers.
- Modify `src/fitbit_health/__main__.py`: expose the four argparse choices and default to 7.
- Modify `src/fitbit_health/pipeline.py`: enforce the shared policy for direct callers.
- Modify `src/fitbit_health/mcp_tools.py`: reuse the shared validation rule and diagnostic message.
- Modify `src/fitbit_health/mcp_server.py`: annotate all six `days` inputs with the shared `Literal` so FastMCP emits an enum.
- Modify `tests/test_cli.py`, `tests/test_pipeline.py`, `tests/test_mcp_tools.py`, and `tests/test_mcp_server.py`: prove the new behavior before implementation.
- Modify `README.md`: replace the 30-day example and document the supported four tiers.

### Task 1: Shared Policy, CLI, and Pipeline

**Files:**
- Create: `src/fitbit_health/fetch_window.py`
- Modify: `src/fitbit_health/__main__.py`
- Modify: `src/fitbit_health/pipeline.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `FetchDays = Literal[14, 7, 3, 1]`
- Produces: `ALLOWED_FETCH_DAYS: tuple[int, ...] = (14, 7, 3, 1)`
- Produces: `DEFAULT_FETCH_DAYS = 7`
- Produces: `FETCH_DAYS_ERROR = "days must be one of 14, 7, 3, or 1."`
- Produces: `is_allowed_fetch_days(value: object) -> bool`
- Consumes: existing `run_sync(root: Path, days: int, ...)` interface without changing its signature.

- [ ] **Step 1: Write failing CLI tests**

Update `tests/test_cli.py` so successful examples use a supported value and verify the default and discrete choices:

```python
@pytest.mark.parametrize("days", ["14", "7", "3", "1"])
def test_cli_accepts_supported_fetch_windows(monkeypatch, tmp_path: Path, days: str) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    run_sync = Mock(return_value=outputs)
    monkeypatch.setattr(cli, "run_sync", run_sync)

    assert cli.main(["sync", "--days", days]) == 0
    run_sync.assert_called_once_with(Path.cwd(), int(days))


def test_cli_defaults_to_seven_days(monkeypatch, tmp_path: Path) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    run_sync = Mock(return_value=outputs)
    monkeypatch.setattr(cli, "run_sync", run_sync)

    assert cli.main(["sync"]) == 0
    run_sync.assert_called_once_with(Path.cwd(), 7)


@pytest.mark.parametrize("days", ["2", "5", "10", "15", "365"])
def test_cli_rejects_unsupported_fetch_windows(days: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["sync", "--days", days])

    assert error.value.code == 2
```

Add `from unittest.mock import Mock` and change the existing successful `--days 30` invocation to `--days 14`.

- [ ] **Step 2: Write failing pipeline tests**

Update `tests/test_pipeline.py` to replace 30-day calls with 14-day calls, change the expected start date for 2026-07-18 to 2026-07-05, and add:

```python
@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_pipeline_accepts_supported_fetch_windows(tmp_path: Path, days: int) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = lambda data_type, start: FetchResult(data_type, [])

    paths = run_sync(
        tmp_path,
        days=days,
        today=date(2026, 7, 18),
        client=fake_client,
    )

    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("days", [0, 2, 5, 10, 15, 365, True, "7"])
def test_pipeline_rejects_unsupported_fetch_windows(tmp_path: Path, days) -> None:
    with pytest.raises(ValueError, match="14, 7, 3, or 1"):
        run_sync(tmp_path, days=days, client=Mock())
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
D:\anaconda\python.exe -m pytest tests/test_cli.py tests/test_pipeline.py -q
```

Expected: FAIL because the CLI still defaults to 30 and accepts any value from 1 through 365, while the pipeline still accepts unsupported values such as 2 and 10.

- [ ] **Step 4: Add the shared fetch-window policy**

Create `src/fitbit_health/fetch_window.py`:

```python
from typing import Literal, TypeAlias


FetchDays: TypeAlias = Literal[14, 7, 3, 1]
ALLOWED_FETCH_DAYS: tuple[int, ...] = (14, 7, 3, 1)
DEFAULT_FETCH_DAYS: FetchDays = 7
FETCH_DAYS_ERROR = "days must be one of 14, 7, 3, or 1."


def is_allowed_fetch_days(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value in ALLOWED_FETCH_DAYS
    )
```

- [ ] **Step 5: Apply the shared policy to CLI and pipeline**

In `src/fitbit_health/__main__.py`, import `ALLOWED_FETCH_DAYS` and `DEFAULT_FETCH_DAYS`, then configure:

```python
sync.add_argument(
    "--days",
    type=int,
    default=DEFAULT_FETCH_DAYS,
    choices=ALLOWED_FETCH_DAYS,
    metavar="{14,7,3,1}",
    help="同步天数，可选 14、7、3、1，默认 7",
)
```

In `src/fitbit_health/pipeline.py`, import `FETCH_DAYS_ERROR` and `is_allowed_fetch_days`, then replace the range check with:

```python
if not is_allowed_fetch_days(days):
    raise ValueError(FETCH_DAYS_ERROR)
```

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
D:\anaconda\python.exe -m pytest tests/test_cli.py tests/test_pipeline.py -q
```

Expected: all CLI and pipeline tests PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add src/fitbit_health/fetch_window.py src/fitbit_health/__main__.py src/fitbit_health/pipeline.py tests/test_cli.py tests/test_pipeline.py
git commit -m "feat: restrict Fitbit fetch windows"
```

### Task 2: MCP Validation and Tool Schema

**Files:**
- Modify: `src/fitbit_health/mcp_tools.py`
- Modify: `src/fitbit_health/mcp_server.py`
- Test: `tests/test_mcp_tools.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `FetchDays`, `FETCH_DAYS_ERROR`, and `is_allowed_fetch_days` from `fitbit_health.fetch_window`.
- Preserves: all six tool names and the existing `ToolEnvelope` fields.
- Produces: an MCP input schema where `days.type == "integer"`, `days.default == 7`, and `days.enum == [14, 7, 3, 1]`.

- [ ] **Step 1: Write failing MCP service validation tests**

In `tests/test_mcp_tools.py`, replace the current invalid-days parameter set with:

```python
@pytest.mark.parametrize("days", [0, 2, 5, 10, 15, 365, "seven", True])
def test_invalid_days_returns_validation_diagnostic_without_client_call(days) -> None:
    service, fake_client = make_service()

    result = service.get_steps(days=days)

    assert set(result) == ENVELOPE_FIELDS
    assert result["available_days"] == 0
    assert result["data"] == []
    assert result["diagnostics"] == {
        "validation": "days must be one of 14, 7, 3, or 1."
    }
    assert fake_client is not None
    fake_client.fetch_all.assert_not_called()
```

Add a supported-tier test:

```python
@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_metric_tools_accept_all_supported_fetch_windows(days: int) -> None:
    service, fake_client = make_service({"steps": FetchResult("steps", [])})

    result = service.get_steps(days=days)

    assert result["requested_days"] == days
    assert fake_client is not None
    fake_client.fetch_all.assert_called_once()
```

- [ ] **Step 2: Write the failing MCP schema test**

In `tests/test_mcp_server.py`, extend the existing loop assertion:

```python
assert days_schema["type"] == "integer"
assert days_schema["default"] == 7
assert days_schema["enum"] == [14, 7, 3, 1]
```

- [ ] **Step 3: Run focused MCP tests and verify RED**

Run:

```powershell
D:\anaconda\python.exe -m pytest tests/test_mcp_tools.py tests/test_mcp_server.py -q
```

Expected: FAIL because unsupported integers are still accepted and the generated MCP schema has no four-value enum.

- [ ] **Step 4: Reuse the policy in the MCP service**

In `src/fitbit_health/mcp_tools.py`, import `FETCH_DAYS_ERROR` and `is_allowed_fetch_days`. Replace `_valid_days` with:

```python
@staticmethod
def _valid_days(days: Any) -> bool:
    return is_allowed_fetch_days(days)
```

Replace both existing range validation dictionaries with:

```python
{"validation": FETCH_DAYS_ERROR}
```

- [ ] **Step 5: Expose the four tiers in all MCP tool schemas**

In `src/fitbit_health/mcp_server.py`, import `FetchDays` and `DEFAULT_FETCH_DAYS`, then change all six registered tool signatures to this pattern:

```python
@server.tool(structured_output=True)
def get_sleep(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
    return get_service().get_sleep(days)
```

Apply the same `days: FetchDays = DEFAULT_FETCH_DAYS` annotation to `get_steps`, `get_heart_rate`, `get_resting_heart_rate`, `get_hrv`, and `get_health_summary`, preserving their existing descriptions and delegation.

- [ ] **Step 6: Run focused MCP tests and verify GREEN**

Run:

```powershell
D:\anaconda\python.exe -m pytest tests/test_mcp_tools.py tests/test_mcp_server.py -q
```

Expected: all MCP service and schema tests PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/fitbit_health/mcp_tools.py src/fitbit_health/mcp_server.py tests/test_mcp_tools.py tests/test_mcp_server.py
git commit -m "feat: expose Fitbit fetch tiers to MCP"
```

### Task 3: Documentation and End-to-End Regression

**Files:**
- Modify: `README.md`
- Verify: `tests/test_mcp_stdio.py`
- Verify: full `tests/` suite

**Interfaces:**
- Consumes: the completed CLI, pipeline, and MCP behavior from Tasks 1 and 2.
- Produces: user documentation matching the implemented four-tier contract.

- [ ] **Step 1: Update README examples and contract**

Replace the 30-day sync example with:

```powershell
python -m fitbit_health sync --days 14
```

Add this rule beside the CLI and MCP usage sections:

```markdown
`days` 只支持 `14`、`7`、`3`、`1` 四档；最大抓取范围为 14 天，默认值为 7 天。
```

Keep all six MCP tool names and their `days: int = 7` documentation unchanged except for adding the supported-value rule.

- [ ] **Step 2: Run the full unit test suite**

Run:

```powershell
D:\anaconda\python.exe -m pytest -q
```

Expected: all tests PASS with zero failures.

- [ ] **Step 3: Run the production MCP stdio handshake test**

Run:

```powershell
D:\anaconda\python.exe -m pytest tests/test_mcp_stdio.py::test_production_server_initializes_and_lists_six_tools_without_oauth -q
```

Expected: PASS; initialize succeeds and exactly six tools remain visible without performing OAuth.

- [ ] **Step 4: Inspect the generated tool schema directly**

Run:

```powershell
@'
import asyncio
from fitbit_health.mcp_server import create_server

async def main():
    tools = await create_server().list_tools()
    for tool in tools:
        days = tool.inputSchema["properties"]["days"]
        print(tool.name, days["default"], days["enum"])

asyncio.run(main())
'@ | D:\anaconda\python.exe -
```

Expected: six lines, each showing default `7` and enum `[14, 7, 3, 1]`.

- [ ] **Step 5: Confirm scope and commit documentation**

Run:

```powershell
git status --short
git diff --check
```

Expected: only the files named in this plan are changed and no whitespace errors are reported.

Commit:

```powershell
git add README.md
git commit -m "docs: document Fitbit fetch tiers"
```

