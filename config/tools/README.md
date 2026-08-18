# 工具配置目录

## 概述

`config/tools/` 目录包含所有安全工具的 YAML 配置文件。CommandBuilder 在启动时自动加载这些文件，用于构建命令行工具调用。

## 文件结构

```
config/tools/
├── _schema.json          # YAML 配置的 JSON Schema 验证
├── _base.yaml            # 全局默认配置（安全设置、最大参数数等）
├── nmap.yaml             # 各个工具的参数定义
├── httpx.yaml
├── ...
└── (150+ 个工具配置文件)
```

## YAML 配置格式

### 基础字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool` | string | ✅ | 工具标识符（必须与文件名一致） |
| `binary` | string | ✅ | 可执行文件名 |
| `target_placement` | enum | ❌ | 目标放置模式：`flag` \| `positional` \| `stdin_pipe` \| `trailing`（默认 `flag`） |
| `target_flag` | string | 条件 | 目标的 CLI 标志（`target_placement=flag` 时必填） |
| `pipe_command` | string | 条件 | stdin 管道命令模板（`target_placement=stdin_pipe` 时必填，使用 `{target}` 占位符） |
| `parameter_order` | array | ❌ | 参数顺序（用于需要特定参数顺序的工具，如 hydra） |
| `parameters` | object | ❌ | 参数定义映射 |
| `default_params` | object | ❌ | 默认参数值 |

### 参数定义

每个参数可以定义以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `flag` | string | 条件 | CLI 标志（如 `-e`, `--threads`），`boolean` 类型必填 |
| `type` | enum | ❌ | 参数类型：`string`（默认）\| `integer` \| `boolean` \| `positional` |
| `required` | boolean | ❌ | 是否必填（默认 `false`） |
| `default` | any | ❌ | 默认值 |
| `validators` | object | ❌ | 验证规则 |

### 验证规则

```yaml
validators:
  min: 1              # 整数最小值
  max: 100            # 整数最大值
  pattern: "^[a-z]+$" # 字符串正则匹配
```

## 目标放置模式

### 1. flag 模式（默认）

目标通过 CLI 标志传递：

```yaml
tool: dirsearch
binary: dirsearch
target_placement: flag
target_flag: -u
```

生成命令：`dirsearch -u https://example.com ...`

### 2. positional 模式

目标作为位置参数传递：

```yaml
tool: httpx
binary: httpx
target_placement: positional
```

生成命令：`httpx https://example.com ...`

### 3. trailing 模式

目标放在命令末尾（在所有参数之后）：

```yaml
tool: hydra
binary: hydra
target_placement: trailing
parameter_order: [username, wordlist, target, service]
```

生成命令：`hydra -l admin -P pass.txt ssh target_host`

### 4. stdin_pipe 模式

目标通过 stdin 管道传递：

```yaml
tool: waybackurls
binary: bash
target_placement: stdin_pipe
pipe_command: 'echo "{target}" | waybackurls'
```

生成命令：`bash -c 'echo "example.com" | waybackurls'`

## 参数类型

### string（默认）

```yaml
extensions:
  flag: -e
  type: string
```

输入：`{"extensions": "php,html"}`
输出：`-e php,html`

### integer

```yaml
threads:
  flag: -t
  type: integer
  validators:
    min: 1
    max: 100
```

输入：`{"threads": 10}`
输出：`-t 10`

### boolean

```yaml
recursive:
  flag: -r
  type: boolean
```

输入：`{"recursive": true}` → 输出：`-r`
输入：`{"recursive": false}` → 输出：（无）

### positional（位置参数）

```yaml
mode:
  type: positional
  default: dir
```

输入：`{"mode": "vhost"}` → 输出：`vhost`（无标志）

## 安全机制

1. **命令注入防护**：`_sanitize()` 自动检测并拒绝包含危险字符的值
2. **参数白名单**：只允许 YAML 中声明的参数名
3. **类型验证**：Pydantic 运行时类型检查
4. **长度限制**：命令最大长度 8192 字符，最多 50 个参数
5. **shell=False**：始终使用列表构建命令，从不使用 `shell=True`

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tools/command-builder/status` | GET | 获取 CommandBuilder 状态 |
| `/api/tools/command-builder/reload` | POST | 热重载配置 |
| `/api/tools/command-builder/tools` | GET | 获取已配置工具列表 |
| `/api/tools/command-builder/tool/<name>` | GET | 获取单个工具配置 |

## 新增工具

1. 在 `config/tools/` 创建 `<tool_name>.yaml`
2. 定义工具配置（参考现有文件）
3. 调用 `POST /api/tools/command-builder/reload` 热重载
4. 无需重启 Worker！

## 验证配置

```bash
# 测试加载所有配置
python -c "from app.services.command_builder import CommandBuilder; CommandBuilder.initialize(); print(CommandBuilder.load_all())"

# 测试单个工具命令构建
python -c "
from app.services.command_builder import CommandBuilder
CommandBuilder.initialize()
CommandBuilder.load_all()
cmd = CommandBuilder.build('dirsearch', 'https://example.com', {'extensions': 'php', 'threads': 10})
print(' '.join(cmd))
"
```
