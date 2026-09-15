"""工具注册表 + 工具实现。

设计：每加一个工具只需用 @register 注册一次（名字、描述、参数声明），
模型可见的 function calling schema 由注册表自动生成，执行时按名字查表分发。
缺参数 / 未知工具 / 执行异常都以字符串返回，由模型自行向用户解释，不会中断对话。
"""

import ast
import datetime
import difflib
import fnmatch
import json
import operator
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .memory import (
    add_undo,
    doc_delete,
    doc_list,
    doc_save,
    get_undo,
    get_project,
    get_setting,
    log_tool_call,
    mark_undo_applied,
    note_list,
    note_save,
    session_project,
    session_title,
    search_docs,
    search_memory,
    set_setting,
)
from .vision import describe_image as _vision_describe
from .vision import vision_configured
from .reminders import add_reminder, delete_reminder, list_reminders
from . import skills as skill_manager

_REGISTRY: dict[str, dict] = {}

_CTX_LOCAL = threading.local()
_CTX_LOCK = threading.RLock()
_CTX_STATES: dict[str, dict] = {}
_BROWSER_LOCK = threading.RLock()


def _new_context_state() -> dict:
    return {
        "root": "",
        "approved": False,
        "cancel_event": None,
        "deadline": 0.0,
        "hook": {"calls": {}, "failures": 0, "total": 0},
        "browser": None,
    }


def _session_id() -> str:
    return getattr(_CTX_LOCAL, "session_id", "")


def _context_state() -> dict:
    session_id = _session_id()
    with _CTX_LOCK:
        return _CTX_STATES.setdefault(session_id, _new_context_state())


def register(
    name: str,
    description: str,
    parameters: dict,
    required: list[str] | None = None,
):
    """注册一个工具：description/parameters 会生成模型可见的 schema。"""

    def wrap(func):
        if name in _REGISTRY:
            raise ValueError(f"工具重名：{name}")
        _REGISTRY[name] = {
            "func": func,
            "description": description,
            "parameters": parameters,
            "required": list(required or []),
        }
        return func

    return wrap


def tool_schemas() -> list[dict]:
    """由注册表自动生成 OpenAI function calling 格式的工具列表。"""
    schemas = []
    for name, spec in _REGISTRY.items():
        params: dict = {"type": "object", "properties": spec["parameters"]}
        if spec["required"]:
            params["required"] = spec["required"]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": params,
                },
            }
        )
    return schemas


_HOOKS: dict[str, list[dict]] = {"pre": [], "post": []}


def register_tool_hook(phase: str, name: str, fn, priority: int = 100) -> None:
    """注册一个工具纪律钩子：pre=执行前拦截/计数；post=执行后审计。

    返回非 None 表示拦截/改写结果（pre 拦截时该字符串直接返回给模型）。
    """
    if phase not in _HOOKS:
        raise ValueError(f"未知钩子阶段：{phase}（可选 pre/post）")
    _HOOKS[phase].append(
        {"name": name, "fn": fn, "priority": int(priority)}
    )
    _HOOKS[phase].sort(key=lambda h: h["priority"])


def reset_hook_state() -> None:
    """每轮对话开始清空纪律状态（重复调用计数/失败数）。"""
    _context_state()["hook"] = {"calls": {}, "failures": 0, "total": 0}


def hook_failures() -> int:
    """本轮内工具失败次数（agent 据此注入收敛提醒）。"""
    return int(_context_state()["hook"].get("failures", 0))


def _fire_tool_hooks(phase: str, name: str, arguments: dict, result=None):
    for hook in _HOOKS.get(phase, []):
        out = hook["fn"](name, arguments, result)
        if out is not None:
            return out
    return None


def _hook_loop_breaker(name: str, arguments: dict, result=None):
    """硬闸门：同一轮里相同(工具, 参数)重复调用 ≥3 次直接拦截。"""
    try:
        key = name + "|" + json.dumps(arguments, sort_keys=True, ensure_ascii=False)[:300]
    except (TypeError, ValueError):
        key = name
    calls = _context_state()["hook"].setdefault("calls", {})
    calls[key] = calls.get(key, 0) + 1
    if calls[key] >= 3:
        return (
            f"纪律闸门：{name} 已用相同参数重复调用 {calls[key]} 次，本轮已拦截。"
            "请换一种做法、先读结果分析原因，或询问用户后再继续。"
        )
    return None


def _hook_call_fuse(name: str, arguments: dict, result=None):
    """硬闸门：单轮工具调用总数超过上限即熔断，防止绕圈烧钱。"""
    if not _session_id():
        return None  # 只在 agent 会话轮内计数；直接调用（测试/工具调试）不熔断
    hook = _context_state()["hook"]
    hook["total"] = hook.get("total", 0) + 1
    if get_setting("disc_call_fuse", "1") == "0":
        return None
    limit = int(os.getenv("NYALUME_TOOL_CALL_LIMIT", "200"))
    if hook["total"] > limit:
        return (
            f"纪律闸门：本轮工具调用已达上限（{limit} 次），已熔断停止。"
            "请先总结目前进展并询问用户，不要继续自动重试。"
        )
    return None


def _hook_failure_audit(name: str, arguments: dict, result=None):
    """post：把执行失败次数记入状态，供 agent 按需注入收敛提醒。"""
    if result and (
        isinstance(result, str)
        and ("失败" in result or result.startswith("执行失败"))
    ):
        if get_setting("disc_failure_hint", "1") != "0":
            hook = _context_state()["hook"]
            hook["failures"] = hook.get("failures", 0) + 1
    return None


register_tool_hook("pre", "loop_breaker", _hook_loop_breaker, priority=0)
register_tool_hook("pre", "call_fuse", _hook_call_fuse, priority=10)
register_tool_hook("post", "failure_audit", _hook_failure_audit, priority=100)


def execute_tool(name: str, arguments: dict) -> str:
    """按名字查注册表执行工具，返回字符串结果（错误也转成字符串）。"""
    spec = _REGISTRY.get(name)
    if not spec:
        return f"未知工具：{name}（可用工具：{', '.join(_REGISTRY)}）"
    if permission_mode() == "daily":
        return "当前是日常模式：只进行聊天，不会调用工具；切回工作模式后可执行。"
    missing = [
        key for key in spec["required"] if arguments.get(key) in (None, "")
    ]
    if missing:
        return f"工具 {name} 缺少必要参数：{'、'.join(missing)}"
    if (
        permission_mode() == "read_only"
        and name in _DISK_WRITE_TOOLS
        and not _context_state()["approved"]
    ):
        return (
            "当前是只读模式：不能写文件、运行代码或下载。"
            "需要先切到“工作区”或“全权”才能做这件事——"
            "用网页顶部的权限开关切换，切换后重试即可。"
        )
    blocked = _fire_tool_hooks("pre", name, arguments)
    if blocked:
        return blocked
    t0 = time.time()
    ok = True
    browser_locked = name.startswith("browser_")
    try:
        if browser_locked:
            _BROWSER_LOCK.acquire()
        result = spec["func"](**arguments)
    except Exception as e:
        ok = False
        result = f"工具 {name} 执行失败：{type(e).__name__}: {e}"
    finally:
        if browser_locked:
            _BROWSER_LOCK.release()
    ms = int((time.time() - t0) * 1000)
    result = result if isinstance(result, str) else str(result)
    if get_setting("disc_audit", "1") != "0":
        try:
            log_tool_call(
                _session_id(),
                name,
                json.dumps(arguments, ensure_ascii=False)[:300],
                ok and "失败" not in result,
                result[:200],
                ms,
            )
        except Exception:
            pass
    _fire_tool_hooks("post", name, arguments, result)
    return result


# ---------- 工具 1：当前时间 ----------


@register(
    "get_current_time",
    "获取当前日期和时间，用于回答“现在几点”“今天几号”等问题。",
    {},
)
def _get_current_time() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 工具 2：安全计算器 ----------


def _safe_calc(expression: str) -> str:
    """只允许数字和四则运算的表达式求值，避免任意代码执行。"""
    allowed_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.operand))
        raise ValueError("表达式包含不支持的语法")

    try:
        result = _eval(ast.parse(expression, mode="eval"))
    except Exception as e:
        return f"计算失败：{e}"
    return str(result)


@register(
    "calculator",
    "执行数学表达式求值（四则运算、幂、取模等），如 (13*78)+5。"
    "只做数学计算，不会执行其他代码。",
    {
        "expression": {
            "type": "string",
            "description": "要计算的数学表达式，如 (13*78)+5",
        }
    },
    required=["expression"],
)
def _calculator(expression: str) -> str:
    return _safe_calc(expression)


# ---------- 工具 3/4：带标签的便签 ----------


@register(
    "save_note",
    "保存一条带标签的便签（作为长期记忆）。"
    "若用户只说“记下来/记个便签/记个标签”而没有说明要记的内容，"
    "就把上下文中最该记的内容整理成 content，例如计算结果记成 (13*78)=1014，"
    "并自动取一个简短 tag（如 计算记录），不要留空或含含糊。",
    {
        "content": {
            "type": "string",
            "description": "要保存的具体便签内容，例如 (13*78)=1014",
        },
        "tag": {
            "type": "string",
            "description": "可选标签，用于归类，例如 计算记录/待办/灵感",
        },
    },
    required=["content"],
)
def _save_note(content: str, tag: str = "") -> str:
    return note_save(content, tag, _session_id())


@register(
    "list_notes",
    "查看最近保存的便签，可按标签筛选。",
    {
        "tag": {
            "type": "string",
            "description": "可选：只看该标签下的便签；不填则看全部最近便签",
        }
    },
)
def _list_notes(tag: str = "") -> str:
    return note_list(tag, _session_id())


@register(
    "delete_note",
    "删除一条便签（用户说“删掉某条便签/忘记 XX”时调用；找不到要如实说）。",
    {"content": {"type": "string", "description": "要删除的便签原文内容"}},
    required=["content"],
)
def _delete_note(content: str) -> str:
    from .memory import note_delete_by_content

    ok = note_delete_by_content(content, _session_id())
    return f"已删除便签：{content}" if ok else f"没有找到这条便签：{content}"


@register(
    "search_memory",
    "搜索历史记忆（过去的对话、便签、滚动摘要），grep 式关键词检索，"
    "不需要向量库。用户问“我上次/之前说过什么”“我们什么时候聊过 XX”"
    "“以前记过 XX 吗”等跨会话回忆问题时，先调用本工具再回答，"
    "不要凭印象编造；没搜到就如实说没找到。",
    {
        "query": {
            "type": "string",
            "description": "检索关键词（可多个词一起试），例如 408 复习 / 喝水 / 壁纸",
        },
        "limit": {
            "type": "integer",
            "description": "最多返回几条，默认 8，范围 1~20",
        },
    },
    required=["query"],
)
def _search_memory(query: str, limit: int = 8) -> str:
    return search_memory(query, limit, _session_id())


# ---------- 文档库：本地文件夹/文档/PDF ----------

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".json", ".py", ".pyw",
    ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".bat", ".ps1",
}
_OFFICE_EXTS = {".docx", ".xlsx", ".pptx", ".doc"}
_DOC_EXTS = _TEXT_EXTS | _OFFICE_EXTS | {".pdf"}
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".nyalume"
}
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 60
_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 150
_WORKSPACE_SETTING = "workspace_root"
_PERM_SETTING = "permission_mode"
_APPROVAL_ALLOW_SETTING = "approval_allow"
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_DISK_WRITE_TOOLS = {
    "file_write",
    "file_mkdir",
    "file_move",
    "file_delete",
    "pdf_edit",
    "pdf_ocr",
    "office_edit",
    "file_set_root",
    "set_workspace",
    "web_download",
    "skill_install_url",
    "browser_screenshot",
    "run_code",
}


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _extract_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_xlsx(path: str) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"# 表：{ws.title}")
            for row in ws.iter_rows(values_only=True):
                values = ["" if v is None else str(v) for v in row]
                if any(values):
                    parts.append(" | ".join(values))
        return "\n".join(parts)
    finally:
        wb.close()


def _extract_pptx(path: str) -> str:
    from pptx import Presentation

    prs = Presentation(path)
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        parts.append(line)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _extract_doc(path: str) -> str:
    """旧版 Word .doc：本机有 Word/WPS 时用 COM 转文字，否则明确报错。"""
    import win32com.client

    app = None
    for progid in ("Word.Application", "KWPS.Application"):
        try:
            app = win32com.client.DispatchEx(progid)
            break
        except Exception:
            continue
    if app is None:
        raise ValueError("未检测到 Word/WPS，无法解析 .doc；请用 Word 另存为 .docx 后再导入")
    try:
        try:
            app.Visible = False
        except Exception:
            pass
        doc = app.Documents.Open(path, ReadOnly=True, AddToRecentFiles=False)
        try:
            return doc.Content.Text
        finally:
            doc.Close(False)
    finally:
        try:
            app.Quit()
        except Exception:
            pass


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按字符切成重叠片段，避免关键词正好落在切缝上。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def _read_document(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = _extract_pdf(path)
        # 扫描件常见坑：每页只有相同水印行（如“扫描全能王 创建”），
        # 抽出来全是重复行；没有真实正文就按无文字层处理。
        lines = {ln.strip() for ln in text.splitlines() if ln.strip()}
        return "" if len(lines) <= 2 else text
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".xlsx":
        return _extract_xlsx(path)
    if ext == ".pptx":
        return _extract_pptx(path)
    if ext == ".doc":
        return _extract_doc(path)
    with open(path, "rb") as fh:
        raw = fh.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError("文件超过 2MB，先拆小或告诉我只要哪部分")
    return _decode_text(raw)


def _candidate_files(path: str) -> list[str]:
    """展开目录或单文件为待导入文件清单，自动跳过常见噪音目录/超大文件。"""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return [path]
    found: list[str] = []
    for root, dirs, files in os.walk(path):
        _check_run_control()
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            found.append(os.path.join(root, name))
            if len(found) >= _MAX_FILES:
                return found
    return found


def project_inventory(project_id: str, limit: int = 80) -> str:
    """项目文件清单（首次项目对话时给 agent 看的轻量索引，不读正文）。"""
    project = get_project(project_id)
    if not project:
        return ""
    lines: list[str] = []
    for root in project.get("folders") or [project.get("path")]:
        if not root or not os.path.isdir(root):
            continue
        label = os.path.basename(os.path.normpath(root)) or root
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for name in sorted(files):
                rel = os.path.relpath(os.path.join(base, name), root)
                if len(lines) < limit:
                    lines.append(f"{label}/{rel}")
                else:
                    return "\n".join(lines) + f"\n…（还有更多文件，需要时再列）"
    return "\n".join(lines) or "（空目录）"


@register(
    "search_files",
    "按文件名关键词找文件（默认只在当前授权根：项目/工作目录内搜索）。"
    "想搜授权根以外（比如 D:\\ 整盘）必须给 base 参数；工作区模式下会先弹审批，"
    "全权模式下可直接搜。搜不到就如实说，不要编路径。",
    {
        "keyword": {
            "type": "string",
            "description": "文件名关键词（不含路径），支持 * 和 ? 通配，例如 小夏 / *.pdf",
        },
        "base": {
            "type": "string",
            "description": "可选：搜索起点绝对路径，如 D:\\Desktop；留空 = 当前授权根",
        },
        "limit": {
            "type": "integer",
            "description": "最多返回几条，默认 30，最大 200",
        },
    },
    required=["keyword"],
)
def _search_files(keyword: str, base: str = "", limit: int = 30) -> str:
    keyword = (keyword or "").strip()
    if not keyword:
        return "搜索失败：缺少文件名关键词"
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 30
    try:
        root = _path_in_workspace(base) if base else None
    except ValueError as e:
        return f"搜索失败：{e}"
    roots = [root] if root else _allowed_roots()
    use_glob = any(ch in keyword for ch in "*?")
    pattern = keyword.lower()
    hits: list[str] = []
    for start in roots:
        if not os.path.isdir(start):
            continue
        for dirpath, dirs, files in os.walk(start):
            _check_run_control()
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for bucket, is_dir in ((sorted(dirs), True), (sorted(files), False)):
                for name in bucket:
                    low = name.lower()
                    if fnmatch.fnmatchcase(low, pattern) if use_glob else pattern in low:
                        path = os.path.join(dirpath, name)
                        hits.append(("[目录] " if is_dir else "[文件] ") + path)
                        if len(hits) >= limit:
                            break
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
    if not hits:
        scope = base or "当前授权根"
        return f"在 {scope} 里没有找到名称含「{keyword}」的文件或文件夹"
    lines = [f"找到 {len(hits)} 个匹配项（已到上限则可能没列全）："] + [f"- {h}" for h in hits]
    return "\n".join(lines)


@register(
    "add_documents",
    "把本地文件夹/文档/PDF 导入长期文档库，之后可随时检索回答。"
    "用户给出本地路径（文件夹或 .txt/.md/.pdf 等文件）并要你看/总结/基于它回答/存档时调用；"
    "导入同一路径会更新不重复。导入完成后按实际导入的文件回答用户，不要假装读过没导入的文件。",
    {
        "path": {
            "type": "string",
            "description": "本地绝对路径：文件夹或单个文档，例如 D:\\资料\\项目文档",
        }
    },
    required=["path"],
)
def _add_documents(path: str) -> str:
    path = (path or "").strip().strip('"').strip("'")
    if not os.path.exists(path):
        return f"路径不存在：{path}"

    files = [f for f in _candidate_files(path)]
    if not files:
        return f"在 {path} 里没有找到支持的文档类型（文本/Markdown/PDF 等）"
    supported = [f for f in files if os.path.splitext(f)[1].lower() in _DOC_EXTS]
    if not supported:
        return "没有找到支持的文档类型（支持文本/Markdown/代码/PDF，别的类型再说）"

    imported, skipped = [], []
    for file_path in supported:
        _check_run_control()
        try:
            text = _read_document(file_path)
            chunks = _chunk_text(text)
            if not chunks:
                doc_delete(file_path, _session_id())  # 清掉当前对话里的旧索引
                skipped.append(f"{os.path.basename(file_path)}（没有可提取的文字，可能是扫描版 PDF）")
                continue
            doc_save(
                file_path, os.path.basename(file_path), chunks, _session_id()
            )
            imported.append(f"{os.path.basename(file_path)}（{len(chunks)} 段）")
        except Exception as e:
            skipped.append(f"{os.path.basename(file_path)}（{type(e).__name__}: {e}）")

    if not imported:
        return "导入失败，原因：" + "；".join(skipped)
    reply = f"已导入 {len(imported)} 个文档：\n" + "\n".join(f"- {n}" for n in imported)
    if skipped:
        reply += "\n跳过：" + "；".join(skipped)
    return reply


@register(
    "search_docs",
    "在已导入的文档库里按关键词检索，返回相关原文片段。"
    "用户问导入过的资料/文件夹里的内容时调用；一次没搜到就换相近的关键词"
    "（比如用户的口语说法/错字和文档用词不一致）再搜一次，"
    "实在没有才说没找到或考虑搜网页，不要编造。",
    {
        "query": {
            "type": "string",
            "description": "检索关键词，例如 壁纸 / 权限 / 验收标准",
        },
        "limit": {
            "type": "integer",
            "description": "最多返回几段原文，默认 6，范围 1~10",
        },
    },
    required=["query"],
)
def _search_docs(query: str, limit: int = 6) -> str:
    return search_docs(query, limit, _session_id())


@register(
    "list_docs",
    "列出文档库里已导入的所有文档及分段数。",
    {},
)
def _list_docs() -> str:
    rows = doc_list(_session_id())
    if not rows:
        return "文档库还是空的，给我一个文件夹或文档路径就能导入"
    return "\n".join(
        f"- {r['title']}（{r['chunks']} 段）\n  {r['source']}" for r in rows
    )


# ---------- 工作目录：文件类工具只允许在这个目录内动文件 ----------

def workspace_root() -> str:
    """当前工作目录：默认 <仓库>/workspace，可被用户显式改到别处（存 DB）。"""
    root = (
        os.environ.get("AGENT_WORKSPACE")
        or get_setting(_WORKSPACE_SETTING)
        or os.path.join(_REPO_ROOT, "workspace")
    )
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root


def session_work_root(session_id: str = "", create: bool = True) -> str:
    """项目会话用项目根；普通会话在 workspace/conversations 下各用一个目录。"""
    session_id = (session_id or "").strip()
    if not session_id:
        return workspace_root()
    project_id = session_project(session_id)
    if project_id:
        project = get_project(project_id)
        if project.get("path"):
            return os.path.abspath(project["path"])

    parent = os.path.join(workspace_root(), "conversations")
    if create:
        os.makedirs(parent, exist_ok=True)
    elif not os.path.isdir(parent):
        return ""
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80] or "chat"
    suffix = "__" + safe_id
    for entry in os.scandir(parent):
        if entry.is_dir() and entry.name.endswith(suffix):
            return entry.path
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", session_title(session_id))
    title = re.sub(r"\s+", " ", title).strip(" .")[:36] or "新对话"
    root = os.path.join(parent, title + suffix)
    if create:
        os.makedirs(root, exist_ok=True)
        return root
    return ""


def cleanup_session_temp(session_id: str) -> None:
    """清掉本轮一次性辅助脚本；正式产物不会放在这里。"""
    existing_root = session_work_root(session_id, create=False)
    if not existing_root:
        return
    root = os.path.realpath(existing_root)
    temp_root = os.path.realpath(os.path.join(root, ".nyalume", "tmp"))
    if os.path.commonpath([root, temp_root]) != root or not os.path.isdir(temp_root):
        return
    try:
        shutil.rmtree(temp_root)
    except OSError:
        return
    meta_dir = os.path.dirname(temp_root)
    try:
        os.rmdir(meta_dir)
    except OSError:
        pass


def clean_session_workspace(session_id: str) -> dict:
    """只清普通会话目录里的可再生成缓存，保留用户产物与上传文件。"""
    if session_project(session_id):
        raise ValueError("项目会话共用项目目录，不能从这里清理")
    existing_root = session_work_root(session_id, create=False)
    if not existing_root:
        return {"files": 0, "bytes": 0}
    root = os.path.realpath(existing_root)
    conversations = os.path.realpath(os.path.join(workspace_root(), "conversations"))
    if os.path.commonpath([conversations, root]) != conversations:
        raise ValueError("会话工作目录不在 conversations 下")

    removed_files = 0
    removed_bytes = 0

    def count_tree(path: str) -> None:
        nonlocal removed_files, removed_bytes
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if not os.path.islink(os.path.join(dirpath, name))
            ]
            for name in filenames:
                file_path = os.path.join(dirpath, name)
                removed_files += 1
                try:
                    removed_bytes += os.path.getsize(file_path)
                except OSError:
                    pass

    cache_names = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        for name in list(dirnames):
            path = os.path.join(dirpath, name)
            is_helper_tmp = name == "tmp" and os.path.basename(dirpath) == ".nyalume"
            if name in cache_names or is_helper_tmp:
                count_tree(path)
                shutil.rmtree(path, ignore_errors=True)
                dirnames.remove(name)
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in {".tmp", ".pyc", ".pyo"}:
                continue
            path = os.path.join(dirpath, name)
            removed_files += 1
            try:
                removed_bytes += os.path.getsize(path)
                os.remove(path)
            except OSError:
                pass
    meta_dir = os.path.join(root, ".nyalume")
    try:
        os.rmdir(meta_dir)
    except OSError:
        pass
    return {"files": removed_files, "bytes": removed_bytes}


def permission_mode() -> str:
    """当前模式：daily=日常聊天，其余三档为工作权限。"""
    mode = get_setting(_PERM_SETTING, "workspace")
    return mode if mode in ("daily", "read_only", "workspace", "full") else "workspace"


def set_permission_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in ("daily", "read_only", "workspace", "full"):
        return "无效模式，可选 daily / read_only / workspace / full"
    set_setting(_PERM_SETTING, mode)
    return mode


def describe_tool(name: str, arguments: dict) -> str:
    """给 UI/审批弹窗一句话描述工具要做什么。"""
    args = arguments or {}
    simple = {
        "file_write": f"写文件 {args.get('path')}",
        "file_mkdir": f"创建目录 {args.get('path')}",
        "file_move": f"移动 {args.get('src')} → {args.get('dst')}",
        "file_delete": f"删除 {args.get('path')}",
        "pdf_edit": f"编辑 PDF → {args.get('output')}",
        "pdf_ocr": f"识别 PDF 文字 {args.get('path')} → {args.get('output')}",
        "office_edit": f"编辑 Office 文档 {args.get('path')} → {args.get('output')}",
        "file_list": f"列目录 {args.get('path') or '.'}",
        "file_read": f"读文件 {args.get('path')}",
        "file_set_root": f"切换操作根 {args.get('path')}",
        "set_workspace": f"设置工作目录 {args.get('path')}",
        "web_download": f"下载 {args.get('url')}",
        "skill_install_url": f"从网上安装 Skill：{args.get('url')}",
        "skill_read_resource": (
            f"读取 Skill {args.get('skill_id')} 的 {args.get('path') or 'SKILL.md'}"
        ),
        "web_search": f"联网搜索「{args.get('query')}」",
        "browser_read": f"打开网页读内容 {args.get('url')}",
        "browser_click": f"在网页上点「{args.get('label')}」"
        + (f"（先打开 {args.get('url')}）" if args.get("url") else ""),
        "browser_fill": f"在网页上往「{args.get('label')}」输入内容",
        "browser_screenshot": "截当前网页图保存",
        "browser_close": "关闭浏览器页面",
        "search_files": f"搜索文件名「{args.get('keyword')}」"
        + (f"（范围 {args.get('base')}）" if args.get("base") else "（当前授权根内）"),
        "run_code": f"运行：{args.get('command')}",
        "add_documents": f"导入资料 {args.get('path')}",
        "search_docs": f"检索文档「{args.get('query')}」",
        "search_memory": f"检索记忆「{args.get('query')}」",
        "save_note": f"记便签：{args.get('content')}",
        "delete_note": f"删便签：{args.get('content')}",
        "create_reminder": f"设定时提醒：{args.get('content')}",
        "remind_me_in": f"设一次性提醒：{args.get('content')}",
        "cancel_reminder": f"取消提醒 #{args.get('reminder_id')}",
    }
    return simple.get(name) or f"{name}{args}"


def approval_needed(name: str, arguments: dict) -> tuple[bool, str]:
    """判定该不该弹审批：full 全放行；只读全拦；工作区只拦越界/联网/改根。"""
    label = str((arguments or {}).get("label") or "")
    if name == "skill_install_url":
        return True, "将从网上下载并安装新的 Skill，每次都要确认：" + describe_tool(name, arguments)
    if name == "pdf_ocr":
        return True, "扫描页可能发送给已配置的视觉模型，每次都要确认：" + describe_tool(name, arguments)
    if name == "browser_click" and re.search(
        r"下单|购买|支付|付款|转账|发送|发布|删除|注销|退款|订阅|开通|"
        r"确认|确定|提交|保存|上传|登录|注册|"
        r"submit|buy|purchase|pay|transfer|send|publish|delete|remove|"
        r"subscribe|checkout|place\s*order",
        label,
        re.I,
    ):
        return True, "网页上的高风险操作，每次都要确认：" + describe_tool(name, arguments)
    if name == "browser_fill" and re.search(
        r"密码|验证码|银行卡|信用卡|卡号|安全码|password|passcode|otp|cvv|card\s*number",
        label,
        re.I,
    ):
        return True, "将填写敏感信息，每次都要确认：" + describe_tool(name, arguments)
    if auto_approved(name):
        return False, ""
    if name == "search_files":
        base = arguments.get("base")
        if base:
            try:
                _path_in_workspace(str(base))
            except ValueError:
                return True, "越出当前授权根搜索：" + describe_tool(name, arguments)
        return False, ""
    mode = permission_mode()
    if mode == "full":
        return False, ""
    if name not in _DISK_WRITE_TOOLS:
        return False, ""
    desc = describe_tool(name, arguments)
    if mode == "read_only":
        return True, f"只读模式下请求执行：{desc}"
    if name in ("web_download", "set_workspace"):
        return True, f"涉及联网或改动工作根：{desc}"
    path_values = [
        arguments.get(key)
        for key in ("path", "src", "dst", "cwd", "output")
    ]
    file_values = arguments.get("files") or []
    path_values.extend([file_values] if isinstance(file_values, str) else file_values)
    for value in path_values:
        if value:
            try:
                _path_in_workspace(str(value))
            except ValueError:
                return True, f"越出当前授权根：{desc}"
    return False, ""


def approval_rememberable(tool_name: str) -> bool:
    """网页交互可能改变外部状态，不能用“同意并记住”永久绕过审批。"""
    return tool_name not in {
        "browser_click",
        "browser_fill",
        "pdf_ocr",
        "skill_install_url",
    }


def set_session_context(session_id: str) -> None:
    """agent 每轮调用工具前注入当前会话，文件工具据此定位“当前项目”。"""
    _CTX_LOCAL.session_id = session_id or ""
    _context_state()


def set_run_control(cancel_event=None, deadline: float = 0.0) -> None:
    state = _context_state()
    state["cancel_event"] = cancel_event
    state["deadline"] = float(deadline or 0.0)


def _run_control_state() -> str:
    state = _context_state()
    if state["cancel_event"] is not None and state["cancel_event"].is_set():
        return "cancelled"
    if state["deadline"] and time.monotonic() >= state["deadline"]:
        return "timeout"
    return ""


def _check_run_control() -> None:
    state = _run_control_state()
    if state == "cancelled":
        raise InterruptedError("任务已取消")
    if state == "timeout":
        raise TimeoutError("任务已超过总时限")


def reset_session_context(session_id: str = "") -> None:
    """新一轮对话开始前清空上下文，防止上一轮的“操作根切换”泄漏。"""
    set_session_context(session_id)
    state = _context_state()
    state["root"] = ""
    state["approved"] = False
    state["cancel_event"] = None
    state["deadline"] = 0.0
    state["hook"] = {"calls": {}, "failures": 0, "total": 0}


def clear_session_context() -> None:
    state = _context_state()
    state["root"] = ""
    state["approved"] = False
    state["cancel_event"] = None
    state["deadline"] = 0.0
    state["hook"] = {"calls": {}, "failures": 0, "total": 0}
    if hasattr(_CTX_LOCAL, "session_id"):
        delattr(_CTX_LOCAL, "session_id")


def set_approved_context(approved: bool) -> None:
    """审批通过后放行本次（until next reset）越界/只读操作。"""
    _context_state()["approved"] = bool(approved)


def _approval_allow_map() -> dict:
    try:
        return json.loads(get_setting(_APPROVAL_ALLOW_SETTING, "{}") or "{}")
    except (ValueError, TypeError):
        return {}


def record_approval_rule(tool_name: str) -> None:
    """“同意并记住”：本会话内该工具不再弹审批。"""
    session_id = _session_id()
    if not session_id:
        return
    with _CTX_LOCK:
        mapping = _approval_allow_map()
        allowed = set(mapping.get(session_id, []))
        allowed.add(tool_name)
        mapping[session_id] = sorted(allowed)
        set_setting(_APPROVAL_ALLOW_SETTING, json.dumps(mapping, ensure_ascii=False))


def auto_approved(tool_name: str) -> bool:
    session_id = _session_id()
    if not session_id:
        return False
    mapping = _approval_allow_map()
    return tool_name in (mapping.get(session_id) or [])


def _allowed_roots() -> list[str]:
    """当前会话项目授权了哪些根目录（含主目录）；无项目 = 只有工作目录。"""
    session_id = _session_id()
    if session_id:
        pid = session_project(session_id)
        if pid:
            project = get_project(pid)
            folders = list(project.get("folders") or [])
            if project.get("path") and project["path"] not in folders:
                folders.insert(0, project["path"])
            if folders:
                return [os.path.abspath(f) for f in folders]
        return [session_work_root(session_id)]
    return [workspace_root()]


def _base_root() -> str:
    """文件工具的实际作用根：项目根、独立会话目录或全局工作目录。"""
    context_root = _context_state()["root"]
    if context_root and context_root in _allowed_roots():
        return context_root
    root = _allowed_roots()[0]
    os.makedirs(root, exist_ok=True)
    return root


def _path_in_workspace(rel: str) -> str:
    """把路径解析到工作目录/项目内；全权模式允许用户点名的绝对路径。"""
    rel = (rel or "").strip().replace("\\", "/").strip("/")
    if not rel:
        return _base_root()
    is_abs = bool(re.match(r"^[a-zA-Z]:", rel)) or rel.startswith("/")
    if is_abs and (permission_mode() == "full" or _context_state()["approved"]):
        return os.path.abspath(rel)
    if ".." in rel.split("/") or is_abs:
        raise ValueError(
            "只能操作工作目录/项目内的相对路径，不能越出当前授权根。"
            "要改项目外的文件，让用户三选一：把该目录并入项目授权（⋯ 菜单）、"
            "把权限切到“全权”、或把文件拷进工作目录。"
        )
    target = os.path.join(_base_root(), *rel.split("/"))
    return os.path.abspath(target)


def _display(path: str) -> str:
    """给模型看的路径统一用工作目录相对形式。"""
    root = _base_root()
    rel = os.path.relpath(path, root)
    if rel == ".":
        return "."
    if rel.startswith(".."):
        return path  # 全权模式在授权根之外，直接显示绝对路径
    return rel.replace("\\", "/")


def media_abs(path: str) -> str:
    """把工具产生的相对媒体路径解析成绝对路径（按当前授权根）。"""
    p = (path or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        return os.path.abspath(p)
    rel = p.replace("\\", "/").lstrip("/")
    for root in _allowed_roots():
        cand = os.path.join(root, rel)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return os.path.abspath(os.path.join(_allowed_roots()[0], rel))


def tool_paths(name: str, arguments: dict) -> list[str]:
    """给前端的可点击本地路径；不改变模型看到的简洁工具结果。"""
    args = arguments or {}
    values: list[str] = []
    if name == "file_move":
        values = [str(args.get("src") or ""), str(args.get("dst") or "")]
    elif name == "pdf_edit":
        file_values = args.get("files") or []
        if isinstance(file_values, str):
            file_values = [file_values]
        values = [
            *(str(value) for value in file_values),
            str(args.get("output") or ""),
        ]
    elif name in {"pdf_ocr", "office_edit"}:
        values = [str(args.get("path") or ""), str(args.get("output") or "")]
    elif name in {"file_read", "file_write", "file_mkdir", "file_delete", "file_list"}:
        values = [str(args.get("path") or "")]
    elif name == "run_code":
        values = [str(args.get("cwd") or "")]
    elif name in {"file_set_root", "set_workspace", "add_documents"}:
        value = str(args.get("path") or "")
        if value:
            try:
                return [
                    os.path.abspath(value) if os.path.isabs(value)
                    else _path_in_workspace(value)
                ]
            except (OSError, ValueError):
                return []
    elif name in {"web_download", "browser_screenshot"} and args.get("save_as"):
        values = [str(args["save_as"])]
    paths = []
    for value in values:
        try:
            path = _path_in_workspace(value)
        except (OSError, ValueError):
            continue
        if path not in paths:
            paths.append(path)
    return paths


def _read_text(path: str, limit: int = 30000) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise ValueError("二进制文件不能直接读，先说明要做什么")
    return text[:limit] + (f"\n…（共 {len(text)} 字符，已截断）" if len(text) > limit else "")


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


_UNDO_TEXT_LIMIT = 200 * 1024


def _record_undo(kind: str, path: str, prev: str = "") -> None:
    """记录一次文件操作（只在有会话上下文时记，便于 Web 里撤销/查看）。"""
    session_id = _session_id()
    if not session_id:
        return
    op_id = "undo-" + uuid.uuid4().hex[:10]
    add_undo(op_id, session_id, kind, path, prev)


def _readable_prev(path: str) -> str:
    """读文件原文做撤销快照；超限/二进制返回空（表示无法完整恢复）。"""
    try:
        if os.path.getsize(path) > _UNDO_TEXT_LIMIT:
            return ""
        return _read_text(path, limit=_UNDO_TEXT_LIMIT)
    except (OSError, ValueError):
        return ""


def apply_undo(op_id: str) -> str:
    """执行一次撤销（server 端点用），返回结果文本。"""
    op = get_undo(op_id)
    if not op or op.get("applied"):
        return f"没有可撤销的操作 {op_id}"
    path = op.get("path") or ""
    prev = op.get("prev") or ""
    kind = op.get("kind") or ""
    try:
        if kind == "write":
            if prev:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(prev)
            elif os.path.isfile(path):
                os.remove(path)  # 新建文件：撤销=删掉
        elif kind == "delete":
            if prev:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(prev)
            else:
                return "该文件删除前不是文本，无法自动恢复；磁盘文件已无法找回"
        elif kind == "delete_dir":
            os.makedirs(path, exist_ok=True)
        elif kind == "move":
            os.makedirs(os.path.dirname(prev), exist_ok=True)
            os.replace(path, prev)
        elif kind == "mkdir":
            if os.path.isdir(path):
                os.rmdir(path)  # 非空会失败，保留给用户手动处理
    except OSError as e:
        return f"撤销失败：{e}"
    mark_undo_applied(op_id)
    return f"已撤销「{kind}」：{path}"


def preview_undo(op_id: str) -> str:
    """给“查看”用：返回该操作之前的内容/去向。"""
    op = get_undo(op_id)
    if not op:
        return "找不到该操作"
    kind = op.get("kind") or ""
    path = op.get("path") or ""
    if kind == "move":
        return f"移动前位置：{op.get('prev')}\n当前：{path}"
    if kind == "mkdir":
        return f"创建的目录：{path}"
    if kind == "delete":
        return f"删除的文件：{path}\n\n—— 删除前内容 ——\n{op.get('prev') or '（非文本，无快照）'}"
    return f"文件：{path}\n\n—— 写入前内容 ——\n{op.get('prev') or '（新建文件，无先前内容）'}"


def diff_undo(op_id: str) -> dict | None:
    """按行对比撤销快照与当前文件，输出 +/- 行数与带上下文的分块。"""
    op = get_undo(op_id)
    if not op or op.get("kind") not in ("write", "delete"):
        return None
    path = op.get("path") or ""
    prev_text = op.get("prev") or ""
    try:
        current_text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    except (OSError, UnicodeDecodeError):
        current_text = ""
    before = prev_text.splitlines()
    after = current_text.splitlines()
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    added = removed = 0
    hunks: list[list[list[str]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        added += j2 - j1
        removed += i2 - i1
        block: list[list[str]] = []
        if i1 > 0:
            block.append(["ctx", before[i1 - 1]])
        block += [["del", line] for line in before[i1:i2]]
        block += [["add", line] for line in after[j1:j2]]
        if j2 < len(after):
            block.append(["ctx", after[j2]])
        hunks.append(block)
    return {"added": added, "removed": removed, "hunks": hunks}


@register(
    "set_workspace",
    "设置 agent 的工作目录（文件工具只能在这里面操作）。"
    "只有用户在当前消息里明确给出新路径时才调用；路径不存在会自动创建。",
    {
        "path": {
            "type": "string",
            "description": "新工作目录的绝对路径，例如 D:\\我的项目",
        }
    },
    required=["path"],
)
def _set_workspace(path: str) -> str:
    path = (path or "").strip().strip('"').strip("'")
    if not re.match(r"^[a-zA-Z]:[\\/]", path):
        return "请给 Windows 绝对路径，例如 D:\\我的项目"
    path = os.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    set_setting(_WORKSPACE_SETTING, path)
    return f"工作目录已设为：{path}\n之后的文件操作都在这个目录内进行。"


@register(
    "file_set_root",
    "切换文件工具当前的操作根目录。项目授权了多个文件夹时，"
    "要操作非主目录里的文件就先调用本工具把根切过去（参数必须是授权目录的完整路径）；"
    "相对路径一律以切换后的根为准。",
    {
        "path": {
            "type": "string",
            "description": "授权文件夹的绝对路径，例如 D:\\课程资料",
        }
    },
    required=["path"],
)
def _file_set_root(path: str) -> str:
    path = (path or "").strip().strip('"').strip("'")
    target = os.path.abspath(path)
    allowed = _allowed_roots()
    if target not in allowed:
        return "不允许切到 " + target + "；当前可用根：\n- " + "\n- ".join(allowed)
    if not os.path.isdir(target):
        return f"目录不存在：{target}"
    _context_state()["root"] = target
    return f"已切换操作根：{target}\n之后 file_* 的相对路径都以它为准。"


@register(
    "file_list",
    "列出工作目录里的文件/子目录（含大小和修改时间）。",
    {
        "path": {
            "type": "string",
            "description": "相对路径，留空看工作目录根；例如 src / docs",
        }
    },
)
def _file_list(path: str = "") -> str:
    try:
        target = _path_in_workspace(path)
    except ValueError as e:
        return f"操作失败：{e}"
    if not os.path.exists(target):
        return f"路径不存在：{_display(target)}"
    if os.path.isfile(target):
        size = os.path.getsize(target)
        return f"{_display(target)}（文件，{_human_size(size)}）"
    try:
        entries = sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return f"读取失败：{e}"
    if not entries:
        return f"工作目录 {_display(target)} 是空的"
    import datetime

    lines = [f"工作目录 {_display(target)} 下："]
    for entry in entries:
        mtime = datetime.datetime.fromtimestamp(entry.stat().st_mtime).strftime(
            "%m-%d %H:%M"
        )
        if entry.is_dir():
            lines.append(f"[目录] {entry.name}/")
        else:
            lines.append(f"[文件] {entry.name}（{_human_size(entry.stat().st_size)}，{mtime}）")
    return "\n".join(lines)


@register(
    "file_read",
    "读取工作目录里的文本文件内容（最大约 3 万字符；超长会截断并提示）。",
    {
        "path": {
            "type": "string",
            "description": "相对路径，例如 main.py / docs/说明.md",
        }
    },
    required=["path"],
)
def _file_read(path: str) -> str:
    try:
        target = _path_in_workspace(path)
        if not os.path.isfile(target):
            return f"不是文件或不存在：{_display(target)}"
        return _read_text(target)
    except ValueError as e:
        return f"操作失败：{e}"
    except OSError as e:
        return f"读取失败：{e}"


@register(
    "file_write",
    "在工作目录里新建/覆盖一个文本文件（会自动创建缺失的父目录）。"
    "用于整理文件、写代码、新建项目等；一次写一个文件，内容较长也可。",
    {
        "path": {"type": "string", "description": "相对路径，例如 src/main.py"},
        "content": {"type": "string", "description": "要写入的完整文本内容"},
    },
    required=["path", "content"],
)
def _file_write(path: str, content: str) -> str:
    try:
        target = _path_in_workspace(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        prev = _readable_prev(target) if os.path.isfile(target) else ""
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content or "")
        _record_undo("write", target, prev)
    except (ValueError, OSError) as e:
        return f"写入失败：{e}"
    return f"已写入 {_display(target)}（{len(content or '')} 字符）"


@register(
    "file_mkdir",
    "在工作目录里新建目录（可一次建多层）。",
    {"path": {"type": "string", "description": "相对路径，例如 src/utils"}},
    required=["path"],
)
def _file_mkdir(path: str) -> str:
    try:
        target = _path_in_workspace(path)
        os.makedirs(target, exist_ok=True)
        _record_undo("mkdir", target)
    except (ValueError, OSError) as e:
        return f"创建失败：{e}"
    return f"已创建目录 {_display(target)}"


@register(
    "file_move",
    "在工作目录内移动/重命名文件或目录。用于整理文件（归类、改名）。",
    {
        "src": {"type": "string", "description": "源相对路径"},
        "dst": {"type": "string", "description": "目标相对路径（含新名字）"},
    },
    required=["src", "dst"],
)
def _file_move(src: str, dst: str) -> str:
    try:
        s_target = _path_in_workspace(src)
        d_target = _path_in_workspace(dst)
        if not os.path.exists(s_target):
            return f"源不存在：{_display(s_target)}"
        if os.path.abspath(d_target) == os.path.abspath(s_target):
            return "源和目标相同，不用移动"
        os.makedirs(os.path.dirname(d_target), exist_ok=True)
        os.replace(s_target, d_target)
        _record_undo("move", d_target, s_target)
    except (ValueError, OSError) as e:
        return f"移动失败：{e}"
    return f"已移动：{_display(s_target)} → {_display(d_target)}"


@register(
    "file_delete",
    "删除工作目录里的单个文件或空目录（非空目录会拒绝，避免误删）。"
    "只允许删单个文件/空目录；批量清理请多次调用或先说明用途。",
    {"path": {"type": "string", "description": "相对路径"}},
    required=["path"],
)
def _file_delete(path: str) -> str:
    try:
        target = _path_in_workspace(path)
        if target == _base_root():
            return "不能删除工作目录本身"
        if permission_mode() == "full":
            drive_root = os.path.splitdrive(target)[0] + os.sep
            if target == drive_root or target == os.path.abspath(os.sep):
                return "不能删除磁盘根目录"
            if target.lower().startswith(
                ("c:\\windows", "c:\\program files", "c:\\programdata")
            ):
                return "不能删除系统目录"
        if not os.path.exists(target):
            return f"不存在：{_display(target)}"
        if os.path.isfile(target):
            prev = _readable_prev(target)
            os.remove(target)
            _record_undo("delete", target, prev)
            return f"已删除文件 {_display(target)}"
        os.rmdir(target)  # 只删空目录；非空会抛 OSError
        _record_undo("delete_dir", target)
        return f"已删除空目录 {_display(target)}"
    except (ValueError, OSError) as e:
        return f"删除失败：{e}"


def _pdf_page_numbers(value: str, total: int) -> list[int]:
    """把 1-based 页码（1-3,5）转成去重后的 0-based 列表。"""
    pages: list[int] = []
    for part in (value or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            if len(bounds) != 2 or not all(item.isdigit() for item in bounds):
                raise ValueError(f"页码格式不正确：{part}")
            start, end = (int(item) for item in bounds)
            if start > end:
                raise ValueError(f"页码范围应从小到大：{part}")
            numbers = range(start, end + 1)
        elif part.isdigit():
            numbers = [int(part)]
        else:
            raise ValueError(f"页码格式不正确：{part}")
        for number in numbers:
            if number < 1 or number > total:
                raise ValueError(f"页码 {number} 超出范围（共 {total} 页）")
            index = number - 1
            if index not in pages:
                pages.append(index)
    if not pages:
        raise ValueError("请提供页码，例如 1-3,5")
    return pages


@register(
    "pdf_edit",
    "对工作目录/项目内的 PDF 做页面级编辑：合并多个 PDF、抽取指定页、删除指定页，"
    "或旋转指定页。页码从 1 开始，支持 1-3,5；始终写入新的 output，不修改源文件。"
    "本工具不能改写页面中的现有文字，也不能给扫描件做 OCR。",
    {
        "operation": {
            "type": "string",
            "enum": ["merge", "extract", "delete", "rotate"],
            "description": "操作：merge 合并 / extract 抽页 / delete 删页 / rotate 旋转",
        },
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "源 PDF 路径列表；合并按列表顺序，其余操作只传一个文件",
        },
        "output": {
            "type": "string",
            "description": "新 PDF 的相对路径，例如 output/整理后.pdf；不能与源文件同名",
        },
        "pages": {
            "type": "string",
            "description": "抽页/删页必填；旋转可留空表示全部页，例如 1-3,5",
        },
        "degrees": {
            "type": "integer",
            "enum": [90, 180, 270],
            "description": "rotate 的顺时针角度：90、180 或 270",
        },
    },
    required=["operation", "files", "output"],
)
def _pdf_edit(
    operation: str,
    files: list[str],
    output: str,
    pages: str = "",
    degrees: int = 90,
) -> str:
    from pypdf import PdfReader, PdfWriter

    operation = (operation or "").strip().lower()
    if operation not in {"merge", "extract", "delete", "rotate"}:
        return "PDF 编辑失败：operation 只能是 merge / extract / delete / rotate"
    if isinstance(files, str):
        files = [files]
    if not files:
        return "PDF 编辑失败：至少需要一个源 PDF"
    if operation == "merge" and len(files) < 2:
        return "PDF 编辑失败：合并至少需要两个 PDF"
    if operation != "merge" and len(files) != 1:
        return f"PDF 编辑失败：{operation} 只接受一个源 PDF"

    try:
        sources = [_path_in_workspace(path) for path in files]
        target = _path_in_workspace(output)
        if os.path.splitext(target)[1].lower() != ".pdf":
            raise ValueError("输出文件必须使用 .pdf 扩展名")
        for source in sources:
            if (
                os.path.splitext(source)[1].lower() != ".pdf"
                or not os.path.isfile(source)
            ):
                raise ValueError(f"源 PDF 不存在：{_display(source)}")
        if any(
            os.path.abspath(source) == os.path.abspath(target)
            for source in sources
        ):
            raise ValueError("output 不能与源文件相同")
        if os.path.exists(target):
            raise ValueError(f"输出已存在，请换一个文件名：{_display(target)}")

        readers = [PdfReader(source) for source in sources]
        writer = PdfWriter()
        if operation == "merge":
            for reader in readers:
                for page in reader.pages:
                    writer.add_page(page)
        else:
            reader = readers[0]
            total = len(reader.pages)
            if operation == "extract":
                selected = _pdf_page_numbers(pages, total)
                for index in selected:
                    writer.add_page(reader.pages[index])
            elif operation == "delete":
                selected = set(_pdf_page_numbers(pages, total))
                if len(selected) == total:
                    raise ValueError("不能删除全部页面")
                for index, page in enumerate(reader.pages):
                    if index not in selected:
                        writer.add_page(page)
            else:
                if degrees not in (90, 180, 270):
                    raise ValueError("旋转角度只能是 90、180 或 270")
                selected = (
                    set(_pdf_page_numbers(pages, total))
                    if pages
                    else set(range(total))
                )
                for index, page in enumerate(reader.pages):
                    if index in selected:
                        page.rotate(degrees)
                    writer.add_page(page)

        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + ".nyalume-" + uuid.uuid4().hex[:8] + ".tmp"
        try:
            with open(temporary, "wb") as fh:
                writer.write(fh)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    except (OSError, ValueError) as exc:
        return f"PDF 编辑失败：{exc}"
    except Exception as exc:
        return f"PDF 编辑失败：{type(exc).__name__}: {exc}"

    return f"PDF 已生成：{_display(target)}（{len(writer.pages)} 页）"


def _replace_in_runs(runs, old: str, new: str) -> int:
    """跨相邻文本 run 替换，尽量保留首尾 run 的原格式。"""
    combined = "".join(run.text for run in runs)
    search_end = len(combined)
    count = 0
    while True:
        start = combined.rfind(old, 0, search_end)
        if start < 0:
            return count
        end = start + len(old)
        offset = 0
        first = last = None
        first_offset = last_offset = 0
        for index, run in enumerate(runs):
            run_end = offset + len(run.text)
            if first is None and start < run_end:
                first, first_offset = index, start - offset
            if first is not None and end <= run_end:
                last, last_offset = index, end - offset
                break
            offset = run_end
        if first is None or last is None:
            return count
        prefix = runs[first].text[:first_offset]
        suffix = runs[last].text[last_offset:]
        if first == last:
            runs[first].text = prefix + new + suffix
        else:
            runs[first].text = prefix + new
            for index in range(first + 1, last):
                runs[index].text = ""
            runs[last].text = suffix
        count += 1
        search_end = start
        combined = "".join(run.text for run in runs)


@register(
    "office_edit",
    "编辑工作目录/项目内的 Office 文档并另存新文件。docx_replace 和 pptx_replace "
    "用于精确替换文字；xlsx_set 按 Sheet!A1 写入单元格。保留原文件，不能处理旧版 "
    ".doc/.xls/.ppt。",
    {
        "operation": {
            "type": "string",
            "enum": ["docx_replace", "pptx_replace", "xlsx_set"],
            "description": "Word/PPT 文字替换，或 Excel 单元格写入",
        },
        "path": {"type": "string", "description": "源 Office 文档路径"},
        "output": {"type": "string", "description": "新文档路径，扩展名须与源文件相同"},
        "find": {"type": "string", "description": "Word/PPT 中要查找的精确文字"},
        "replace": {"type": "string", "description": "Word/PPT 的替换文字，可为空"},
        "changes": {
            "type": "object",
            "description": "Excel 修改，例如 {\"Sheet1!A1\": \"项目\", \"B2\": 100}",
        },
    },
    required=["operation", "path", "output"],
)
def _office_edit(
    operation: str,
    path: str,
    output: str,
    find: str = "",
    replace: str = "",
    changes: dict | None = None,
) -> str:
    operation = (operation or "").strip().lower()
    extensions = {
        "docx_replace": ".docx",
        "pptx_replace": ".pptx",
        "xlsx_set": ".xlsx",
    }
    if operation not in extensions:
        return "Office 编辑失败：operation 只能是 docx_replace / pptx_replace / xlsx_set"
    try:
        source = _path_in_workspace(path)
        target = _path_in_workspace(output)
        extension = extensions[operation]
        if not os.path.isfile(source) or os.path.splitext(source)[1].lower() != extension:
            raise ValueError(f"源文件不存在或不是 {extension}：{_display(source)}")
        if os.path.splitext(target)[1].lower() != extension:
            raise ValueError(f"输出文件必须使用 {extension} 扩展名")
        if os.path.abspath(source) == os.path.abspath(target):
            raise ValueError("output 不能与源文件相同")
        if os.path.exists(target):
            raise ValueError(f"输出已存在，请换一个文件名：{_display(target)}")
        if operation.endswith("_replace") and not find:
            raise ValueError("请提供非空的 find")
        if operation == "xlsx_set" and not isinstance(changes, dict):
            raise ValueError("请用 changes 提供至少一个单元格修改")

        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + ".nyalume-" + uuid.uuid4().hex[:8] + ".tmp"
        count = 0
        try:
            if operation == "docx_replace":
                from docx import Document

                document = Document(source)
                paragraphs = list(document.paragraphs)
                for table in document.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            paragraphs.extend(cell.paragraphs)
                count = sum(
                    _replace_in_runs(paragraph.runs, find, replace)
                    for paragraph in paragraphs
                )
                if count:
                    document.save(temporary)
            elif operation == "pptx_replace":
                from pptx import Presentation

                presentation = Presentation(source)
                paragraphs = []
                for slide in presentation.slides:
                    for shape in slide.shapes:
                        if getattr(shape, "has_text_frame", False):
                            paragraphs.extend(shape.text_frame.paragraphs)
                        if getattr(shape, "has_table", False):
                            for row in shape.table.rows:
                                for cell in row.cells:
                                    paragraphs.extend(cell.text_frame.paragraphs)
                count = sum(
                    _replace_in_runs(paragraph.runs, find, replace)
                    for paragraph in paragraphs
                )
                if count:
                    presentation.save(temporary)
            else:
                from openpyxl import load_workbook

                if not changes:
                    raise ValueError("请用 changes 提供至少一个单元格修改")
                workbook = load_workbook(source)
                try:
                    for address, value in changes.items():
                        if not isinstance(address, str):
                            raise ValueError("changes 的键必须是 Sheet!A1 或 A1 形式的文本")
                        if not isinstance(value, (str, int, float, bool, type(None))):
                            raise ValueError(f"单元格 {address} 的值必须是文本、数字、布尔值或 null")
                        if "!" in address:
                            sheet_name, cell_address = address.rsplit("!", 1)
                        else:
                            sheet_name, cell_address = workbook.active.title, address
                        if sheet_name not in workbook.sheetnames:
                            raise ValueError(f"工作表不存在：{sheet_name}")
                        if not re.fullmatch(r"[A-Za-z]{1,3}[1-9]\d*", cell_address):
                            raise ValueError(f"单元格地址不正确：{address}")
                        workbook[sheet_name][cell_address] = value
                        count += 1
                    workbook.save(temporary)
                finally:
                    workbook.close()
            if not count:
                return f"没有找到「{find}」，未生成文件"
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    except (OSError, ValueError) as exc:
        return f"Office 编辑失败：{exc}"
    except Exception as exc:
        return f"Office 编辑失败：{type(exc).__name__}: {exc}"

    return f"Office 文档已生成：{_display(target)}（修改 {count} 处）"


@register(
    "pdf_ocr",
    "把 PDF 文字识别为 Markdown 或纯文本。已有文字层的页面直接提取；扫描页会渲染成图片，"
    "并发送给用户已配置的视觉模型识别，因此每次都要审批。一次最多 10 页；OCR 结果可能"
    "有误，重要内容必须人工复核。",
    {
        "path": {"type": "string", "description": "源 PDF 路径"},
        "output": {"type": "string", "description": "输出 .md 或 .txt 路径"},
        "pages": {
            "type": "string",
            "description": "可选页码，例如 1-3,5；留空表示全文（但不能超过 10 页）",
        },
    },
    required=["path", "output"],
)
def _pdf_ocr(path: str, output: str, pages: str = "") -> str:
    from pypdf import PdfReader

    try:
        source = _path_in_workspace(path)
        target = _path_in_workspace(output)
        if not os.path.isfile(source) or os.path.splitext(source)[1].lower() != ".pdf":
            raise ValueError(f"源 PDF 不存在：{_display(source)}")
        if os.path.splitext(target)[1].lower() not in {".md", ".txt"}:
            raise ValueError("输出文件必须使用 .md 或 .txt 扩展名")
        if os.path.exists(target):
            raise ValueError(f"输出已存在，请换一个文件名：{_display(target)}")

        reader = PdfReader(source)
        total = len(reader.pages)
        if total < 1:
            raise ValueError("PDF 没有页面")
        selected = _pdf_page_numbers(pages, total) if pages else list(range(total))
        if len(selected) > 10:
            raise ValueError(f"一次最多识别 10 页；当前选择了 {len(selected)} 页，请用 pages 分批")

        extracted: dict[int, str] = {}
        scanned: list[int] = []
        for index in selected:
            _check_run_control()
            try:
                text = (reader.pages[index].extract_text() or "").strip()
            except Exception:
                text = ""
            if len(re.sub(r"\s+", "", text)) >= 20:
                extracted[index] = text
            else:
                scanned.append(index)
        if scanned and not vision_configured():
            raise ValueError("检测到扫描页，但尚未配置可看图的视觉模型")

        temporary_dir = os.path.join(
            _base_root(), ".nyalume", "tmp", "ocr-" + uuid.uuid4().hex[:8]
        )
        if scanned:
            import pypdfium2 as pdfium

            os.makedirs(temporary_dir, exist_ok=True)
            pdf = None
            try:
                pdf = pdfium.PdfDocument(source)
                for index in scanned:
                    _check_run_control()
                    page = pdf[index]
                    bitmap = page.render(scale=2)
                    image_path = os.path.join(temporary_dir, f"page-{index + 1}.png")
                    try:
                        bitmap.to_pil().save(image_path, format="PNG")
                    finally:
                        bitmap.close()
                        page.close()
                    text = _vision_describe(
                        image_path,
                        "你是 OCR 工具。只逐字转写页面中实际可见的文字，保留段落和列表结构；"
                        "不要解释、概括或补写。无法辨认的位置写 [无法辨认]。",
                    ).strip()
                    if text.startswith(
                        ("看图失败", "Nyalume 还不能", "图片不存在", "（视觉模型没有")
                    ):
                        raise ValueError(f"第 {index + 1} 页识别失败：{text}")
                    extracted[index] = text
            finally:
                if pdf is not None:
                    pdf.close()
                shutil.rmtree(temporary_dir, ignore_errors=True)

        markdown = os.path.splitext(target)[1].lower() == ".md"
        chunks = []
        for index in selected:
            heading = f"## 第 {index + 1} 页" if markdown else f"===== 第 {index + 1} 页 ====="
            chunks.append(f"{heading}\n\n{extracted.get(index, '')}".rstrip())
        body = "\n\n".join(chunks) + "\n"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
        _record_undo("write", target)
    except (OSError, ValueError) as exc:
        return f"PDF OCR 失败：{exc}"
    except Exception as exc:
        return f"PDF OCR 失败：{type(exc).__name__}: {exc}"

    return (
        f"PDF 文字已保存：{_display(target)}（{len(selected)} 页，"
        f"其中 {len(scanned)} 页使用视觉 OCR；请人工复核重要内容）"
    )


# ---------- 受限执行器：像 Codex 一样能跑代码，但限制在项目/工作目录内 ----------

_RUNNABLE = {"python", "py", "node"}
_RUN_TIMEOUT_DEFAULT = 30
_RUN_TIMEOUT_MAX = 120
_RUN_OUTPUT_LIMIT = 200 * 1024
_SECRET_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD")


def _split_command(cmd: str) -> list[str]:
    """把命令拆成 argv（不做转义；引号内空格算一个参数）。"""
    parts: list[str] = []
    buf = ""
    quote = ""
    for ch in cmd or "":
        if quote:
            if ch == quote:
                quote = ""
            else:
                buf += ch
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if buf:
                parts.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return parts


def _safe_env() -> dict:
    """给子进程的干净环境：保留常用项但剥掉含密钥特征的变量。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(hint in k.upper() for hint in _SECRET_HINTS)
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


@register(
    "run_code",
    "在项目/工作目录内运行代码（类似 Codex 执行命令）。"
    "用户说“跑一下/运行/执行/测试这段代码”时调用。"
    "只支持 python、py、node；以当前操作根为工作目录，无法弹出窗口、无法交互输入；"
    "默认 30 秒超时。复杂脚本建议先用 file_write 存成文件再运行。",
    {
        "command": {
            "type": "string",
            "description": "要执行的命令，例如 python 分析.py 或 python -c \"print('hi')\"",
        },
        "cwd": {
            "type": "string",
            "description": "可选：相对当前根的运行目录，留空用根目录",
        },
        "timeout": {
            "type": "integer",
            "description": "可选超时秒数，默认 30，最大 120",
        },
    },
    required=["command"],
)
def _run_code(command: str, cwd: str = "", timeout: int = _RUN_TIMEOUT_DEFAULT) -> str:
    argv = _split_command(command)
    if not argv:
        return "用法：run_code 需要命令，例如 python main.py"
    try:
        timeout = max(1, min(_RUN_TIMEOUT_MAX, int(timeout or _RUN_TIMEOUT_DEFAULT)))
    except (TypeError, ValueError):
        timeout = _RUN_TIMEOUT_DEFAULT

    first = argv[0].lower()
    if first == "python" or first == "py":
        argv[0] = sys.executable  # 复用当前 venv，依赖齐全
    elif first == "node":
        node = shutil.which("node")
        if not node:
            return "没有找到 node，先安装或用 python"
        argv[0] = node
    else:
        return "只允许运行 python / py / node（其他命令会被拒绝）"

    try:
        root = _path_in_workspace(cwd)
        if not os.path.isdir(root):
            return f"运行目录不存在：{root}"
    except ValueError as e:
        return f"运行失败：{e}"

    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        deadline = _context_state()["deadline"]
        if deadline:
            timeout = min(timeout, max(1, int(deadline - time.monotonic())))
        proc = subprocess.Popen(
            argv,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_safe_env(),
            creationflags=flags,
        )
    except OSError as e:
        return f"启动失败：{e}"

    started = time.monotonic()
    while True:
        state = _run_control_state()
        elapsed = time.monotonic() - started
        if state or elapsed >= timeout:
            proc.kill()
            proc.communicate()
            if state == "cancelled":
                return "任务已取消，正在运行的脚本已终止。"
            return f"超时（{timeout} 秒）已终止。脚本可能死循环或太慢，先检查再跑。"
        try:
            stdout, stderr = proc.communicate(timeout=min(0.2, timeout - elapsed))
            break
        except subprocess.TimeoutExpired:
            continue

    def clip(name: str, text: str) -> str:
        if not text:
            return ""
        if len(text) > _RUN_OUTPUT_LIMIT:
            text = text[:_RUN_OUTPUT_LIMIT] + f"\n…（输出过长，截断 {len(text)} 字符）"
        return f"---- {name} ----\n{text}\n"

    return (
        f"退出码 {proc.returncode}\n"
        + clip("stdout", stdout)
        + clip("stderr", stderr)
    ).rstrip()


# ---------- 上网下载文件 ----------

_DOWNLOAD_DIR = "downloads"
_MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024


@register(
    "web_download",
    "从网址下载文件到工作目录的 downloads 子目录。"
    "用户说“下载/拉取某个文件”时，先用 web_search 找到真实文件链接再调用；"
    "下载成功且是支持的文档类型会自动导入文档库，之后可直接基于它回答。",
    {
        "url": {"type": "string", "description": "文件直链，如 https://…/report.pdf"},
        "save_as": {
            "type": "string",
            "description": "可选：保存的文件名（相对 downloads 目录），留空自动取",
        },
    },
    required=["url"],
)
def _web_download(url: str, save_as: str = "") -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "下载失败：只支持 http/https 链接"
    try:
        req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            parts = []
            total = 0
            while total <= _MAX_DOWNLOAD_BYTES:
                _check_run_control()
                part = resp.read(min(64 * 1024, _MAX_DOWNLOAD_BYTES + 1 - total))
                if not part:
                    break
                parts.append(part)
                total += len(part)
            data = b"".join(parts)
            if len(data) > _MAX_DOWNLOAD_BYTES:
                return f"下载失败：文件超过 {_MAX_DOWNLOAD_BYTES // 1024 // 1024}MB，暂不支持"
            name = (save_as or "").strip()
            if not name:
                name = os.path.basename(urllib.parse.urlparse(url).path) or "download.bin"
            name = os.path.basename(name.replace("\\", "/"))  # 防路径穿越
            down_dir = os.path.join(_base_root(), _DOWNLOAD_DIR)
            os.makedirs(down_dir, exist_ok=True)
            target = os.path.join(down_dir, name)
            n = 1
            while os.path.exists(target):  # 重名不覆盖，自动加 (1)
                stem, ext = os.path.splitext(name)
                target = os.path.join(down_dir, f"{stem} ({n}){ext}")
                n += 1
            with open(target, "wb") as fh:
                fh.write(data)
    except Exception as e:
        return f"下载失败：{type(e).__name__}: {e}"

    msg = f"已下载 {_human_size(len(data))} 到 {_display(target)}"
    if os.path.splitext(target)[1].lower() in _DOC_EXTS:
        msg += "\n" + _add_documents(target)
    return msg


# ---------- 浏览器：读/点/填/截图（系统 Edge，无需下载内核） ----------

_BROWSER_TTL = 300  # 5 分钟没人用自动关，释放 Edge


def _browser_page():
    """取（或懒启动）常驻无头 Edge 页面；5 分钟闲置自动关。"""
    from playwright.sync_api import sync_playwright

    state = _context_state()
    browser_state = state["browser"]
    if browser_state and time.time() - browser_state["used"] > _BROWSER_TTL:
        _browser_close()
        browser_state = None
    if browser_state is None:
        pw = browser = None
        try:
            pw = sync_playwright().start()
            try:
                browser = pw.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = pw.chromium.launch(headless=True)  # 需要 playwright install chromium
            page = browser.new_page(locale="zh-CN")
        except Exception:
            for obj in (browser, pw):
                try:
                    obj.close()
                except Exception:
                    pass
            raise
        browser_state = {"pw": pw, "browser": browser, "page": page, "used": time.time()}
        state["browser"] = browser_state
    browser_state["used"] = time.time()
    return browser_state["page"]


def _browser_close() -> None:
    state = _context_state()
    browser_state = state["browser"]
    if browser_state is None:
        return
    for obj in ("browser", "pw"):
        try:
            browser_state[obj].close()
        except Exception:
            pass
    state["browser"] = None


def _browser_goto(page, url: str) -> None:
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    page.goto(url, timeout=20000, wait_until="domcontentloaded")


def _browser_no_playwright() -> str:
    return "浏览器工具未安装：请运行 pip install playwright（用系统 Edge，无需下载浏览器内核）"


def _browser_try_start() -> tuple:
    """返回 (page, None) 或 (None, 错误文本)。"""
    try:
        return _browser_page(), None
    except ImportError:
        return None, _browser_no_playwright()
    except Exception as e:
        return None, f"启动浏览器失败：{type(e).__name__}: {str(e)[:200]}"


@register(
    "browser_read",
    "用系统 Edge 打开网页并返回标题+正文文本。"
    "适合 web_download/搜索拿不到的动态渲染、需要 JS 的页面"
    "（如点击加载、登录后跳转的内容页）；只读，不点击不填表。",
    {
        "url": {"type": "string", "description": "要打开的网址，如 https://example.com/page"},
        "max_chars": {
            "type": "integer",
            "description": "最多返回多少字符正文（默认 8000）",
        },
        "wait_ms": {
            "type": "integer",
            "description": "打开后额外等待毫秒数，让 JS 渲染完（默认 2500）",
        },
    },
    required=["url"],
)
def _browser_read(url: str, max_chars: int = 8000, wait_ms: int = 2500) -> str:
    page, err = _browser_try_start()
    if err:
        return err
    url = (url or "").strip()
    max_chars = max(500, min(30000, int(max_chars)))
    wait_ms = max(0, min(15000, int(wait_ms)))
    try:
        _browser_goto(page, url)
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        title = (page.title() or "").strip()
        text = page.inner_text("body")
    except Exception as e:
        return f"打开网页失败：{type(e).__name__}: {str(e)[:300]}"
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n…（正文过长，共 {len(text)} 字符，已截断）"
    head = f"标题：{title}\n" if title else ""
    return f"{head}正文：\n{text}"


@register(
    "browser_click",
    "点击当前网页里文字为 label 的按钮/链接（传 url 会先打开该页再点）。"
    "nth 表示点第几个同名元素（0 开始）。"
    "付款/下单/删除/发消息等不可逆操作前，先向用户确认再点。",
    {
        "label": {"type": "string", "description": "要点击的按钮/链接上的文字，如 登录/下一页"},
        "url": {"type": "string", "description": "可选：先打开这个网址再点"},
        "nth": {"type": "integer", "description": "第几个同名元素，默认 0"},
    },
    required=["label"],
)
def _browser_click(label: str, url: str = "", nth: int = 0) -> str:
    page, err = _browser_try_start()
    if err:
        return err
    label = (label or "").strip()
    nth = max(0, int(nth or 0))
    try:
        if url:
            _browser_goto(page, url)
        clicked = False
        for make in (
            lambda: page.get_by_role("button", name=label, exact=False).nth(nth),
            lambda: page.get_by_role("link", name=label, exact=False).nth(nth),
            lambda: page.get_by_text(label, exact=False).nth(nth),
        ):
            try:
                make().click(timeout=5000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            buttons = []
            try:
                buttons = page.get_by_role("button").all_inner_texts()[:15]
            except Exception:
                pass
            sample = "、".join(b.strip()[:10] for b in buttons if b.strip())
            return (
                f"没找到文字为「{label}」的可点元素。"
                + (f" 当前页按钮示例：{sample}" if sample else " 当前页没有可见按钮。")
                + " 可以先用 browser_screenshot 截图，让用户指认后换文字重试。"
            )
    except Exception as e:
        return f"点击失败：{type(e).__name__}: {str(e)[:200]}"
    return f"已点击「{label}」。可以 browser_screenshot 截图给用户确认页面变化。"


@register(
    "browser_fill",
    "往网页表单里输入内容：label 写输入框的提示文字/名称（如 用户名、搜索、手机号），"
    "value 写要输入的内容。传 url 会先打开页面再填。不代替用户提交。",
    {
        "label": {"type": "string", "description": "输入框的名称/占位文字，如 用户名、搜索关键词"},
        "value": {"type": "string", "description": "要输入的内容"},
        "url": {"type": "string", "description": "可选：先打开这个网址再填"},
        "nth": {"type": "integer", "description": "填第几个同名输入框，默认 0"},
    },
    required=["label", "value"],
)
def _browser_fill(label: str, value: str, url: str = "", nth: int = 0) -> str:
    page, err = _browser_try_start()
    if err:
        return err
    label = (label or "").strip()
    value = str(value or "")
    nth = max(0, int(nth or 0))
    try:
        if url:
            _browser_goto(page, url)
        field = None
        for make in (
            lambda: page.get_by_label(label, exact=False).nth(nth),
            lambda: page.get_by_placeholder(label).nth(nth),
            lambda: page.get_by_role("textbox", name=label, exact=False).nth(nth),
            lambda: page.get_by_role("searchbox", name=label, exact=False).nth(nth),
        ):
            try:
                loc = make()
                if loc.count() > 0:
                    field = loc
                    break
            except Exception:
                continue
        if field is None:
            # 兜底：按 placeholder/title/aria-label/name 含 label 找 input
            inputs = page.locator("input, textarea")
            found = inputs.evaluate_all(
                """(els, kw) => {
                    const out = [];
                    for (const el of els) {
                        const pool = [el.placeholder, el.title, el.getAttribute('aria-label'), el.name, el.type];
                        if (pool.some(v => v && String(v).toLowerCase().includes(kw.toLowerCase()))) out.push(el);
                    }
                    return out.length;
                }""",
                label,
            )
            if found > 0:
                field = inputs.nth(nth)
        if field is None:
            return f"没找到名称含「{label}」的输入框。可先 browser_screenshot 截图给用户看。"
        field.fill(value, timeout=5000)
    except Exception as e:
        return f"填写失败：{type(e).__name__}: {str(e)[:200]}"
    return f"已在「{label}」处输入完成。要不要 browser_screenshot 截图确认，或继续点提交按钮？"


@register(
    "browser_screenshot",
    "把当前网页截图保存成 PNG（传 url 会先打开该页再截）。"
    "截图文件在工作目录 downloads/screenshots/ 下，可让用户打开确认页面状态。",
    {
        "url": {"type": "string", "description": "可选：先打开这个网址再截图"},
        "save_as": {"type": "string", "description": "可选：文件名（自动补 .png），默认按时间命名"},
    },
)
def _browser_screenshot(url: str = "", save_as: str = "") -> str:
    page, err = _browser_try_start()
    if err:
        return err
    try:
        if url:
            _browser_goto(page, url)
            page.wait_for_timeout(800)
        name = (save_as or "").strip() or time.strftime("shot_%Y%m%d_%H%M%S.png")
        if not name.lower().endswith(".png"):
            name += ".png"
        shot_dir = os.path.join(_base_root(), "downloads", "screenshots")
        os.makedirs(shot_dir, exist_ok=True)
        target = os.path.join(shot_dir, os.path.basename(name.replace("\\", "/")))
        page.screenshot(path=target, full_page=False)
    except Exception as e:
        return f"截图失败：{type(e).__name__}: {str(e)[:200]}"
    msg = f"截图已保存：{_display(target)}"
    if vision_configured():
        desc = _vision_describe(target)
        if not desc.startswith("看图失败") and not desc.startswith("视觉模型未配置"):
            msg += "\n画面内容：" + desc
    return msg


@register(
    "describe_image",
    "让 Nyalume 看一张本地图片并返回文字描述（主体、界面元素、按钮文字、正文内容等）。"
    "用户发图/指图、或截图后需要确认页面内容时调用。",
    {
        "path": {
            "type": "string",
            "description": "图片路径（工作区/项目内相对路径；全权或已审批可用绝对路径）",
        },
        "question": {
            "type": "string",
            "description": "可选：想让它重点看什么，例如“这个按钮的文字是什么”",
        },
    },
    required=["path"],
)
def _describe_image_tool(path: str, question: str = "") -> str:
    p = (path or "").strip()
    if not p:
        return "看图失败：缺少图片路径"
    if not os.path.isabs(p):
        try:
            p = _path_in_workspace(p)
        except ValueError:
            return (
                "看图失败：图片要在当前授权目录内。可把图片拖进网页，"
                "或把所在文件夹加入项目授权后再试。"
            )
    if not os.path.isfile(p):
        return f"看图失败：文件不存在 {p}"
    return _vision_describe(p, question)


@register(
    "browser_close",
    "关闭无头浏览器、释放内存（通常不需要手动调用；5 分钟不用会自动关）。",
    {},
)
def _browser_close_tool() -> str:
    _browser_close()
    return "浏览器已关闭。"


# ---------- 工具 5：网页搜索（必应，免密钥） ----------

_SEARCH_TIMEOUT = 12
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _html_to_text(raw: str) -> str:
    """去掉 HTML 标签并折叠空白，只留可见文本。"""
    text = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", text).strip()


def _search_terms(query: str) -> list[str]:
    """拆出值得补搜的核心词：长中文查询常让必应返回单字字典。"""
    runs = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    terms = (
        [f'"{query}"', query]
        if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", query)
        else [query]
    )
    for run in runs:
        terms.append(run)
        if len(run) > 5:
            terms += [run[i : i + 4] for i in range(0, len(run) - 3, 2)]
    return list(dict.fromkeys(terms))[:4]


def _search_quality(title: str, url: str, query: str = "", snippet: str = "") -> int:
    """先看查询相关度，再看站点质量；不让无关百科靠域名挤到前面。"""
    low = url.lower()
    if any(k in low for k in ("zidian", "hanyu", "chagushici", "hgcha", "guoxue")):
        return -8
    score = 0
    haystack = re.sub(r"\W+", "", title + urllib.parse.unquote(url) + snippet).lower()
    needle = re.sub(r"\W+", "", query).lower()
    if needle:
        if needle in haystack:
            score += 40
        else:
            chinese = "".join(re.findall(r"[\u4e00-\u9fff]", needle))
            if len(chinese) >= 2:
                coverage = sum(char in haystack for char in set(chinese)) / len(set(chinese))
                score += 8 if coverage >= 0.75 else -8 if coverage >= 0.5 else -24
            else:
                words = re.findall(r"[a-z0-9]{2,}", needle)
                score += 6 if any(word in haystack for word in words) else -18
    if any(
        k in low
        for k in ("baike.baidu", "moegirl", "mihoyo", "bilibili", "zhihu", "douban", "wikipedia")
    ):
        score += 4
    if any(k in title for k in ("百科", "萌娘", "wiki", "米哈游", "bilibili", "知乎")):
        score += 6
    if len(title) >= 8:
        score += 2
    return score


def _bing_search(query: str, limit: int) -> list[dict]:
    """走必应 RSS 接口：无需 key、返回干净 XML，国内可直接访问。"""
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"format": "rss", "q": query}
    )
    req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
    with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)

    results: list[dict] = []
    seen_titles: set[str] = set()
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = _html_to_text(item.findtext("description") or "")
        if (
            not title
            or title in seen_titles
            or not link.startswith(("http://", "https://"))
        ):
            continue
        seen_titles.add(title)
        results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


@register(
    "web_search",
    "在互联网上搜索公开信息（必应网页搜索，无需 API key）。"
    "适合回答时效性强、模型训练数据里没有或不确定的问题：新闻、最新版本、"
    "第三方文档、某个人/公司/产品的最新情况等。搜索后请基于结果回答，可引用链接。"
    "若结果只有单字字典/明显不相关，不要断定“搜不到”，改用更短的核心词"
    "（如只保留人名或作品名）再搜一次。",
    {
        "query": {
            "type": "string",
            "description": "搜索关键词，尽量具体，中英文均可，例如 DeepSeek API 文档",
        },
        "max_results": {
            "type": "integer",
            "description": "最多返回几条结果，范围 1~8，默认 5",
        },
    },
    required=["query"],
)
def _web_search(query: str, max_results: int = 5) -> str:
    try:
        limit = max(1, min(8, int(max_results)))
    except (TypeError, ValueError):
        limit = 5
    query = (query or "").strip()[:150]
    if not query:
        return "搜索失败：缺少查询关键词"

    merged: dict[str, dict] = {}
    last_error: Exception | None = None
    for term in _search_terms(query):
        try:
            for item in _bing_search(term, limit):
                if item["url"] not in merged:
                    merged[item["url"]] = item
        except Exception as e:
            last_error = e
            continue
    if not merged and last_error is not None:
        return f"搜索失败（网络或解析错误）：{type(last_error).__name__}: {last_error}"
    ranked = sorted(
        merged.values(),
        key=lambda r: _search_quality(r["title"], r["url"], query, r["snippet"]),
        reverse=True,
    )
    results = [
        r for r in ranked
        if _search_quality(r["title"], r["url"], query, r["snippet"]) >= 0
    ][:limit]

    if not results:
        return f"没有搜到与「{query}」相关的结果，可以换个关键词再试。"
    lines = [f"「{query}」的搜索结果："]
    for index, item in enumerate(results, 1):
        lines.append(
            f"{index}. {item['title']}\n"
            f"   {item['url']}\n"
            f"   {item['snippet']}"
        )
    return "\n".join(lines)


# ---------- 工具 6~8：定时主动提醒（cron） ----------


@register(
    "create_reminder",
    "创建一条定时主动提醒。cron 用标准 5 段表达式：分 时 日 月 周"
    "（周：0/7=周日，1-6=周一到周六）。"
    "常见写法：'0 9 * * *'=每天 9 点；'*/30 * * * *'=每 30 分钟；"
    "'0 9 * * 1-5'=工作日 9 点。"
    "用户说“提醒我/定时/每天 X 点/每隔 X 分钟”时，把自然语言翻译成 cron"
    "后调用本工具，并在回复中告诉用户已设好、到点会主动提醒。",
    {
        "content": {
            "type": "string",
            "description": "提醒内容，例如 喝水 / 恢复 408 复习",
        },
        "cron": {
            "type": "string",
            "description": "5 段 cron 表达式，例如 0 9 * * *",
        },
    },
    required=["content", "cron"],
)
def _create_reminder(content: str, cron: str) -> str:
    try:
        rid = add_reminder(content, cron, one_shot=False)
    except ValueError as e:
        return f"创建提醒失败：{e}"
    return f"已设置定时提醒（#{rid}）：{content}，cron={cron}。到点 agent 会自己动，不用你再喊我。"


@register(
    "remind_me_in",
    "创建一条“一次性、多少分钟/小时后提醒我”的主动提醒（例如：1 分钟后提醒我喝水）。"
    "按分钟精度到点触发一次即结束，不是周期提醒。"
    "只有用户明确说“每隔/每 X 分钟/每天”等周期需求时才用 create_reminder 的 */n cron，"
    "绝不能把一次性提醒写成 */1 * * * *（那会变成每分钟都提醒）。",
    {
        "content": {
            "type": "string",
            "description": "提醒内容，例如 喝水 / 该休息了",
        },
        "minutes": {
            "type": "integer",
            "description": "多少分钟后提醒（正整数；1 小时=60）",
        },
    },
    required=["content", "minutes"],
)
def _remind_me_in(content: str, minutes: int) -> str:
    import datetime

    try:
        minutes = int(minutes)
        if minutes < 1 or minutes > 60 * 24 * 30:
            raise ValueError("分钟数需在 1 ~ 43200 之间")
        target = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
        if target.second > 0 or target.microsecond > 0:
            target = target.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        cron = f"{target.minute} {target.hour} * * *"
        rid = add_reminder(content, cron, one_shot=True)
    except ValueError as e:
        return f"创建提醒失败：{e}"
    return (
        f"已设好一次性提醒（#{rid}）：{minutes} 分钟后提醒「{content}」"
        f"（约 {target.strftime('%H:%M')} 触发）。到点 agent 会自己动。"
    )


@register(
    "list_reminders",
    "查看当前所有定时主动提醒。",
    {},
)
def _list_reminders() -> str:
    rows = list_reminders()
    if not rows:
        return "还没有定时提醒"
    return "\n".join(
        f"- #{r['id']} {r['content']}（cron: {r['cron']}）" for r in rows
    )


@register(
    "cancel_reminder",
    "取消一条定时提醒（按 id）。",
    {
        "reminder_id": {
            "type": "integer",
            "description": "提醒的 id，可用 list_reminders 查看",
        }
    },
    required=["reminder_id"],
)
def _cancel_reminder(reminder_id: int) -> str:
    ok = delete_reminder(int(reminder_id))
    return f"已取消提醒 #{reminder_id}" if ok else f"没有找到提醒 #{reminder_id}"


# ---------- 本地 Skill 管理 ----------


@register(
    "skill_list",
    "查看已经安装的 Skill、启停状态与声明的权限。",
    {},
)
def _skill_list() -> str:
    rows = skill_manager.list_skills()
    if not rows:
        return "还没有安装 Skill，可以在设置 → Skill 管理中导入，或给我一个 Skill 直链。"
    lines = []
    for row in rows:
        permissions = "、".join(item["label"] for item in row["permissions"]) or "仅提示与资料"
        lines.append(
            f"- {row['name']}（{'已启用' if row['enabled'] else '已停用'}；权限：{permissions}）"
        )
    return "\n".join(lines)


@register(
    "skill_install_url",
    "从用户明确提供的 http/https 直链下载安装 Skill（支持 ZIP 或 SKILL.md）。"
    "安装前界面一定会请求用户确认；不能安装网页搜索结果里未经用户指定的 Skill。",
    {
        "url": {
            "type": "string",
            "description": "用户明确提供的 ZIP 或 SKILL.md 下载直链",
        }
    },
    required=["url"],
)
def _skill_install_url(url: str) -> str:
    try:
        row = skill_manager.install_url(url)
    except Exception as exc:
        return f"Skill 安装失败：{type(exc).__name__}: {exc}"
    permissions = "、".join(item["label"] for item in row["permissions"]) or "仅提示与资料"
    return f"Skill「{row['name']}」已安装并启用；声明权限：{permissions}。"


@register(
    "skill_read_resource",
    "读取已启用 Skill 引用的 UTF-8 文本资源。path 必须是 Skill 目录内的相对路径。",
    {
        "skill_id": {"type": "string", "description": "Skill id（系统提示中会标出）"},
        "path": {"type": "string", "description": "相对路径，如 references/guide.md"},
    },
    required=["skill_id", "path"],
)
def _skill_read_resource(skill_id: str, path: str) -> str:
    try:
        return skill_manager.read_resource(skill_id, path)
    except ValueError as exc:
        return f"读取 Skill 资源失败：{exc}"


# 供 agent.py 使用的模型可见工具列表（注册完成后生成一次）
TOOL_SCHEMAS = tool_schemas()
