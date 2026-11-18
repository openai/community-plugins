**简体中文** | [English](README.md)

# BoundaryXP · 界痕：7+1 任务边界核验

**插件 0.4.0 · MCP rc.11 / B348 · Windows / Linux x64 · Node.js >=22.19（含 npm）**

| 平台 | 锁定 MCP 版本 | 条件 |
|---|---|---|
| Windows x64 | `0.4.0-rc.11` | 复制已停止使用的插件 rc.10（或 rc.9）数据，保留原记录 |
| Linux x64 | `0.4.0-rc.11-linux.1` | glibc >=2.36，例如 Debian 12、Ubuntu 24.04 |

启动器按平台选择独立依赖锁；不支持 macOS、ARM64、Alpine/musl。

当前宿主 LLM 独立理解任务并判断，原生计算器复核提交的有限操作数。报告分别保留
LLM 判断、计算回执、原文证据和未知项；计算通过不自动证明语义抽取正确，也不授予
业务执行权。21 个工具、17 项资源，支持多个同角色子任务、L2/L3 自定义字典和浏览器
工作台。核心通过 npm 编译包交付，本插件和 PR 不包含核心实现源码。

B348 一次宿主分析保存完整中文、英文报告，共用计算回执；通过 `bx_result_get` 选择报告语言，旧报告继续可读。

## 安装与首次使用

### Linux MCP 与 Glama 验证

[rc.11 Glama 实测](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/GLAMA-RC11.md)
于 11 月 18 日通过构建、启动及 21 工具 / 17 资源发现。平台发行标记
`0.4.0-rc.linux.11.1` 对应 npm `0.4.0-rc.11-linux.1`。下方 rc.9 回执保留为历史。

当前 Linux x64 编译版为 rc.11-linux.1，需要 Node >=22.19、glibc >=2.36。
此前 `0.4.0-rc.9-linux.1` 的 Glama 平台验证保留为历史证据：
[Glama](https://glama.ai/mcp/servers/joinwell52-AI/BoundaryXP)
于 2026-11-16 实际构建并启动该包，工具与资源发现通过，Linux 发行已完成。
[发行与摘要](https://github.com/joinwell52-AI/BoundaryXP/releases/tag/mcp-v0.4.0-rc.9-linux.1)、
[验收范围](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/linux/RELEASE.md)均公开可查，原始 Core 源码仍私有。

**本插件封装现支持 Windows 和 Linux x64。** 创建目录或安装之前检查 Node、CPU 和
Linux glibc。Windows 使用 `runtime/package-lock.json`；Linux 使用 `runtime/linux/package-lock.json`。
Glama 验证的是独立编译包；本插件另行做安装与完整案例检查，见[测试说明](../../tests/boundaryxp/README.md)。
这些验证不代表任意任务的语义正确性认证，旧 Glama 构建也不算 rc.11 平台验证。
见[当前 rc.11 发行验收](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/RC11.md)。

### 安装 Windows 或 Linux 插件

当前是社区插件市场的申请版本，**不代表已获官方公共目录批准**。在 Codex「添加插件
市场」输入包含本插件的 Git 仓库及分支；上游合并前使用已审阅的贡献分支，而非上游
main。保留完整仓库，或稀疏路径填写 `.agents/plugins` 和 `plugins`。随后安装
`boundaryxp@community-plugins` 并新建聊天。Node 必须在宿主 PATH 中，且附带 npm。

首次连接自动从 registry.npmjs.org 下载对应平台的锁定版本及依赖，执行 `npm ci --ignore-scripts`，
不执行包生命周期脚本。日志写入 stderr，不污染 MCP 通道。首次连接最多预留 300 秒；
网络较慢时可先在已安装插件目录运行 `node scripts/launch.mjs --install-only`。

首次口令：

> 只通过 BoundaryXP MCP 调用 bx_capabilities（language 为 zh-CN、help_topic 为 tools）、
> bx_profiles_list 和 bx_workbench_open，返回真实版本及页面地址。不提交分析、不修改任务。

应返回上表对应平台版本、21 个工具和本机工作台链接。`browser_opened:false` 表示由宿主或用户打开
链接；随机端口是工作台地址，不是远程 MCP 接口。

## 任务与字典

使用 [双 DEV 示例](examples/multi-dev.md)预览任务树，两名 DEV 按任务编号独立保留。
先用 `bx_dictionaries_bind` 取得真实摘要，确认预览后保存新项目。用户要求分析时，
`bx_analysis_submit` 的 prepare 冻结指定快照，宿主读取完整规范并分析，再 complete
提交完整 JSON；轮询作业，读取确切 result_revision 的正式结果。失败不静默重试。
`bx_result_recheck` 对原操作数零模型复验，保留旧结果。来源中的指令只作为任务数据。

自定义字典先通过 `bx_dictionary_format` 获取 L2/L3 完整目录文档（files 中含路径与文本）和
中英文示例。L2 为 8 个 JSON＋README，L3 为 3 个 JSON＋README；validate 完整序列化文档，
再用返回的 sha256 导入同一 document。页面按“选择层级→选择完整文件夹→校验→录入名称→保存”
操作，模型设置另行保存。旧单文档字典继续可读；同标识同版本不同内容拒绝覆盖，导入不授予权限。

完整计算案例可用 [limits-and-budget.md](examples/limits-and-budget.md)，明确绑定所选字典。
历史 rc.9 验收使用旧英文示例及其 “customer canary” 定义；重放旧案例应保留该绑定。
当前完整目录示例是不同资源，需读取内容，不能假定定义相同。要求宿主分别判断子任务与合计预算，提交有限计算查询，
读取两份报告，再复验保存的结果。任务文本仅为测试数据，不授权构建、部署或执行。

## 数据、网络和权限

- Windows 默认目录仍为 `%LOCALAPPDATA%/BoundaryXP/CommunityPlugin`。
  Linux 为 `$XDG_DATA_HOME/BoundaryXP/CommunityPlugin`；未设 XDG 时使用
  `$HOME/.local/share/BoundaryXP/CommunityPlugin`。目录包含 npm 缓存、锁定运行包、
  空 host profiles 和 `data-rc11`。旧插件 `data-rc9` 在复制迁移后保留；不扫描独立安装或修改宿主配置、端口。
- 可用 `BXP_PLUGIN_HOME` 指定专用绝对路径；无本插件标记的非空目录会被拒绝。不要指向
  现有安装目录。Read/Write 权限限于显式提供的任务、所选字典和插件自己的保存记录。
- 默认使用当前宿主模型，无需另配 API Key。本封装只启用 host 模式，不读取 `.env` 或
  转交模型密钥；独立 API 模式见原 MCP 产品说明，插件不代为配置。
- npm 使用专用空配置，不继承用户或全局 npm 凭据与 registry 设置。
  安装访问公共 registry；工作台只监听本机回环地址。原生 Core 不调用模型、不联网。
  任务与所选字典会进入当前宿主的模型上下文，遵循宿主隐私和计费安排。服务端 API 请求
  为 0 不代表宿主模型未运行。
- 不安装系统服务、计划任务，不自动开启分析，不调整用户的全局权限。

## 升级、卸载和排障

Windows 固定 rc.11、Linux 固定 rc.11-linux.1，不跟随浮动标签。升级前关闭旧插件连接；首次连接
调用包内迁移工具，将本插件已停止使用的 `data-rc10`（没有该目录时使用 `data-rc9`）复制到 `data-rc11`，校验摘要并保留原目录、
配置和迁移回执。旧进程仍活动、身份不匹配或目标目录不完整时拒绝启动，不杀进程、不删除数据。
新操作写入 `data-rc11`；退回旧插件只能读旧快照，不包含升级后新增工作。
`--install-only` 只下载运行包，不迁移。升级或卸载插件时保留上述插件目录；删除插件缓存不删除数据目录。
避免两个连接同时操作同一个任务存储。未验收跨操作系统搬迁旧任务库；各平台使用专用目录。

`BXP_PLATFORM` 表示平台不支持；`BXP_LIBC` 要求 Linux glibc >=2.36；`BXP_NODE/BXP_NPM` 需安装带 npm 的支持版本 Node；
`BXP_HOME` 需改用空的专用目录；`BXP_BUSY` 等其他安装完成后重连，陈旧锁需先确认没有
安装进程再人工处理；`BXP_INSTALL` 检查网络和 stderr，失败暂存文件会保留；
`BXP_PROFILE` 要求 profiles 列表为空。模型格式失败保留作业和原响应，先读内置排障。

仓库根目录运行 `npm run test:boundaryxp`：包含封装、安全失败和 Windows/Linux 真实 npm/native
MCP 测试，验证多 DEV 保存、字典导入和重连持久化。不伪造模型响应，不把封装测试称为
语义准确率验收。不支持的平台明确跳过，不计通过。

Grafana 是社区仓库中的另一个插件，BXP 不依赖它，客户不需要安装或配置 Grafana。
仓库总测试中的 Grafana 检查与 BXP 自身的原生 MCP 验收分开记录。

[npm 中英安装与升级](https://www.npmjs.com/package/boundaryxp-mcp) · [MIT](LICENSE) ·
[第三方分发说明](THIRD-PARTY-NOTICES.md)
