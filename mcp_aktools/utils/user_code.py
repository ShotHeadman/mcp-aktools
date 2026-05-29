"""用户代码安全校验工具。"""

import ast
from typing import Iterable

from ..exceptions import ValidationException


BLOCKED_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "globals",
    "input",
    "locals",
    "open",
}

SAFE_BUILTINS = {
    "__build_class__": __build_class__,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "object": object,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "super": super,
    "tuple": tuple,
    "ValueError": ValueError,
    "zip": zip,
}


def _import_names(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name.split(".")[0]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            yield node.module.split(".")[0]


def validate_user_code(
    code: str,
    *,
    allowed_imports: Iterable[str] = (),
    max_chars: int = 20000,
) -> None:
    """对 MCP 输入的 Python 代码做静态安全校验。

    这不是沙箱，只用于拒绝明显危险的代码形态。真正的隔离仍应由进程、
    容器或运行时权限边界提供。
    """
    if not code or not code.strip():
        raise ValidationException("code", "代码不能为空")
    if len(code) > max_chars:
        raise ValidationException("code", f"代码长度不能超过 {max_chars} 字符")

    allowed = {item.strip() for item in allowed_imports if item.strip()}

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValidationException("code", f"代码语法错误: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            blocked = sorted(set(_import_names(node)) - allowed)
            if blocked:
                raise ValidationException(
                    "code",
                    f"不允许导入模块: {', '.join(blocked)}",
                )

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALLS:
                raise ValidationException("code", f"不允许调用 {func.id}()")
            if isinstance(func, ast.Attribute) and func.attr.startswith("__"):
                raise ValidationException("code", "不允许调用双下划线属性")

        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValidationException("code", "不允许访问双下划线属性")
