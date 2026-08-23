"""
viewer/static/index.html の契約フォーマッタ（fmtObligation/fmtOutcome）の
frontend単体テスト。

JSテスト基盤（package.json/node_modules）は本プロジェクトに存在しないため、
`// ==== CONTRACT_FORMATTER_START/END ====` センチネルで囲まれた実コードを
index.htmlから直接抽出し、node で実行して検証する。node が使えない環境では
skip する（CI耐性）。

抽出範囲: CARD_INFO / cardRankKey / cardInfo / pidLabel / yen / safeStr /
fmtObligation / fmtOutcome。pidLabel が参照する situationData と
splitModelName はスタブで与える（実装と同じフォールバック挙動：
seat_mapが無ければpidをそのまま返す）。escapeHtml は
`div.textContent = text || ''; return div.innerHTML;` と同じ結果になる
最小実装をNode上でスタブする（DOM非依存の文字置換）。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "viewer" / "static" / "index.html"
START_MARK = "// ==== CONTRACT_FORMATTER_START ===="
END_MARK = "// ==== CONTRACT_FORMATTER_END ===="
DELIVERY_START_MARK = "// ==== DELIVERY_BADGE_START ===="
DELIVERY_END_MARK = "// ==== DELIVERY_BADGE_END ===="

_NODE_CANDIDATES = ["node", "/home/uso8m/.devrelay/node/bin/node"]


def _find_node() -> str | None:
    for c in _NODE_CANDIDATES:
        found = shutil.which(c) if "/" not in c else (c if Path(c).exists() else None)
        if found:
            return found
    return None


NODE = _find_node()


def _extract_block(start_mark: str, end_mark: str) -> str:
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    start = html.index(start_mark)
    end = html.index(end_mark)
    assert start < end, f"{start_mark} センチネルの順序が不正"
    return html[start:end]


def _extract_formatter_block() -> str:
    return _extract_block(START_MARK, END_MARK)


def _extract_delivery_badge_block() -> str:
    return _extract_block(DELIVERY_START_MARK, DELIVERY_END_MARK)


# escapeHtml のスタブ。
# 実装: `const div = document.createElement('div'); div.textContent = text || ''; return div.innerHTML;`
# textノードのHTMLフラグメントシリアライズは & / < / > をエスケープする
# （属性値ではないので " ' は対象外）。falsy値は '' になる点も再現する。
_ESCAPE_HTML_STUB = """
function escapeHtml(text) {
  const t = text || '';
  const s = (typeof t === 'string') ? t : String(t);
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function splitModelName(label) {
  return { name: (typeof label === 'string') ? label : '' };
}
let situationData = null;
"""


def _run_js(assertions: str, src: str | None = None) -> subprocess.CompletedProcess:
    """抽出したソース + スタブ + assertions を node で実行する。

    assertions は `assert(cond, msg)` ヘルパを使ったJSコード文字列。
    失敗時はプロセスがnon-zeroで終了し、stderrに理由が入る。
    src を省略すると従来どおり契約フォーマッタブロックを使う（既存呼び出し互換）。
    """
    js_src = src if src is not None else _extract_formatter_block()
    script = (
        _ESCAPE_HTML_STUB
        + "\n"
        + js_src
        + "\n"
        + """
function assert(cond, msg) {
  if (!cond) {
    console.error('ASSERTION FAILED: ' + msg);
    process.exit(1);
  }
}
"""
        + assertions
        + "\nconsole.log('OK');\n"
    )
    assert NODE is not None
    return subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30,
    )


@pytest.fixture(autouse=True)
def _require_node():
    if NODE is None:
        pytest.skip("node not found in PATH or /home/uso8m/.devrelay/node/bin/node")


class TestFmtObligationKnownTypes:
    """4種のob_typeすべてが期待どおりの日本語文言を含むこと"""

    def test_type_a_payment(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P01', counterparty:'P05', ob_type:'type_a_payment',
                                    round_num:7, details:{amount:300000}});
          assert(s.includes('P01'), 'obligor missing');
          assert(s.includes('P05'), 'counterparty missing');
          assert(s.includes('支払う'), 'payment verb missing');
          assert(s.includes('R7履行'), 'round missing');
          assert(s.includes('¥300,000'), 'amount missing: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_type_b_market(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P03', ob_type:'type_b_market',
                                    round_num:4, details:{market_id:'M01'}});
          assert(s.includes('M01'), 'market missing');
          assert(s.includes('参加する'), 'verb missing: ' + s);
          assert(!s.includes('参加してはいけない'), 'should not be negated: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_type_b_no_market_means_must_not_participate(self):
        """type_b_no_marketは『不参加義務』。ユーザー提案の誤訳
        （型Bを出してはいけない）ではなく、市場への不参加を明示すること"""
        r = _run_js("""
          const s = fmtObligation({obligor:'P09', counterparty:'P03', ob_type:'type_b_no_market',
                                    round_num:6, details:{market_id:'M02'}});
          assert(s.includes('P09'), 'obligor missing');
          assert(s.includes('M02'), 'market missing');
          assert(s.includes('参加してはいけない'), 'must-not-participate wording missing: ' + s);
          assert(s.includes('R6履行'), 'round missing');
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_type_b_card(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P02', ob_type:'type_b_card',
                                    round_num:3, details:{card_rank:'ONE_PAIR'}});
          assert(s.includes('ONE PAIR'), 'card label missing: ' + s);
          assert(s.includes('使用する'), 'verb missing');
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestFmtObligationCardFallback:
    """details.card / card_rank / rank の3経路すべてでカード名が出ること
    （実データの77%がdetails.cardだった既存バグの回帰防止）"""

    def test_accepts_card_key(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P02', ob_type:'type_b_card', details:{card:'ONE_PAIR_2'}});
          assert(s.includes('ONE PAIR'), 'details.card path broken: ' + s);
          assert(!s.includes('>?<') , 'must not fallback to ?: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_accepts_card_rank_key(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P02', ob_type:'type_b_card', details:{card_rank:'FLUSH'}});
          assert(s.includes('FLUSH'), 'details.card_rank path broken: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_accepts_rank_key(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P02', ob_type:'type_b_card', details:{rank:'STRAIGHT'}});
          assert(s.includes('STRAIGHT'), 'details.rank path broken: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_card_rank_priority_over_card(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P02', ob_type:'type_b_card',
                                    details:{card_rank:'FLUSH', card:'ONE_PAIR_2'}});
          assert(s.includes('FLUSH'), 'card_rank should win: ' + s);
          assert(!s.includes('ONE PAIR'), 'card should not leak when card_rank present: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestFmtObligationUnknownAndMissing:
    def test_unknown_ob_type_no_guess(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P01', ob_type:'type_b_secret_handshake', details:{}});
          assert(s.includes('未対応の契約条項'), 'fallback text missing: ' + s);
          assert(s.includes('type_b_secret_handshake'), 'type name missing: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_missing_details_no_throw(self):
        r = _run_js("""
          const a = fmtObligation({obligor:'P01', ob_type:'type_b_market'});
          const b = fmtObligation({obligor:'P01', ob_type:'type_b_market', details: null});
          const c = fmtObligation({obligor:'P01', ob_type:'type_b_market', details: 'garbage'});
          assert(a.includes('?'), 'missing details should fallback to ?: ' + a);
          assert(b.includes('?'), 'null details should fallback to ?: ' + b);
          assert(c.includes('?'), 'string details should fallback to ?: ' + c);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_null_and_non_object_term_no_throw(self):
        r = _run_js("""
          const a = fmtObligation(null);
          const b = fmtObligation(undefined);
          const c = fmtObligation('garbage');
          const d = fmtObligation(42);
          assert(typeof a === 'string' && a.length > 0, 'null term should not throw');
          assert(typeof b === 'string' && b.length > 0, 'undefined term should not throw');
          assert(typeof c === 'string' && c.length > 0, 'string term should not throw');
          assert(typeof d === 'string' && d.length > 0, 'number term should not throw');
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestFmtObligationRoundNum:
    def test_invalid_round_num_hidden(self):
        r = _run_js("""
          const cases = [null, '6', 0, -1, true, 6.5, undefined];
          for (const rv of cases) {
            const s = fmtObligation({obligor:'P01', ob_type:'type_b_market',
                                      round_num: rv, details:{market_id:'M01'}});
            assert(!s.includes('履行'),
                   'invalid round_num should not render R-badge: ' + JSON.stringify(rv) + ' -> ' + s);
          }
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_valid_round_num_shown(self):
        r = _run_js("""
          const s = fmtObligation({obligor:'P01', ob_type:'type_b_market',
                                    round_num: 6, details:{market_id:'M01'}});
          assert(s.includes('R6履行'), 'valid round_num should render: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestFmtObligationSecurityAndRobustness:
    def test_xss_in_obligor_and_market(self):
        r = _run_js("""
          const s = fmtObligation({
            obligor: '<img src=x onerror=alert(1)>',
            ob_type: 'type_b_market',
            details: { market_id: '</b><script>alert(2)</script>' },
          });
          assert(!s.includes('<img'), 'raw <img must be escaped: ' + s);
          assert(!s.includes('<script>'), 'raw <script> must be escaped: ' + s);
          assert(s.includes('&lt;'), 'escaped angle bracket expected: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_bool_and_object_values_not_stringified_literally(self):
        r = _run_js("""
          const s1 = fmtObligation({obligor:'P01', ob_type:'type_b_market', details:{market_id: true}});
          const s2 = fmtObligation({obligor:'P01', ob_type:'type_a_payment', details:{amount: {}}});
          assert(!s1.includes('>true<'), 'bool must not render as literal true: ' + s1);
          assert(!s2.includes('[object Object]'), 'object must not stringify as [object Object]: ' + s2);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_obligor_bool_does_not_render_as_true_string(self):
        r = _run_js("""
          const s = fmtObligation({obligor: true, ob_type: 'type_b_market', details:{market_id:'M01'}});
          assert(!s.includes('>true<'), 'bool obligor must not render literal true: ' + s);
          assert(s.includes('?'), 'bool obligor should fallback to ?: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestFmtOutcome:
    def test_each_known_event_type(self):
        r = _run_js("""
          const cases = [
            ['TYPE_A_EXECUTION', '履行完了'],
            ['TYPE_A_FAILURE', '履行失敗'],
            ['TYPE_B_VIOLATION', '違反'],
            ['AUTO_COMMIT', '自動履行'],
            ['AUTO_COMMIT_FAILURE', '自動履行失敗'],
          ];
          for (const [et, expect] of cases) {
            const s = fmtOutcome({event_type: et, round: 6, data: {player_id: 'P09'}});
            assert(s.includes(expect), et + ' missing text ' + expect + ': ' + s);
          }
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_violation_shows_who(self):
        r = _run_js("""
          const s = fmtOutcome({event_type:'TYPE_B_VIOLATION', round:6, data:{player_id:'P09'}});
          assert(s.includes('P09'), 'violator missing: ' + s);
          assert(s.includes('R6:'), 'round prefix missing: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_unknown_event_type_no_guess(self):
        r = _run_js("""
          const s = fmtOutcome({event_type:'SOME_FUTURE_EVENT', round:2, data:{}});
          assert(s.includes('未対応の結果'), 'fallback text missing: ' + s);
          assert(s.includes('SOME_FUTURE_EVENT'), 'event type name missing: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_null_and_non_object_outcome_no_throw(self):
        r = _run_js("""
          const a = fmtOutcome(null);
          const b = fmtOutcome('garbage');
          assert(typeof a === 'string' && a.length > 0);
          assert(typeof b === 'string' && b.length > 0);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_xss_in_outcome_player_id(self):
        r = _run_js("""
          const s = fmtOutcome({event_type:'TYPE_B_VIOLATION', round:1,
                                 data:{player_id:'<script>alert(1)</script>'}});
          assert(!s.includes('<script>'), 'raw script tag must be escaped: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr


class TestMultipleTermsRenderAsList:
    def test_three_terms_map_independently(self):
        r = _run_js("""
          const terms = [
            {obligor:'P01', ob_type:'type_a_payment', details:{amount:1000}},
            {obligor:'P02', ob_type:'type_b_market', details:{market_id:'M01'}},
            {obligor:'P03', ob_type:'unknown_type', details:{}},
          ];
          const rendered = terms.map(fmtObligation);
          assert(rendered.length === 3, 'expected 3 rendered items');
          assert(rendered[0].includes('支払う'));
          assert(rendered[1].includes('参加する'));
          assert(rendered[2].includes('未対応の契約条項'));
        """)
        assert r.returncode == 0, r.stdout + r.stderr


def _run_delivery_js(assertions: str) -> subprocess.CompletedProcess:
    """deliveryBadge/isIncomingDm/deliveryClass ブロック + assertions を node で実行する。"""
    return _run_js(assertions, src=_extract_delivery_badge_block())


class TestDeliveryBadgeDirection:
    """
    選手詳細モーダルでのDM送受信バッジ分離（✅ 送信済 / 📥 受信済）の回帰テスト。

    deliveryBadge(m, viewerPid) は viewerPid を渡したときだけ向きで表示を分ける。
    viewerPid省略（状況パネル等・特定選手基準でない画面）は現行どおり一律「送信済」。
    rejected/unverifiedは方向より優先し、broadcast/anonymous_broadcastは
    絶対に受信済にならない（type!=='dm'ガード）。
    """

    def test_outgoing_delivered_says_soushinzumi(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P03', to:'P04', delivery:'delivered'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('✅ 送信済'), 'outgoing should say 送信済: ' + s);
          assert(s.includes('delivery-delivered'), 'outgoing class missing: ' + s);
          assert(!s.includes('受信済'), 'outgoing must not say 受信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_incoming_delivered_says_jushinzumi(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P04', to:'P03', delivery:'delivered'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('📥 受信済'), 'incoming should say 受信済: ' + s);
          assert(s.includes('delivery-received'), 'incoming class missing: ' + s);
          assert(!s.includes('✅ 送信済'), 'incoming must not say 送信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_viewer_pid_omitted_stays_soushinzumi_both_ways(self):
        r = _run_delivery_js("""
          const outgoing = {type:'dm', sender:'P03', to:'P04', delivery:'delivered'};
          const incoming = {type:'dm', sender:'P04', to:'P03', delivery:'delivered'};
          const so = deliveryBadge(outgoing);
          const si = deliveryBadge(incoming);
          assert(so.includes('✅ 送信済'), 'omitted viewerPid (outgoing) should say 送信済: ' + so);
          assert(si.includes('✅ 送信済'), 'omitted viewerPid (incoming) should say 送信済: ' + si);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_incoming_rejected_keeps_rejected_wording(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P04', to:'P03', delivery:'rejected', reject_reason:'Target P03 is not alive'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('❌ 不成立'), 'rejected must win over direction: ' + s);
          assert(s.includes('Target P03 is not alive'), 'reject reason missing: ' + s);
          assert(!s.includes('受信済') && !s.includes('送信済'), 'direction wording must not leak: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_outgoing_rejected_keeps_rejected_wording(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P03', to:'P09', delivery:'rejected', reject_reason:'Target P09 is not alive'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('❌ 不成立'), 'rejected must win over direction: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_incoming_and_outgoing_unverified_both_say_mikensho(self):
        r = _run_delivery_js("""
          const incoming = {type:'dm', sender:'P04', to:'P03', delivery:'unverified'};
          const outgoing = {type:'dm', sender:'P03', to:'P04', delivery:'unverified'};
          const si = deliveryBadge(incoming, 'P03');
          const so = deliveryBadge(outgoing, 'P03');
          assert(si.includes('❔ 未検証'), 'incoming unverified must win over direction: ' + si);
          assert(so.includes('❔ 未検証'), 'outgoing unverified must win over direction: ' + so);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_broadcast_never_becomes_incoming(self):
        r = _run_delivery_js("""
          const m = {type:'broadcast', sender:'P04', delivery:'delivered'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('✅ 送信済'), 'broadcast should stay 送信済: ' + s);
          assert(!s.includes('受信済'), 'broadcast must never say 受信済: ' + s);
          assert(isIncomingDm(m, 'P03') === false, 'broadcast isIncomingDm must be false');
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_anonymous_broadcast_never_becomes_incoming(self):
        r = _run_delivery_js("""
          const m = {type:'anonymous_broadcast', actual_sender:'P04', delivery:'delivered'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('✅ 送信済'), 'anonymous_broadcast should stay 送信済: ' + s);
          assert(!s.includes('受信済'), 'anonymous_broadcast must never say 受信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_to_as_array_including_viewer_pid_is_incoming(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P04', to:['P03','P05'], delivery:'delivered'};
          assert(isIncomingDm(m, 'P03') === true, 'array to containing viewerPid should be incoming');
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('📥 受信済'), 'array to should say 受信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_missing_or_empty_to_falls_back_to_outgoing_wording(self):
        r = _run_delivery_js("""
          const cases = [
            {type:'dm', sender:'P04', to: '', delivery:'delivered'},
            {type:'dm', sender:'P04', to: null, delivery:'delivered'},
            {type:'dm', sender:'P04', delivery:'delivered'},
          ];
          for (const m of cases) {
            assert(isIncomingDm(m, 'P03') === false, 'missing/empty to must not be incoming: ' + JSON.stringify(m));
            const s = deliveryBadge(m, 'P03');
            assert(s.includes('✅ 送信済'), 'missing/empty to should fallback to 送信済: ' + s);
          }
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_missing_sender_does_not_become_incoming(self):
        r = _run_delivery_js("""
          const m = {type:'dm', to:'P03', delivery:'delivered'};
          assert(isIncomingDm(m, 'P03') === false, 'missing sender must not be incoming');
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('✅ 送信済'), 'missing sender should fallback to 送信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_self_addressed_dm_prefers_outgoing_wording(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P03', to:'P03', delivery:'delivered'};
          const s = deliveryBadge(m, 'P03');
          assert(s.includes('✅ 送信済'), 'self-addressed dm should prefer outgoing wording: ' + s);
          assert(!s.includes('受信済'), 'self-addressed dm must not say 受信済: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_no_delivery_field_renders_nothing(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P04', to:'P03'};
          const s = deliveryBadge(m, 'P03');
          assert(s === '', 'no delivery field (legacy log) should render nothing: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_reject_reason_xss_is_escaped(self):
        r = _run_delivery_js("""
          const m = {type:'dm', sender:'P04', to:'P03', delivery:'rejected',
                     reject_reason: '<script>alert(1)</script>'};
          const s = deliveryBadge(m, 'P03');
          assert(!s.includes('<script>'), 'raw script tag must be escaped: ' + s);
          assert(s.includes('&lt;script&gt;'), 'escaped angle bracket expected: ' + s);
        """)
        assert r.returncode == 0, r.stdout + r.stderr
