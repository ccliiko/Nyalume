"""替我审查：AI 代替用户审查危险操作。"""

import json
import os
import time
from typing import Literal

from . import llm, memory

# 审查策略
REVIEW_POLICIES = {
    "strict": {
        "name": "严格模式",
        "description": "高风险操作必须用户确认，中低风险由AI审查",
        "always_ask": [
            "file_delete",      # 删除文件
            "browser_click",    # 网页点击（可能是付款）
            "web_download",     # 下载文件
            "skill_install_url",# 安装技能
            "run_code",         # 运行代码
        ],
        "ai_review": [
            "file_write",       # 写文件
            "file_mkdir",       # 创建目录
            "file_move",        # 移动文件
            "pdf_edit",         # 编辑PDF
            "office_edit",      # 编辑Office
            "browser_fill",     # 填写表单
            "browser_read",     # 读取网页
            "browser_screenshot",# 截图
        ],
        "auto_allow": [
            "search_files",     # 搜索文件
            "search_docs",      # 搜索文档
            "web_search",       # 联网搜索
            "get_current_time", # 获取时间
            "calculator",       # 计算
            "save_note",        # 保存便签
            "list_notes",       # 列出便签
            "delete_note",      # 删除便签
            "search_memory",    # 搜索记忆
            "pet_status",       # 桌宠状态
            "pet_perform",      # 桌宠动作
            "pet_configure",    # 桌宠设置
        ],
    },
    "moderate": {
        "name": "中等模式",
        "description": "只有极高风险操作需要用户确认",
        "always_ask": [
            "file_delete",
            "skill_install_url",
            "web_download",
        ],
        "ai_review": [
            "file_write", "file_mkdir", "file_move",
            "pdf_edit", "office_edit", "run_code",
            "browser_click", "browser_fill", "browser_screenshot",
        ],
        "auto_allow": [
            "search_files", "search_docs", "web_search", "get_current_time",
            "calculator", "save_note", "list_notes", "delete_note",
            "search_memory", "pet_status", "pet_perform", "pet_configure",
            "browser_read",
        ],
    },
    "permissive": {
        "name": "宽松模式",
        "description": "只有删除和付款需要用户确认",
        "always_ask": [
            "file_delete",
        ],
        "ai_review": [
            "file_write", "file_mkdir", "file_move",
            "pdf_edit", "office_edit", "run_code",
            "browser_click", "browser_fill", "browser_read",
            "browser_screenshot", "web_download", "skill_install_url",
        ],
        "auto_allow": [
            "search_files", "search_docs", "web_search", "get_current_time",
            "calculator", "save_note", "list_notes", "delete_note",
            "search_memory", "pet_status", "pet_perform", "pet_configure",
        ],
    },
}

# 审查历史（内存中，会话级）
_review_history: dict[str, list[dict]] = {}


def get_review_policy(session_id: str) -> str:
    """获取当前会话的审查策略。"""
    return memory.get_setting("review_policy", "moderate")


def set_review_policy(session_id: str, policy: str) -> None:
    """设置审查策略。"""
    if policy in REVIEW_POLICIES:
        memory.set_setting("review_policy", policy)


def get_policy_config(policy: str) -> dict:
    """获取策略配置。"""
    return REVIEW_POLICIES.get(policy, REVIEW_POLICIES["moderate"])


def classify_tool(tool_name: str, arguments: dict) -> Literal["always_ask", "ai_review", "auto_allow"]:
    """分类工具风险级别。"""
    policy = get_review_policy("")
    config = get_policy_config(policy)
    
    # 检查是否在对应列表中
    if tool_name in config.get("always_ask", []):
        return "always_ask"
    if tool_name in config.get("ai_review", []):
        return "ai_review"
    if tool_name in config.get("auto_allow", []):
        return "auto_allow"
    
    # 默认：未知工具按 ai_review 处理
    return "ai_review"


def build_review_context(tool_name: str, arguments: dict, session_id: str) -> str:
    """构建审查上下文。"""
    policy = get_review_policy(session_id)
    config = get_policy_config(policy)
    risk_level = classify_tool(tool_name, arguments)
    
    # 获取最近的审查历史
    history = _review_history.get(session_id, [])[-5:]
    
    context = f"""你是一个操作审查助手。用户正在使用 AI 助手执行操作。

## 当前操作
- 工具：{tool_name}
- 参数：{json.dumps(arguments, ensure_ascii=False)[:500]}
- 风险级别：{risk_level}
- 当前策略：{config['name']}

## 策略说明
{config['description']}

## 审查历史（最近5条）
"""
    if history:
        for h in history:
            context += f"- {h['tool']}({h.get('desc', '')}): {'批准' if h['approved'] else '拒绝'} - {h['reason']}\n"
    else:
        context += "无历史记录\n"
    
    context += """
## 审查规则

### 必须拒绝的情况（无论策略如何）：
1. 删除系统文件或关键配置文件
2. 访问明显不安全的网站（钓鱼、恶意软件）
3. 执行可能损坏数据的命令
4. 填写敏感信息（密码、银行卡）到不信任的网站

### 应该拒绝的情况：
1. 操作范围明显超出用户需求
2. 操作可能产生不可逆后果且用户未明确确认
3. 操作涉及大量文件或数据
4. 操作时机不合适（如深夜执行大量操作）

### 可以批准的情况：
1. 操作符合用户明确意图
2. 操作可逆或有备份
3. 操作范围适当
4. 操作风险可控

## 输出要求
请以JSON格式输出审查决定：
{
    "approved": true/false,
    "reason": "批准或拒绝的原因（简洁明了）",
    "confidence": 0.0-1.0,
    "suggestions": "如果有建议，写在这里"
}

只输出JSON，不要输出其他内容。"""
    
    return context


def review_tool_call(
    session_id: str,
    tool_name: str,
    arguments: dict,
    tool_desc: str,
) -> dict:
    """审查工具调用，返回决定。
    
    返回格式：
    {
        "approved": bool,
        "reason": str,
        "confidence": float,
        "suggestions": str,
        "reviewed_by": "ai" | "policy" | "user"
    }
    """
    risk_level = classify_tool(tool_name, arguments)
    
    # auto_allow：直接批准
    if risk_level == "auto_allow":
        return {
            "approved": True,
            "reason": "低风险操作，自动批准",
            "confidence": 1.0,
            "suggestions": "",
            "reviewed_by": "policy",
        }
    
    # always_ask：必须用户确认
    if risk_level == "always_ask":
        return {
            "approved": None,  # 需要用户确认
            "reason": "高风险操作，需要用户确认",
            "confidence": 1.0,
            "suggestions": "",
            "reviewed_by": "user",
        }
    
    # ai_review：AI 审查
    try:
        context = build_review_context(tool_name, arguments, session_id)
        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": f"请审查这个操作：{tool_name}\n{tool_desc}"},
        ]
        
        response = llm.chat_text(messages, timeout=30)
        
        # 解析JSON响应
        response = response.strip()
        # 移除可能的markdown代码块标记
        if response.startswith("```"):
            response = response.split("\n", 1)[-1]
        if response.endswith("```"):
            response = response.rsplit("```", 1)[0]
        
        result = json.loads(response)
        
        # 记录审查历史
        _review_history.setdefault(session_id, []).append({
            "tool": tool_name,
            "desc": tool_desc,
            "arguments": arguments,
            "approved": result.get("approved", False),
            "reason": result.get("reason", ""),
            "timestamp": time.time(),
        })
        
        return {
            "approved": result.get("approved", False),
            "reason": result.get("reason", "AI审查完成"),
            "confidence": result.get("confidence", 0.8),
            "suggestions": result.get("suggestions", ""),
            "reviewed_by": "ai",
        }
        
    except Exception as e:
        # AI审查失败，降级为需要用户确认
        return {
            "approved": None,
            "reason": f"AI审查失败（{e}），需要用户确认",
            "confidence": 0.0,
            "suggestions": "",
            "reviewed_by": "user",
        }


def get_review_history(session_id: str, limit: int = 20) -> list[dict]:
    """获取审查历史。"""
    return _review_history.get(session_id, [])[-limit:]


def clear_review_history(session_id: str) -> None:
    """清除审查历史。"""
    _review_history.pop(session_id, None)


def get_review_stats(session_id: str) -> dict:
    """获取审查统计。"""
    history = _review_history.get(session_id, [])
    if not history:
        return {"total": 0, "approved": 0, "denied": 0, "user_reviewed": 0}
    
    approved = sum(1 for h in history if h.get("approved"))
    denied = sum(1 for h in history if not h.get("approved"))
    user_reviewed = sum(1 for h in history if h.get("reviewed_by") == "user")
    
    return {
        "total": len(history),
        "approved": approved,
        "denied": denied,
        "user_reviewed": user_reviewed,
        "ai_reviewed": len(history) - user_reviewed,
    }
