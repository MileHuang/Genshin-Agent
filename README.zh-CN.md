# 个人规划 Agent

[English](README.md) | [中文](README.zh-CN.md)

一个能够从用户安排方式中学习的个人规划助手，用于日常日程与旅行规划。系统不仅记录用户说了什么，还会记录用户对计划的接受、移动、删除和跳过操作，并据此改进后续计划。

## 项目状态

仓库包含第一版 MVP 的后端核心：基于 OpenAI Agents SDK 的规划 Agent、通过 Moonshot OpenAI 兼容 API 使用的 Kimi K3、模拟日历/待办/偏好工具、Open-Meteo 天气服务、确定性冲突校验器和离线测试。真实日历集成、前端和长期偏好学习将在后续迭代实现。

## MVP 目标

首个可用演示支持以下流程：

1. 用户用自然语言描述当天目标。
2. Agent 读取模拟日历中的固定事件。
3. Agent 读取模拟待办事项和用户偏好。
4. 目标涉及旅行或户外活动时，Agent 读取天气。
5. Agent 生成结构化日计划草稿。
6. Python 的确定性校验器检查时间冲突。
7. 命令行显示草稿供用户审阅。

此里程碑不包含自动预订、支付、多 Agent 交接、向量数据库或直接写入真实日历。

## 核心流程

```text
用户请求
    -> OpenAI Agents SDK Runner
    -> Personal Planner Agent
    -> 日历 / 待办 / 偏好 / 天气工具
    -> 结构化计划
    -> Python 冲突校验
    -> 用户审阅和编辑
    -> 编辑事件存储
    -> 偏好更新
    -> 更好的后续计划
```

OpenAI Agents SDK 负责 Agent 循环、模型调用、函数工具调用和结构化输出。Python 代码负责时间冲突、时长、校验及数据库写入等硬性约束。

## 规划架构

```text
Streamlit 前端
        |
        v
FastAPI 后端
        |
        v
OpenAI Agents SDK Runner
        |
        v
Personal Planner Agent
   |         |         |
   v         v         v
日历工具    记忆工具    调度校验器
   |         |         |
   v         v         v
Google      SQLite     Python 规则
Calendar    数据库
```

日历写入属于有副作用的操作。系统必须先生成草稿，只有在用户明确确认后才能写入真实日历。

## 技术栈

- Python 3.11+
- OpenAI Agents SDK：Agent 编排、工具调用和结构化输出
- Kimi K3：通过 Moonshot OpenAI 兼容 Chat Completions API 调用
- Open-Meteo：地点解析和每日天气预报
- OpenAI Python SDK：底层兼容 API 客户端
- HTTPX：可注入的离线 transport 与在线天气请求
- Pydantic：结构化输入和输出
- pytest：测试

FastAPI、Streamlit 和 SQLite 是后续阶段的计划。

## 项目结构

```text
Genshin-Agent/
|-- planner_agents/
|   `-- planner_agent.py
|-- config/
|   `-- settings.py
|-- tools/
|   |-- calendar_tool.py
|   |-- todo_tool.py
|   |-- weather_tool.py
|   |-- preference_tool.py
|   `-- validator_tool.py
|-- tests/
|-- main.py
|-- requirements.txt
|-- README.md
`-- README.zh-CN.md
```

## 本地安装（Windows PowerShell）

### 1. 获取项目

```powershell
git clone <repository-url>
cd Genshin-Agent
```

### 2. 创建虚拟环境

```powershell
py -3.11 -m venv .venv
```

### 3. 激活环境

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. 安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

核心依赖包括：

```text
httpx
openai
openai-agents
pydantic
python-dotenv
pytest
```

### 5. 配置环境变量

复制 `.env.example` 为 `.env`，并填入自己的 API Key：

```powershell
Copy-Item .env.example .env
```

```env
MOONSHOT_API_KEY=replace_with_your_moonshot_api_key
KIMI_MODEL=kimi-k3
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_REASONING_EFFORT=max
WEATHER_PROVIDER=open-meteo
WEATHER_TIMEOUT_SECONDS=20
```

Open-Meteo 不需要 API Key。完全离线开发时，可设置 `WEATHER_PROVIDER=mock`。请勿提交 `.env` 或 API Key。

## 使用方法

首先运行完全离线、确定性的 MVP 演示：

```powershell
python main.py --demo --date 2026-08-07 --location "Madison, WI"
```

配置 `MOONSHOT_API_KEY` 后，可运行真实 Kimi 规划：

```powershell
python main.py --goal "安排两小时深度学习和一次户外跑步" --date 2026-08-07 --location "Madison, WI"
```

命令会输出通过校验的 JSON `DailyPlan`。MVP 输入参数为 `--goal`、`--date` 和 `--location`；`--demo` 会在不调用外部模型的情况下，完整运行日历、待办、偏好、天气路由和冲突校验流程。

运行测试：

```powershell
python -m pytest -q
```

## 数据契约

规划模块定义了 `PlanItem`、`PlanValidation` 和 `DailyPlan` Pydantic 模型。应用未来还会包含：

- `Task`
- `CalendarEvent`
- `EditEvent`
- `UserPreference`

所有层应共享这些契约：工具返回结构化日历和偏好数据；Agent 返回结构化计划；校验器校验同一计划；前端渲染同一计划；数据库保存计划及后续编辑。

## 工具提供者

当前规划器使用：

- `get_calendar_events(date)`
- `get_todos()`
- `get_user_preferences()`
- `get_weather(location, date)`
- `validate_schedule(schedule)`

日历、待办和偏好数据为模拟数据。天气默认使用 Open-Meteo，并提供可重复的模拟实现用于离线测试。提供者接口允许日后替换为真实 API，而无需修改规划器接口。

## 记忆设计

系统区分三类记忆：

1. 对话记忆：多轮对话所需的近期消息。
2. 档案记忆：作息、偏好开始时间、每日最大活动数等相对稳定的偏好。
3. 轨迹记忆：移动、删除、接受或跳过计划项等原始证据。

长期偏好不应因为一次编辑就被覆盖。偏好更新器应保存证据数量、置信度和最近更新时间，并在证据充分时更新偏好。

## 第一阶段验收标准

- [x] 可从命令行提交自然语言请求。
- [x] Agent 读取模拟日历、待办和偏好上下文。
- [x] Agent 返回经过校验的 `DailyPlan` 对象。
- [x] 校验器可发现重叠事件。
- [x] 有效计划不与现有事件重叠。
- [x] 终端输出格式化计划 JSON。
- [x] 测试覆盖有效计划、冲突计划和全部模拟工具。

## 路线图

### 阶段 1：日规划垂直切片

- 定义数据模型。
- 实现模拟工具。
- 创建一个规划 Agent。
- 增加确定性冲突校验。
- 显示计划草稿。

### 阶段 2：编辑与记忆

- 使用 SQLite 保存计划。
- 记录移动、删除、接受和跳过事件。
- 实现初始偏好更新规则。
- 在后续计划中使用更新后的偏好。

### 阶段 3：真实日历集成

- 配置 Google Calendar OAuth。
- 读取真实日历事件。
- 日历写入前要求明确确认。
- 同步已确认计划。

### 阶段 4：旅行规划

- 搜索候选地点和活动。
- 复用日规划偏好。
- 校验开放时间、通勤时间、节奏与休息。
- 在同一记忆系统中记录旅行计划的编辑。

## 团队协作

建议使用短生命周期的功能分支和 Pull Request：

```text
main
|-- feature/agent
|-- feature/scheduler
`-- feature/frontend
```

1. 从最新 `main` 创建分支。
2. 实现一个小且可测试的改动。
3. 使用清晰的提交信息提交。
4. 推送分支并创建 Pull Request。
5. 至少请求一位成员审阅。
6. 所有测试通过后再合并。

不要提交 API Key、OAuth Token、`.env`、`.venv` 或本地数据库文件。

## 项目成功定义

项目应展示的不只是计划生成：

> 用户编辑日计划后，系统会存储该编辑的证据；之后的日计划或旅行计划会因为学到的偏好，产生可观察且可解释的变化。

## 贡献者

- 成员 1：Agent 与工具
- 成员 2：调度、记忆与评估

## 许可证

如果项目需要公开，请补充许可证。私有课程项目请遵循课程或学校要求。
