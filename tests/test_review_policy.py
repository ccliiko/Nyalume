"""联网搜索是只读操作：三档策略都应直接放行，不再走 AI 审查。"""

from nyalume.core import review_agent as r


def test_web_search_is_allowed_in_every_policy():
    for policy in r.REVIEW_POLICIES:
        r.memory.set_setting("review_policy", policy)
        assert r.classify_tool("web_search", {"query": "x"}) == "auto_allow"
        decision = r.review_tool_call("s", "web_search", {"query": "x"}, "联网搜索「x」")
        assert decision["approved"] is True and decision["reviewed_by"] == "policy"
