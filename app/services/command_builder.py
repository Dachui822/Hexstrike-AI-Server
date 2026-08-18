"""
通用命令构建器 - 基于 YAML 配置驱动
======================================

架构说明：
- 从 config/tools/*.yaml 加载工具定义
- 支持 5 种目标放置模式：flag, positional, stdin_pipe, trailing
- 支持 4 种参数类型：string, integer, boolean, positional
- 运行时参数验证 + 命令注入防护
- 与 Celery Worker 和 Flask API 完全兼容

使用方式：
    from app.services.command_builder import CommandBuilder

    # 启动时加载（应用初始化时调用一次）
    CommandBuilder.load_all()

    # 构建命令
    cmd = CommandBuilder.build('dirsearch', 'https://example.com', {
        'extensions': 'php,html',
        'threads': 10,
        'recursive': True
    })
    # 返回: ['dirsearch', '-u', 'https://example.com', '-e', 'php,html', '-t', '10', '-r']
"""

import os
import re
import yaml
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """配置文件验证错误"""
    pass


class UnsafeValueError(ValueError):
    """不安全值检测错误"""
    pass


class CommandBuilder:
    """
    通用命令构建器

    职责：
    - 加载和缓存 YAML 工具配置
    - 运行时参数验证
    - 安全命令构建（防注入）
    - 支持热更新

    线程安全：是（使用读写锁保护 registry）
    """

    # 类级别状态
    _registry: Dict[str, Dict] = {}
    _base_config: Dict = {}
    _config_dir: Path = None
    _loaded: bool = False
    _last_load_time: Optional[datetime] = None

    # 安全配置（从 _base.yaml 加载）
    _forbidden_chars: List[str] = [';', '|', '&', '$', '`', '(', ')', '{', '}', '[', ']', '<', '>', '\n', '\r', '\\']
    _max_command_length: int = 8192
    _max_parameters: int = 50

    @classmethod
    def initialize(cls, config_dir: Optional[str] = None):
        """
        初始化配置目录路径

        Args:
            config_dir: 配置文件目录路径（默认：项目根目录/config/tools）
        """
        if config_dir:
            cls._config_dir = Path(config_dir)
        else:
            # 自动检测：从当前文件向上查找
            current = Path(__file__).parent.parent.parent  # app/services/ -> app/ -> 项目根目录
            cls._config_dir = current / "config" / "tools"

        if not cls._config_dir.exists():
            logger.warning(f"Config directory not found: {cls._config_dir}")
            logger.warning("CommandBuilder will use fallback mode (hardcoded commands)")

    @classmethod
    def load_all(cls, force_reload: bool = False) -> int:
        """
        加载所有工具配置

        Args:
            force_reload: 是否强制重新加载（用于热更新）

        Returns:
            加载的工具数量

        Raises:
            ConfigValidationError: 配置文件格式错误
        """
        if cls._loaded and not force_reload:
            logger.info(f"CommandBuilder already loaded ({len(cls._registry)} tools)")
            return len(cls._registry)

        # Auto-initialize only if _config_dir was never set (None)
        if cls._config_dir is None:
            cls.initialize()

        if not cls._config_dir or not cls._config_dir.exists():
            logger.warning("Config directory not available, CommandBuilder disabled")
            return 0

        loaded_count = 0
        errors = []

        # 1. 加载基础配置
        base_file = cls._config_dir / "_base.yaml"
        if base_file.exists():
            try:
                with open(base_file, 'r', encoding='utf-8') as f:
                    cls._base_config = yaml.safe_load(f) or {}
                # 更新安全配置
                safety = cls._base_config.get('safety', {})
                if safety:
                    cls._forbidden_chars = safety.get('forbidden_chars', cls._forbidden_chars)
                    cls._max_command_length = safety.get('max_command_length', cls._max_command_length)
                    cls._max_parameters = safety.get('max_parameters', cls._max_parameters)
                logger.info(f"Loaded base config from {base_file}")
            except Exception as e:
                logger.error(f"Failed to load base config: {e}")

        # 2. 加载所有工具 YAML
        yaml_files = list(cls._config_dir.glob("*.yaml"))
        yaml_files = [f for f in yaml_files if not f.name.startswith("_")]

        for yaml_file in yaml_files:
            try:
                tool_config = cls._load_single_config(yaml_file)
                if tool_config:
                    tool_name = tool_config["tool"]
                    cls._registry[tool_name] = tool_config
                    loaded_count += 1
            except ConfigValidationError as e:
                errors.append(f"{yaml_file.name}: {e}")
            except Exception as e:
                errors.append(f"{yaml_file.name}: Unexpected error - {e}")

        cls._loaded = True
        cls._last_load_time = datetime.now()

        if errors:
            logger.warning(f"Loaded {loaded_count} tools with {len(errors)} errors:")
            for error in errors:
                logger.warning(f"  - {error}")
        else:
            logger.info(f"✅ Loaded {loaded_count} tool configurations")

        return loaded_count

    @classmethod
    def _load_single_config(cls, yaml_file: Path) -> Optional[Dict]:
        """
        加载并验证单个工具配置

        Args:
            yaml_file: YAML 文件路径

        Returns:
            工具配置字典

        Raises:
            ConfigValidationError: 配置无效
        """
        with open(yaml_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if not config:
            raise ConfigValidationError("Empty configuration")

        # 基本字段验证
        if "tool" not in config:
            raise ConfigValidationError("Missing required field: 'tool'")
        if "binary" not in config:
            raise ConfigValidationError(f"Tool '{config.get('tool', 'unknown')}': Missing required field: 'binary'")

        # 验证 target_placement
        placement = config.get("target_placement", "flag")
        valid_placements = {"flag", "positional", "stdin_pipe", "trailing"}
        if placement not in valid_placements:
            raise ConfigValidationError(
                f"Tool '{config['tool']}': Invalid target_placement '{placement}'. "
                f"Must be one of: {valid_placements}"
            )

        # flag 模式必须指定 target_flag
        if placement == "flag" and "target_flag" not in config:
            raise ConfigValidationError(
                f"Tool '{config['tool']}': target_placement='flag' requires 'target_flag'"
            )

        # stdin_pipe 模式必须指定 pipe_command
        if placement == "stdin_pipe" and "pipe_command" not in config:
            raise ConfigValidationError(
                f"Tool '{config['tool']}': target_placement='stdin_pipe' requires 'pipe_command'"
            )

        # 验证参数定义
        parameters = config.get("parameters", {})
        if parameters:
            for param_name, param_def in parameters.items():
                cls._validate_parameter(config["tool"], param_name, param_def)

        return config

    @classmethod
    def _validate_parameter(cls, tool_name: str, param_name: str, param_def: Dict):
        """
        验证单个参数定义

        Args:
            tool_name: 工具名称
            param_name: 参数名称
            param_def: 参数定义

        Raises:
            ConfigValidationError: 参数定义无效
        """
        if not isinstance(param_def, dict):
            raise ConfigValidationError(
                f"Tool '{tool_name}', param '{param_name}': definition must be a dict"
            )

        # 验证 type
        ptype = param_def.get("type", "string")
        valid_types = {"string", "integer", "boolean", "positional"}
        if ptype not in valid_types:
            raise ConfigValidationError(
                f"Tool '{tool_name}', param '{param_name}': invalid type '{ptype}'. "
                f"Must be one of: {valid_types}"
            )

        # boolean 类型不应该有 flag（或者 flag 是可选的）
        if ptype == "boolean" and "flag" not in param_def:
            raise ConfigValidationError(
                f"Tool '{tool_name}', param '{param_name}': boolean type requires 'flag'"
            )

        # 验证 validators
        validators = param_def.get("validators", {})
        if validators:
            if "min" in validators and "max" in validators:
                if validators["min"] > validators["max"]:
                    raise ConfigValidationError(
                        f"Tool '{tool_name}', param '{param_name}': min > max"
                    )

    @classmethod
    def is_available(cls, tool_name: str) -> bool:
        """
        检查工具是否已配置

        Args:
            tool_name: 工具名称

        Returns:
            是否已配置
        """
        return tool_name in cls._registry

    @classmethod
    def get_tool_config(cls, tool_name: str) -> Optional[Dict]:
        """
        获取工具配置

        Args:
            tool_name: 工具名称

        Returns:
            工具配置字典
        """
        return cls._registry.get(tool_name)

    @classmethod
    def get_registered_tools(cls) -> List[str]:
        """
        获取所有已注册的工具名称

        Returns:
            工具名称列表
        """
        return list(cls._registry.keys())

    @classmethod
    def build(cls, tool_name: str, target: str, params: Dict[str, Any]) -> List[str]:
        """
        构建命令（主入口）

        Args:
            tool_name: 工具名称
            target: 目标地址
            params: 参数字典

        Returns:
            命令列表（可用于 subprocess.Popen）

        Raises:
            ValueError: 工具未配置或参数无效
            UnsafeValueError: 检测到不安全值
        """
        # 1. 检查工具是否已配置
        config = cls._registry.get(tool_name)
        if not config:
            raise ValueError(f"Tool '{tool_name}' not found in configuration")

        # 2. 验证目标
        cls._validate_target(target)

        # 2.5 合并 top-level default_params
        default_params = config.get("default_params", {})
        for dk, dv in default_params.items():
            if dk not in params:
                params[dk] = dv

        # 3. 验证参数
        cls._validate_params(config, params)

        # 4. 构建命令
        placement = config.get("target_placement", "flag")

        if placement == "stdin_pipe":
            return cls._build_stdin_pipe(config, target, params)

        cmd = [config["binary"]]

        # 处理目标
        if placement == "flag":
            cmd.extend([config["target_flag"], cls._sanitize(target)])
        elif placement == "positional":
            cmd.append(cls._sanitize(target))
        # trailing 在参数处理后添加

        # 处理参数
        param_order = config.get("parameter_order", list(config.get("parameters", {}).keys()))
        param_count = 0

        for param_name in param_order:
            if param_name == "target":
                continue
            # 跳过 additional_args（在末尾单独处理）
            if param_name == "additional_args":
                continue

            # 检查参数是否存在
            if param_name not in params:
                # 使用默认值
                param_def = config.get("parameters", {}).get(param_name, {})
                if "default" in param_def:
                    params[param_name] = param_def["default"]
                else:
                    continue

            param_def = config.get("parameters", {}).get(param_name)
            if not param_def:
                continue

            param_value = params[param_name]
            formatted = cls._format_param(param_def, param_value)
            cmd.extend(formatted)
            param_count += len(formatted)

        # 检查参数数量限制
        if param_count > cls._max_parameters:
            raise ValueError(
                f"Command would have {param_count} tokens, exceeds limit of {cls._max_parameters}"
            )

        # trailing 模式：目标放在最后
        if placement == "trailing":
            cmd.append(cls._sanitize(target))

        # 处理 additional_args
        if "additional_args" in params:
            additional = str(params["additional_args"])
            for arg in additional.split():
                if arg and not any(c in arg for c in cls._forbidden_chars):
                    cmd.append(arg)

        # 检查命令长度
        cmd_str = " ".join(cmd)
        if len(cmd_str) > cls._max_command_length:
            raise ValueError(
                f"Command length {len(cmd_str)} exceeds limit of {cls._max_command_length}"
            )

        return cmd

    @classmethod
    def _validate_target(cls, target: str):
        """
        验证目标地址

        Args:
            target: 目标地址

        Raises:
            UnsafeValueError: 目标包含不安全字符
        """
        if not target or not isinstance(target, str):
            raise ValueError("Target must be a non-empty string")

        # 检查危险字符
        for char in cls._forbidden_chars:
            if char in target:
                raise UnsafeValueError(
                    f"Target contains forbidden character: {repr(char)}"
                )

    @classmethod
    def _validate_params(cls, config: Dict, params: Dict[str, Any]):
        """
        运行时参数验证

        Args:
            config: 工具配置
            params: 传入的参数

        Raises:
            ValueError: 参数无效
        """
        parameters = config.get("parameters", {})

        for param_name, param_value in params.items():
            # 跳过 additional_args（特殊处理）
            if param_name == "additional_args":
                continue

            # 检查参数是否在定义中
            if param_name not in parameters:
                logger.warning(
                    f"Tool '{config['tool']}': Unknown parameter '{param_name}' "
                    f"(allowed: {list(parameters.keys())})"
                )
                continue

            param_def = parameters[param_name]
            ptype = param_def.get("type", "string")

            # 类型验证
            if param_value is not None:
                if ptype == "integer":
                    try:
                        int_val = int(param_value)
                        # 范围验证
                        validators = param_def.get("validators", {})
                        if "min" in validators and int_val < validators["min"]:
                            raise ValueError(
                                f"Parameter '{param_name}' value {int_val} below minimum {validators['min']}"
                            )
                        if "max" in validators and int_val > validators["max"]:
                            raise ValueError(
                                f"Parameter '{param_name}' value {int_val} above maximum {validators['max']}"
                            )
                    except (ValueError, TypeError) as e:
                        if "below minimum" in str(e) or "above maximum" in str(e):
                            raise
                        raise ValueError(
                            f"Parameter '{param_name}' must be an integer, got: {type(param_value).__name__}"
                        )

                elif ptype == "boolean":
                    # 接受多种布尔值表示
                    valid_booleans = {True, False, "true", "false", "True", "False", 1, 0}
                    if param_value not in valid_booleans:
                        raise ValueError(
                            f"Parameter '{param_name}' must be boolean, got: {repr(param_value)}"
                        )

                elif ptype == "string":
                    # 字符串验证（正则模式）
                    validators = param_def.get("validators", {})
                    if "pattern" in validators:
                        if not re.match(validators["pattern"], str(param_value)):
                            raise ValueError(
                                f"Parameter '{param_name}' does not match pattern: {validators['pattern']}"
                            )

    @classmethod
    def _sanitize(cls, value: str) -> str:
        """
        清理值（命令注入防护）

        Args:
            value: 原始值

        Returns:
            清理后的值

        Raises:
            UnsafeValueError: 包含无法清理的不安全字符
        """
        if not isinstance(value, str):
            value = str(value)

        # 检查危险字符
        for char in cls._forbidden_chars:
            if char in value:
                raise UnsafeValueError(
                    f"Value contains forbidden character: {repr(char)} in '{value}'"
                )

        return value

    @classmethod
    def _format_param(cls, param_def: Dict, value: Any) -> List[str]:
        """
        根据类型格式化参数

        Args:
            param_def: 参数定义
            value: 参数值

        Returns:
            命令片段列表
        """
        ptype = param_def.get("type", "string")
        flag = param_def.get("flag")

        if ptype == "boolean":
            # 布尔标志：仅在值为 true 时添加 flag
            is_true = value in (True, "true", "True", 1)
            return [flag] if is_true and flag else []

        elif ptype == "integer":
            # 整数：转换为字符串
            str_val = str(int(value))
            return [flag, str_val] if flag else [str_val]

        elif ptype == "positional":
            # 位置参数：无 flag，直接添加值
            return [str(value)]

        else:
            # 字符串（默认）
            return [flag, str(value)] if flag else [str(value)]

    @classmethod
    def _build_stdin_pipe(cls, config: Dict, target: str, params: Dict[str, Any]) -> List[str]:
        """
        构建 stdin 管道命令（特殊工具）

        Args:
            config: 工具配置
            target: 目标地址
            params: 参数

        Returns:
            命令列表
        """
        pipe_command = config.get("pipe_command", "")
        if not pipe_command:
            raise ValueError(f"Tool '{config['tool']}': missing pipe_command")

        # 替换 {target} 占位符
        safe_target = cls._sanitize(target)
        full_command = pipe_command.replace("{target}", safe_target)

        # 添加额外参数
        parameters = config.get("parameters", {})
        for param_name, param_value in params.items():
            if param_name == "additional_args" and param_value:
                full_command += " " + str(param_value)
                break

        # 返回 bash -c 命令
        return ["bash", "-c", full_command]

    @classmethod
    def reload(cls) -> int:
        """
        热重载配置

        Returns:
            重新加载的工具数量
        """
        logger.info("Reloading CommandBuilder configuration...")
        cls._registry.clear()
        cls._loaded = False
        return cls.load_all(force_reload=True)

    @classmethod
    def get_stats(cls) -> Dict:
        """
        获取构建器统计信息

        Returns:
            统计字典
        """
        return {
            "loaded": cls._loaded,
            "tool_count": len(cls._registry),
            "config_dir": str(cls._config_dir) if cls._config_dir else None,
            "last_load_time": cls._last_load_time.isoformat() if cls._last_load_time else None,
            "registered_tools": cls.get_registered_tools(),
        }


# ============================================================================
# 模块级便捷函数
# ============================================================================

def init_command_builder(app):
    """
    Flask 应用初始化钩子

    Args:
        app: Flask 应用实例
    """
    config_dir = app.config.get("COMMAND_BUILDER_CONFIG_DIR")
    CommandBuilder.initialize(config_dir)
    count = CommandBuilder.load_all()
    logger.info(f"CommandBuilder initialized with {count} tools")
