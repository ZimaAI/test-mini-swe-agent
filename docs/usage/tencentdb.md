# TencentDB 知识库对照评测

`tencentdb` 模型适配器调用 test-TencentDB 的
`/mini-swe-agent/<space_id>/v1/chat/completions` API。
默认关闭知识库；设为 `knowledge_enabled: true` 后，代理注入所选 Agent 绑定的
wiki / code-graph 工具说明，模型通过原有 bash 工具按需调用知识服务。

两组使用同一代理、模型、任务提示和工具格式。该 API 不写入会话记忆或提取技能，
适用于比较接入知识库与不接入知识库的任务效果。

## 准备

先在 MemoryPanel 中获取业务用户 Key、团队 ID 和该用户拥有的 Agent ID，
给 Agent 绑定知识库并等待处理完成。不要将 Key 写进 YAML 或提交到 Git。

PowerShell 中设置：

```powershell
$env:TDAI_MEMORY_KEY = '<MemoryPanel 业务用户 Key>'
$env:TDAI_TEAM_ID = '<team-id>'
$env:TDAI_AGENT_ID = '<agent-id>'
$env:TDAI_PROXY_URL = 'http://127.0.0.1:8096'
$env:TDAI_SPACE_ID = 'default'
$env:TDAI_KNOWLEDGE_URL = 'http://host.docker.internal:8424/v3'
```

`TDAI_PROXY_URL` 由运行 Python 的主机访问；`TDAI_KNOWLEDGE_URL` 由执行 bash 的环境访问。
后者适用于 Docker Desktop 的 SWE-bench 容器。在宿主机本地运行 bash 时可改成
`http://127.0.0.1:8424/v3`；远程环境需要可达的部署地址。不要把仅限 Compose 网络的
`memory-hub` 主机名用于独立评测容器。可先在目标容器中运行
`curl -f http://host.docker.internal:8424/health` 检查连通性。

## SWE-bench A/B

以下命令在 mini-swe-agent 仓库运行。将模型占位符改为代理实际支持的同一模型名称。
若已有模型参数 YAML，将它放在 `swebench.yaml` 之后、`tencentdb.yaml` 之前，
并用 `-m` 确保两组使用同一个模型。适配器覆盖原配置里的 API 地址和凭证。

```powershell
python -m minisweagent.run.benchmarks.swebench --subset lite --split test --slice 0:5 -w 1 -m 'openai/<模型名称>' -c swebench.yaml -c tencentdb.yaml -c model.knowledge_enabled=false -o results/no-kb
python -m minisweagent.run.benchmarks.swebench --subset lite --split test --slice 0:5 -w 1 -m 'openai/<模型名称>' -c swebench.yaml -c tencentdb.yaml -c model.knowledge_enabled=true -o results/with-kb
```

`tencentdb.yaml` 只是覆盖文件，必须同时加载基础配置。
默认 `cost_tracking: ignore_errors` 允许未登记价格的代理模型运行；这类模型的费用可能记为 0，
应保留 `step_limit`，并在比较费用前配置 LiteLLM 模型价格。
固定数据集、实例顺序、模型参数、知识库版本及环境；分别保存 `preds.json` 和轨迹，
之后使用 SWE-bench 的评测流程判断补丁是否解决问题。不要把未成功运行的实例视为模型失败。

## 配置和结果

| 字段 | 默认值 / 说明 |
| --- | --- |
| `model_class` | `tencentdb` |
| `knowledge_enabled` | `false`；由 YAML/CLI 显式控制，不受环境隐式切换 |
| `proxy_url` | `TDAI_PROXY_URL` 或 `http://127.0.0.1:8096` |
| `space_id` | `TDAI_SPACE_ID` 或 `default` |
| `team_id` / `agent_id` | 环境变量；启用知识库时必填 |
| `knowledge_url` | `TDAI_KNOWLEDGE_URL`；空值使用服务端资源登记地址 |
| `api_key_env` | 保存用户 Key 的环境变量名，默认 `TDAI_MEMORY_KEY` |
| `session_id` | 默认每个实例生成 UUID；同一模型重用于新任务时重新生成 |

显式 YAML 字段优先于环境变量。批量评测不要固定 `session_id`，仅恢复特定会话时设置它。
轨迹 `info.tencentdb` 记录实际会话 ID 和知识库开关；`info.config.model` 保存有效配置，
不保存环境中的 API Key。常规 `litellm` 模型类和未加载本覆盖文件的运行方式保持原有行为。

代理会对错误配置返回明确错误：400 表示请求参数不完整，401/403 表示鉴权或团队/Agent
不匹配，409 表示无可用绑定，503 表示知识服务不可用或未启用。知识库开启组不会静默退化成关闭组。
开启表示工具已提供给模型；是否实际调用及调用效果需要查看轨迹中的 bash 请求和知识服务日志。
