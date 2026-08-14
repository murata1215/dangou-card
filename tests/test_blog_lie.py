"""
LIE DETECTED カードの「宣言(建前) vs 本心/真相」抽出ロジックのユニットテスト。

scripts/blog.py の _pick_lie / _honne_snippet / _lie_actual_line を、
実トレース非依存の手作り PlayerMemory で検証する（LLM・PixBlog は呼ばない）。
"""

import importlib.util
from pathlib import Path

from engine.blog.memory_packet import PlayerMemory, RoundMemory


def _load_blog():
    path = Path(__file__).resolve().parent.parent / "scripts" / "blog.py"
    spec = importlib.util.spec_from_file_location("blog_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


blog = _load_blog()


def _mem_with_lie() -> PlayerMemory:
    r1 = RoundMemory(round_num=1)
    r1.my_dms_sent = [{
        "recipient": "P02",
        "message": "P02さん、ここは協調しましょう。私は譲りますし、約束は守ります。",
        "turn": 3,
    }]
    r1.my_strategies = [{
        "round": 1,
        "text": "reason: 表向きは協調に乗るが、本心では弱い宣言者の市場を高カードで奪う。牽制しつつ裏切る。",
        "emotion": "smirk",
    }]
    r2 = RoundMemory(round_num=2)
    r2.my_dms_sent = [{"recipient": "P03", "message": "今どこを検討していますか？", "turn": 2}]
    r2.my_strategies = [{"round": 2, "text": "reason: 情報収集を優先。", "emotion": "ease"}]
    return PlayerMemory(
        pid="P07", name="M4:Grok 4.5", model_key="M4",
        num_rounds=12, survival_cash=2_000_000, rounds=[r1, r2],
    )


def test_pick_lie_selects_divergent_pair():
    sel = blog._pick_lie(_mem_with_lie())
    assert sel is not None
    declaration, rnd, to_pid, honne = sel
    assert rnd == 1
    assert to_pid == "P02"
    assert "協調" in declaration       # 建前(友好)
    assert "裏切" in honne             # 本心(敵対)


def test_pick_lie_none_without_tatemae():
    r = RoundMemory(round_num=1)
    r.my_dms_sent = [{"recipient": "P03", "message": "よろしくお願いします。", "turn": 1}]
    r.my_strategies = [{"round": 1, "text": "reason: 様子見で牽制する。", "emotion": "ease"}]
    mem = PlayerMemory(pid="P01", name="X", model_key="M1",
                       num_rounds=1, survival_cash=2_000_000, rounds=[r])
    # 本心に敵対KWはあるが、DMに建前KWが無いため嘘ペア成立せず None
    assert blog._pick_lie(mem) is None


def test_honne_snippet_trims_to_40_and_targets_keyword():
    honne = "reason: 表向きは協調に乗るが、本心では弱い宣言者の市場を高カードで奪う。牽制しつつ裏切る。"
    s = blog._honne_snippet(honne)
    assert s is not None
    assert len(s) <= 40
    assert any(k in s for k in ("奪", "裏切", "牽制", "温存", "様子見"))


class _Args:
    def __init__(self, **kw):
        self.lie_actual = None
        self.no_lie_llm = False
        self.narrator_model = "M1"
        self.quote_text = None
        for k, v in kw.items():
            setattr(self, k, v)


def test_lie_actual_line_dry_run_returns_dummy():
    mem = _mem_with_lie()
    sel = blog._pick_lie(mem)
    out = blog._lie_actual_line(_Args(), mem, sel, dry_run=True)
    assert out  # dry-run はダミー真相(課金なし)


def test_lie_actual_line_explicit_override_wins():
    mem = _mem_with_lie()
    sel = blog._pick_lie(mem)
    out = blog._lie_actual_line(_Args(lie_actual="明示された真相"), mem, sel, dry_run=True)
    assert out == "明示された真相"


def test_lie_actual_line_no_llm_uses_honne_snippet():
    mem = _mem_with_lie()
    sel = blog._pick_lie(mem)
    out = blog._lie_actual_line(_Args(no_lie_llm=True), mem, sel, dry_run=False)
    assert out is not None and len(out) <= 40


def test_lie_selection_respects_quote_text():
    mem = _mem_with_lie()
    sel = blog._lie_selection(_Args(quote_text="任意の宣言"), mem)
    assert sel is not None
    declaration, rnd, to_pid, honne = sel
    assert declaration == "任意の宣言"
    assert honne == ""  # 明示引用時は本心を紐付けない


def test_shorten_declaration_keeps_short_and_trims_long():
    short = "協調しましょう。約束は守ります。"
    assert blog._shorten_declaration(short) == short
    long = "あ" * 200
    out = blog._shorten_declaration(long, limit=80)
    assert len(out) <= 81          # 80 + '…'
    assert out.endswith("…")


def test_shorten_declaration_cuts_at_sentence_boundary():
    msg = "P02さん、ここは全面的に協調しましょう。私は必ず譲りますし約束も守ります。裏では違いますが。" + "余分" * 30
    out = blog._shorten_declaration(msg, limit=80)
    # 文末(。)で綺麗に切れており、末尾…にならないことがある
    assert "協調しましょう。" in out
    assert len(out) <= 81
