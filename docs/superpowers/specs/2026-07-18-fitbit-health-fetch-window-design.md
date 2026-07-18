# Fitbit Health 抓取时间四档设计

## 目标

将 Fitbit Health 的数据抓取时间统一限制为四个离散档位：1、3、7、14 天。14 天是最大抓取范围，默认值继续使用 7 天。

## 范围

该规则同时应用于：

- `python -m fitbit_health sync --days ...` 命令行入口；
- `run_sync` 数据管线公共接口；
- 六个 Fitbit Health MCP tools 的 `days` 参数；
- MCP tool 输入 schema、验证诊断、测试和 README 使用说明。

不修改 OAuth、Google Health API 请求类型、标准化逻辑、分析算法、MCP transport 或 Codex 配置。

## 设计

定义一个公共允许值常量，按用户希望的展示优先级排列为 `14、7、3、1`，并提供统一验证函数。所有调用入口复用该规则，避免 CLI、pipeline 和 MCP 产生不同的允许范围。

对外行为如下：

- 默认 `days=7` 保持不变；
- 合法值仅为 `1、3、7、14`；
- `2、5、10、15、365` 等其他整数均不合法；
- 布尔值和非整数继续视为不合法；
- CLI 使用 argparse 的离散 choices 拒绝非法值；
- pipeline 被直接调用且收到非法值时抛出明确的 `ValueError`；
- MCP 收到非法值时不抛出未处理异常，仍返回标准 envelope，并在 `diagnostics.validation` 中说明只允许 1、3、7、14 天；
- MCP schema 将 `days` 暴露为整数 enum，使 Codex 能直接识别四个支持档位。

## 返回兼容性

合法请求的 MCP 顶层 JSON schema 不变：

- `requested_days`
- `available_days`
- `data`
- `missing_data`
- `diagnostics`

现有六个工具名称、默认参数和真实 Google Health 数据结构保持不变。

## 测试

按 TDD 执行：

1. 先增加会失败的测试，覆盖四个合法档位以及 2、5、10、15、365、布尔值和字符串等非法输入。
2. 验证 CLI choices、pipeline 校验、MCP validation JSON 和 MCP tool schema enum。
3. 实施最小公共规则使测试通过。
4. 运行全部单元测试和 MCP stdio 握手测试，确认六个工具仍可发现。

真实 Google Health 调用不作为自动化测试依赖，避免测试触发 OAuth 或外部网络。
