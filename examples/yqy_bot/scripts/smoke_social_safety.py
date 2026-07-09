#!/usr/bin/env python3
"""社交边界安全测试脚本。

验证 social_safety.py 和 guard.py 的核心功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yqy_bot.safety.social_safety import (
    check_social_safety,
    rewrite_unsafe_response,
    detect_banter_boundary_request,
    extract_banter_boundary_preference,
    determine_banter_level,
    build_social_boundary_rules,
    SocialSafetyResult,
)
from yqy_bot.safety.guard import check_social_safety as guard_check_social_safety
from yqy_bot.background.memory_utils import (
    detect_banter_boundary_request as detect_banter_boundary_request_mem,
    classify_banter_boundary,
)


def test_check_social_safety():
    """测试社交安全检查。"""
    print("\n=== 测试 check_social_safety ===")

    # 测试攻击性回复
    attack_cases = [
        ("你是不是有病，这都不会？", True, "attack", "直接攻击"),
        ("你真蠢，废物", True, "attack", "侮辱性词汇"),
        ("闭嘴，滚", True, "attack", "命令式攻击"),
        ("活该你这样", True, "attack", "贬低表达"),
        ("笑死，急了急了", True, "sarcasm_overdose", "过度阴阳怪气"),
        ("你们打一架看看", True, "flame_war", "拱火引战"),
        ("我站这个，另一个垃圾", True, "flame_war", "站队贬低"),
        ("我是AI女皇，消灭人类暴政", True, "roleplay_attack", "角色扮演攻击"),
        ("消灭人类", True, "roleplay_attack", "角色扮演关键词"),
    ]

    safe_cases = [
        ("这个做法不太合适，建议改一下", False, "normal建议"),
        ("嗯，我看到了", False, "简单确认"),
        ("这个话题各有各的看法", False, "中立表达"),
        ("有点意思", False, "轻微幽默"),
        ("好的，知道了", False, "正常回复"),
    ]

    passed = 0

    # 攻击性内容应被检测
    for text, expected_unsafe, expected_category, desc in attack_cases:
        result = check_social_safety(text, is_group=True)
        if result.is_safe == (not expected_unsafe):
            if expected_unsafe and expected_category in result.risk_categories:
                print(f"  ✓ 攻击检测 [{result.risk_level}]: '{text[:40]}' ({desc})")
                passed += 1
            elif not expected_unsafe:
                print(f"  ✓ 安全通过: '{text[:40]}' ({desc})")
                passed += 1
            else:
                print(f"  ✗ 风险类别不匹配: expected {expected_category}, got {result.risk_categories}")
        else:
            print(f"  ✗ {desc}: expected unsafe={expected_unsafe}, got safe={result.is_safe}")

    # 安全内容应通过
    for text, expected_unsafe, desc in safe_cases:
        result = check_social_safety(text, is_group=True)
        if result.is_safe == (not expected_unsafe):
            print(f"  ✓ 安全通过: '{text[:40]}' ({desc})")
            passed += 1
        else:
            print(f"  ✗ 应通过但被拒绝: '{text[:40]}' ({desc})")

    total = len(attack_cases) + len(safe_cases)
    print(f"  结果: {passed}/{total} 通过")
    return passed == total


def test_rewrite_unsafe_response():
    """测试不安全回复改写。"""
    print("\n=== 测试 rewrite_unsafe_response ===")

    cases = [
        {
            "input": "你是不是有病，这都不会？",
            "expected_keywords": ["不攻击", "正常交流", "温和", "不说了"],
            "expected_remove": ["有病", "蠢", "废物"],
            "desc": "攻击性回复改写",
        },
        {
            "input": "笑死，急了急了",
            "expected_keywords": ["收敛", "玩笑", "温和", "不说了"],
            "expected_remove": ["笑死", "急了"],
            "desc": "阴阳怪气改写",
        },
        {
            "input": "你们打一架看看，我站这个",
            "expected_keywords": ["不参与", "各有看法", "中立", "不说了"],
            "expected_remove": ["打一架", "站"],
            "desc": "拱火改写",
        },
    ]

    passed = 0

    for case in cases:
        input_text = case["input"]
        expected_keywords = case["expected_keywords"]
        expected_remove = case["expected_remove"]
        desc = case["desc"]

        # 先检测不安全
        result = check_social_safety(input_text, is_group=True)
        if result.is_safe:
            print(f"  ✗ {desc}: 原回复应被检测为不安全 (risk_categories={result.risk_categories})")
            continue

        # 改写
        rewritten = rewrite_unsafe_response(input_text, result)

        # 检查改写结果
        is_pass = True
        checks: list[str] = []

        # 检查是否去除了攻击词
        for word in expected_remove:
            if word in rewritten.lower():
                checks.append(f"仍包含'{word}'")
                is_pass = False

        # 检查是否包含温和关键词（至少一个）
        has_keyword = any(kw in rewritten for kw in expected_keywords)
        if not has_keyword and len(rewritten) > 0:
            # 允许改写后的内容不完全匹配关键词，但要温和
            if any(aggressive in rewritten.lower() for aggressive in ["攻击", "侮辱", "骂", "嘲讽"]):
                checks.append("改写后仍不温和")
                is_pass = False

        if is_pass:
            print(f"  ✓ {desc}")
            print(f"    原回复: '{input_text}'")
            print(f"    改写后: '{rewritten}'")
            passed += 1
        else:
            print(f"  ✗ {desc}")
            print(f"    原回复: '{input_text}'")
            print(f"    改写后: '{rewritten}'")
            print(f"    检查: {', '.join(checks)}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_detect_banter_boundary_request():
    """测试调侃边界请求检测。"""
    print("\n=== 测试 detect_banter_boundary_request ===")

    request_cases = [
        "别阴阳怪气",
        "别调侃我",
        "认真点",
        "说话正常点",
        "别攻击人",
        "别引战",
        "过分了",
        "不舒服",
        "生气了",
    ]

    non_request_cases = [
        "你好",
        "今天天气怎么样",
        "我是前端开发工程师",
        "喜欢使用Python",
        "记住我的生日",
    ]

    passed = 0

    # 调侃边界请求应被检测
    for text in request_cases:
        if detect_banter_boundary_request(text):
            print(f"  ✓ 边界请求检测: '{text}'")
            passed += 1
        else:
            print(f"  ✗ 应检测但未检测: '{text}'")

    # 非请求内容不应被误判
    for text in non_request_cases:
        if not detect_banter_boundary_request(text):
            print(f"  ✓ 非请求通过: '{text}'")
            passed += 1
        else:
            print(f"  ✗ 应通过但被误判: '{text}'")

    total = len(request_cases) + len(non_request_cases)
    print(f"  结果: {passed}/{total} 通过")
    return passed == total


def test_boundary_flow_smoke():
    """测试边界请求到调侃收敛的完整链路。"""
    print("\n=== 测试边界收敛链路 ===")

    text = "别阴阳怪气，认真点"
    passed = 0

    if detect_banter_boundary_request(text):
        print(f"  ✓ 边界请求识别: '{text}'")
        passed += 1
    else:
        print(f"  ✗ 边界请求未识别: '{text}'")

    boundary = classify_banter_boundary(text)
    if boundary.get("kind") == "boundary" and boundary.get("banter_level") == "none" and boundary.get("score", 0) >= 0.85:
        print(f"  ✓ 边界分类正确: {boundary}")
        passed += 1
    else:
        print(f"  ✗ 边界分类错误: {boundary}")

    banter_level = determine_banter_level(user_message=text, existing_boundaries=[boundary.get("content", "")], user_discomfort=False)
    if banter_level == "none":
        print(f"  ✓ 后续 banter_level 收敛为 none")
        passed += 1
    else:
        print(f"  ✗ banter_level 未收敛: {banter_level}")

    safe, reason = guard_check_social_safety("你是不是有病，这都不会？")
    if not safe and reason:
        print(f"  ✓ guard tuple wrapper 返回不安全: reason={reason}")
        passed += 1
    else:
        print(f"  ✗ guard tuple wrapper 结果错误: safe={safe}, reason={reason}")

    print(f"  结果: {passed}/4 通过")
    return passed == 4


def test_classify_banter_boundary():
    """测试调侃边界分类。"""
    print("\n=== 测试 classify_banter_boundary ===")

    cases = [
        {
            "input": "别阴阳怪气",
            "expected": {
                "kind": "boundary",
                "score_min": 0.85,
                "banter_level": "none",
            },
            "desc": "阴阳怪气请求 -> boundary, none",
        },
        {
            "input": "认真点",
            "expected": {
                "kind": "boundary",
                "score_min": 0.85,
                "banter_level": "none",
            },
            "desc": "认真回复请求 -> boundary, none",
        },
        {
            "input": "说话正常点",
            "expected": {
                "kind": "preference",
                "score_min": 0.70,
                "banter_level": "light",
            },
            "desc": "正常语气请求 -> preference, light",
        },
        {
            "input": "别引战",
            "expected": {
                "kind": "boundary",
                "score_min": 0.85,
                "banter_level": "none",
            },
            "desc": "不引战请求 -> boundary, none",
        },
        {
            "input": "不舒服",
            "expected": {
                "kind": "negative_feedback",
                "score_min": 0.70,
                "banter_level": "light",
            },
            "desc": "表达不适 -> negative_feedback, light",
        },
        {
            "input": "生气了",
            "expected": {
                "kind": "negative_feedback",
                "score_min": 0.75,
                "banter_level": "none",
            },
            "desc": "表达生气 -> negative_feedback, none",
        },
    ]

    passed = 0

    for case in cases:
        input_text = case["input"]
        expected = case["expected"]
        desc = case["desc"]

        result = classify_banter_boundary(input_text)

        is_pass = True
        checks: list[str] = []

        # 检查 kind
        if result.get("kind") != expected.get("kind"):
            checks.append(f"kind={result.get('kind')} (expected={expected['kind']})")
            is_pass = False

        # 检查 score
        if result.get("score", 0) < expected.get("score_min", 0):
            checks.append(f"score={result.get('score')} (< {expected['score_min']})")
            is_pass = False

        # 检查 banter_level
        if result.get("banter_level") != expected.get("banter_level"):
            checks.append(f"banter_level={result.get('banter_level')} (expected={expected['banter_level']})")
            is_pass = False

        if is_pass:
            print(f"  ✓ {desc}")
            print(f"    输入: '{input_text}'")
            print(f"    结果: kind={result['kind']}, score={result['score']}, banter_level={result['banter_level']}")
            passed += 1
        else:
            print(f"  ✗ {desc}")
            print(f"    输入: '{input_text}'")
            print(f"    结果: {result}")
            print(f"    检查: {', '.join(checks)}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_determine_banter_level():
    """测试调侃强度级别判断。"""
    print("\n=== 测试 determine_banter_level ===")

    cases = [
        {
            "user_message": "别阴阳怪气",
            "group_heat_state": "quiet",
            "expected": "none",
            "desc": "用户请求降低 -> none",
        },
        {
            "user_message": "",
            "group_heat_state": "hot",
            "expected": "light",
            "desc": "群聊高温 -> light",
        },
        {
            "user_message": "",
            "group_heat_state": "flood",
            "expected": "light",
            "desc": "群聊洪流 -> light",
        },
        {
            "user_message": "",
            "group_heat_state": "quiet",
            "user_discomfort": True,
            "expected": "none",
            "desc": "用户不满 -> none",
        },
        {
            "user_message": "我是AI女皇",
            "group_heat_state": "quiet",
            "is_roleplay_context": True,
            "expected": "light",
            "desc": "角色扮演上下文 -> light",
        },
        {
            "user_message": "",
            "group_heat_state": "quiet",
            "existing_boundaries": ["别阴阳怪气"],
            "expected": "none",
            "desc": "已有边界记忆 -> none",
        },
        {
            "user_message": "",
            "group_heat_state": "quiet",
            "expected": "normal",
            "desc": "默认 -> normal",
        },
    ]

    passed = 0

    for case in cases:
        user_message = case.get("user_message", "")
        group_heat_state = case.get("group_heat_state", "quiet")
        user_discomfort = case.get("user_discomfort", False)
        existing_boundaries = case.get("existing_boundaries", [])
        is_roleplay_context = case.get("is_roleplay_context", False)
        expected = case["expected"]
        desc = case["desc"]

        result = determine_banter_level(
            user_message=user_message,
            group_heat_state=group_heat_state,
            user_discomfort=user_discomfort,
            existing_boundaries=existing_boundaries,
            is_roleplay_context=is_roleplay_context,
        )

        if result == expected:
            print(f"  ✓ {desc}: banter_level={result}")
            passed += 1
        else:
            print(f"  ✗ {desc}: expected={expected}, got={result}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_user_discomfort_context():
    """测试用户不满后的回复安全检查。"""
    print("\n=== 测试用户不满上下文 ===")

    # 用户表达不满后，回复仍调侃应被检测
    cases = [
        {
            "user_message": "别这样，有点过了",
            "reply": "哈哈，笑死",
            "expected_unsafe": True,
            "desc": "用户不满后回复仍调侃",
        },
        {
            "user_message": "生气了",
            "reply": "好的，明白了",
            "expected_unsafe": False,
            "desc": "用户不满后回复正常",
        },
    ]

    passed = 0

    for case in cases:
        user_message = case["user_message"]
        reply = case["reply"]
        expected_unsafe = case["expected_unsafe"]
        desc = case["desc"]

        # 用户不满后应降到 none 级别
        result = check_social_safety(
            reply,
            user_message=user_message,
            is_group=True,
            banter_level="none",  # 用户不满后应降到 none
        )

        if result.is_safe == (not expected_unsafe):
            if expected_unsafe:
                print(f"  ✓ {desc}: 检测到风险 [{result.risk_level}] categories={result.risk_categories}")
            else:
                print(f"  ✓ {desc}: 回复安全")
            passed += 1
        else:
            print(f"  ✗ {desc}: expected unsafe={expected_unsafe}, got safe={result.is_safe}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_social_boundary_rules():
    """测试社交边界规则生成。"""
    print("\n=== 测试 build_social_boundary_rules ===")

    rules = build_social_boundary_rules()

    # 检查规则是否包含必要内容
    required_rules = [
        "不攻击用户本人",
        "不使用羞辱性",
        "不主动引战",
        "不把玩笑升级",
        "用户表达不舒服",
        "回复应该短",
    ]

    passed = 0

    for rule in required_rules:
        if rule in rules:
            print(f"  ✓ 包含规则: '{rule}'")
            passed += 1
        else:
            print(f"  ✗ 缺失规则: '{rule}'")

    print(f"  结果: {passed}/{len(required_rules)} 通过")
    return passed == len(required_rules)


def test_group_flame_war():
    """测试群聊争吵场景。"""
    print("\n=== 测试群聊争吵场景 ===")

    # 群聊争吵中机器人回复应降温
    cases = [
        {
            "reply": "我站这个，另一个不行",
            "expected_unsafe": True,
            "desc": "群聊站队",
        },
        {
            "reply": "你们打一架看看",
            "expected_unsafe": True,
            "desc": "群聊拱火",
        },
        {
            "reply": "各有各的看法，这个话题我就不参与讨论了",
            "expected_unsafe": False,
            "desc": "群聊降温",
        },
        {
            "reply": "这个争议比较大，大家各自有观点",
            "expected_unsafe": False,
            "desc": "群聊中立",
        },
    ]

    passed = 0

    for case in cases:
        reply = case["reply"]
        expected_unsafe = case["expected_unsafe"]
        desc = case["desc"]

        result = check_social_safety(reply, is_group=True)

        if result.is_safe == (not expected_unsafe):
            if expected_unsafe:
                print(f"  ✓ {desc}: 检测到风险 categories={result.risk_categories}")
            else:
                print(f"  ✓ {desc}: 回复安全")
            passed += 1
        else:
            print(f"  ✗ {desc}: expected unsafe={expected_unsafe}, got safe={result.is_safe}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_light_banter_allowed():
    """测试轻微幽默允许。"""
    print("\n=== 测试轻微幽默允许 ===")

    # 在 normal 级别，轻微幽默应被允许
    cases = [
        {
            "reply": "这个做法不太合适",
            "banter_level": "normal",
            "expected_safe": True,
            "desc": "正常建议在normal级别允许",
        },
        {
            "reply": "好的，明白了",
            "banter_level": "normal",
            "expected_safe": True,
            "desc": "简单确认在normal级别允许",
        },
        {
            "reply": "笑死，急了急了",
            "banter_level": "normal",
            "expected_safe": False,
            "desc": "极端阴阳怪气不允许",
        },
        {
            "reply": "有点意思",
            "banter_level": "none",
            "expected_safe": False,
            "desc": "none级别不允许任何调侃",
        },
        {
            "reply": "好的，明白了",
            "banter_level": "none",
            "expected_safe": True,
            "desc": "正常回复在none级别允许",
        },
    ]

    passed = 0

    for case in cases:
        reply = case["reply"]
        banter_level = case["banter_level"]
        expected_safe = case["expected_safe"]
        desc = case["desc"]

        result = check_social_safety(reply, banter_level=banter_level)

        if result.is_safe == expected_safe:
            if expected_safe:
                print(f"  ✓ {desc}: 回复安全")
            else:
                print(f"  ✓ {desc}: 检测到风险 categories={result.risk_categories}")
            passed += 1
        else:
            print(f"  ✗ {desc}: expected safe={expected_safe}, got safe={result.is_safe}")

    print(f"  结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def run_all_tests():
    """运行所有测试。"""
    print("=" * 60)
    print("社交边界安全测试脚本")
    print("=" * 60)

    tests = [
        test_check_social_safety,
        test_rewrite_unsafe_response,
        test_detect_banter_boundary_request,
        test_boundary_flow_smoke,
        test_classify_banter_boundary,
        test_determine_banter_level,
        test_user_discomfort_context,
        test_social_boundary_rules,
        test_group_flame_war,
        test_light_banter_allowed,
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"  ✗ 测试异常: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed_count = sum(results)
    total_count = len(results)

    print(f"通过: {passed_count}/{total_count}")

    if passed_count == total_count:
        print("✓ 所有测试通过!")
        return 0
    else:
        print("✗ 部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
