# HexStrike AI 环境变量配置说明

## 任务执行配置

### 空闲超时 (IDLE_TIMEOUT)
- **默认值**: 300 秒 (5 分钟)
- **说明**: 如果工具在指定时间内没有产生任何输出，自动终止任务
- **用途**: 防止工具卡住导致任务永久运行

```bash
# Linux/macOS
export IDLE_TIMEOUT=600  # 10分钟无输出则终止

# Windows
set IDLE_TIMEOUT=600
```

### Docker 配置
```yaml
# docker-compose.yml
backend:
  environment:
    IDLE_TIMEOUT: 600
```

## 任务池配置

### 最大并发数 (MAX_WORKERS)
- **默认值**: 3
- **说明**: 同时运行的最大任务数

```bash
export MAX_WORKERS=5
```

### 健康检测配置

#### 自动健康检测 (AUTO_HEALTH_CHECK)
- **默认值**: true
- **说明**: 是否启用自动健康检测

#### 检测间隔 (HEALTH_CHECK_INTERVAL)
- **默认值**: 300 秒 (5 分钟)
- **说明**: 自动健康检测的时间间隔

#### 检测超时 (HEALTH_CHECK_TIMEOUT)
- **默认值**: 30 秒
- **说明**: 单个工具健康检测的超时时间

## 配置示例

### 开发环境
```bash
# .env.development
FLASK_ENV=development
MAX_WORKERS=1
IDLE_TIMEOUT=120  # 2分钟
AUTO_HEALTH_CHECK=false
```

### 生产环境
```bash
# .env.production
FLASK_ENV=production
MAX_WORKERS=5
IDLE_TIMEOUT=600  # 10分钟
AUTO_HEALTH_CHECK=true
HEALTH_CHECK_INTERVAL=300
HEALTH_CHECK_TIMEOUT=30
```

## 注意事项

1. **空闲超时 vs 绝对超时**
   - 空闲超时：基于工具是否产生输出
   - 绝对超时：基于任务开始时间（已移除）
   
2. **推荐配置**
   - 快速工具 (nmap, gobuster): 120-300 秒
   - 慢速工具 (nikto, sqlmap): 300-600 秒
   - 深度扫描 (nuclei, wpscan): 600-1800 秒

3. **手动取消**
   - 前端提供"取消"按钮，可随时终止运行中的任务
   - 取消会同时终止进程及其子进程

4. **进程清理**
   - Windows: 使用 `terminate()` 终止进程
   - Linux/macOS: 使用进程组 (`killpg`) 终止所有子进程
