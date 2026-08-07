"""题库管理模块 - 负责题目加载、筛选和获取"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.json")


def load_questions():
    """加载全部题目（含测试用例，仅供服务端判题使用）"""
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["questions"]


def load_questions_public():
    """加载全部题目（不含测试用例，供前端展示）"""
    questions = load_questions()
    return [_strip_secrets(q) for q in questions]


def get_question_by_id(question_id):
    """根据ID获取单道题目（含测试用例，仅供服务端判题使用）"""
    questions = load_questions()
    for q in questions:
        if q["id"] == question_id:
            return q
    return None


def get_question_by_id_public(question_id):
    """根据ID获取单道题目（不含测试用例，供前端展示）"""
    q = get_question_by_id(question_id)
    return _strip_secrets(q) if q else None


def _strip_secrets(q):
    """公开视图: 仅保留可见示例用例, 隐藏用例只下发计数（防止泄露答案）"""
    cases = q.get("test_cases", [])
    visible_cases = [c for c in cases if c.get("visible", True)]
    clean = {k: v for k, v in q.items() if k != "test_cases"}
    clean["test_cases"] = visible_cases
    clean["total_cases"] = len(cases)
    clean["hidden_cases"] = len(cases) - len(visible_cases)
    return clean


def get_questions_by_difficulty(difficulty=None):
    """按难度筛选题目"""
    questions = load_questions()
    if difficulty is None or difficulty == "all":
        return questions
    return [q for q in questions if q["difficulty"] == difficulty]


def get_categories():
    """获取所有题目分类"""
    questions = load_questions()
    return list(set(q["category"] for q in questions))


def get_difficulty_label(difficulty):
    """将难度标识转为中文标签"""
    labels = {"easy": "简单", "medium": "中等", "hard": "困难"}
    return labels.get(difficulty, difficulty)
