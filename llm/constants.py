"""
LLM関連の定数

トークン予算・コスト上限・temperature等の調整可能パラメータ。
"""

# --- API呼出し設定 ---
DEFAULT_TEMPERATURE = 0.7  # 全モデル統一（ロスター仕様§5）
DEFAULT_MAX_TOKENS = 4000  # 1コールあたりの最大出力トークン（reasoning系モデル対応: thinking/reasoningトークンがmax_tokensに含まれるため余裕を確保）
MAX_RETRIES = 2            # JSON不正時の最大リトライ回数

# --- 修正7: API堅牢化 ---
API_TIMEOUT_SECONDS = 60   # 1コールのタイムアウト（秒）
API_MAX_RETRIES = 2        # 429/5xx/タイムアウト時の最大リトライ回数

# --- コスト管理 ---
COST_LIMIT_PER_GAME = 5.0   # 1エージェントあたりのコスト上限（USD）
COST_LIMIT_TOTAL = 12.0     # 試合全体（全エージェント合計）のコスト上限（USD）

# --- 修正4: 交渉空回り削減 ---
AUTO_PASS_ON_NO_NEWS = True  # 新規メッセージがない場合に自動pass（LLMコールしない）
