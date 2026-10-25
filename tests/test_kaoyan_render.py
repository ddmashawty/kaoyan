from kaoyan.render import check_attribution, lookup_template, render_placeholders

SYSU = {
    "alias": "F1",
    "fact_id": "score_lines:sysu-670-085404:college:2026:-:abc123",
    "value_text": "379",
    "year": 2026,
    "label": "学院复试线",
    "school": "sysu",
    "school_name": "中山大学",
    "code": "085404",
    "program_id": "sysu-670-085404",
    "source": {"title": "中大计院复试方案", "url": "https://cse.sysu.edu.cn/article/3475", "publish_date": "2026-03-18"},
}
SCUT = {
    "alias": "F2",
    "fact_id": "score_lines:scut-cs-085404:school_baseline:2026:-:def456",
    "value_text": "305",
    "year": 2026,
    "label": "学校基本线",
    "school": "scut",
    "school_name": "华南理工大学",
    "code": "085404",
    "program_id": "scut-cs-085404",
    "source": {"title": "华工基本线", "url": "https://yz.scut.edu.cn/x"},
}
FACT_MAP = {"F1": SYSU, "F2": SCUT}


def test_render_replaces_alias_and_appends_source() -> None:
    text, info = render_placeholders("中大 085404 学院复试线 [[F1]]。", FACT_MAP)
    assert "379" in text and "[[F1]]" not in text
    assert "中大计院复试方案" in text and "https://cse.sysu.edu.cn/article/3475" in text
    assert info["missing"] == [] and info["misattributed"] == []


def test_missing_alias_is_insufficient() -> None:
    text, info = render_placeholders("复试线 [[F9]]。", FACT_MAP)
    assert "依据不足" in text
    assert info["missing"] == ["F9"]


def test_cross_school_placeholder_is_misattributed() -> None:
    assert check_attribution("华工 085404 复试线 [[F1]]。", FACT_MAP) == ["F1"]
    text, info = render_placeholders("华工 085404 复试线 [[F1]]。", FACT_MAP)
    assert info["misattributed"] == ["F1"]
    assert "379" in text


def test_table_row_attribution() -> None:
    table = (
        "| 学校 | 复试线 |\n"
        "| --- | --- |\n"
        "| 中大 | [[F1]] |\n"
        "| 华工 | [[F1]] |\n"
    )
    bad = check_attribution(table, FACT_MAP)
    assert bad == ["F1"]
    text, info = render_placeholders("| 中大 | [[F1]] |\n", FACT_MAP)
    assert "379" in text and info["misattributed"] == []


def test_lookup_template_skips_compare() -> None:
    assert lookup_template({"kinds": ["compare"], "schools": ["sysu"], "codes": ["085404"]}, {"F1": SYSU}) is None
    answer = lookup_template({"kinds": ["score_line"], "schools": ["sysu"], "codes": ["085404"]}, {"F1": SYSU})
    assert answer is not None and "379" in answer and "学院复试线" in answer
    both = lookup_template(
        {"kinds": ["score_line"], "schools": ["sysu"], "codes": ["085404"]},
        FACT_MAP,
    )
    assert both is None
