"""
Bot共通の調整可能定数

全Botの数値パラメータを1ファイルに集約。
シミュレーション結果を見て調整する。
"""

# --- 共通: 返済ポリシー ---
REPAY_RESERVE = 200_000  # 返済時に残す予備資金（Entry Fee以外の余裕分）

# --- Bot 1: Random ---
# 借入額: ランダム（min〜max）

# --- Bot 2: Conservative ---
CONSERVATIVE_LOAN = 1_200_000  # 最低借入

# --- Bot 3: Strong-Card Save ---
SCS_LOAN = 3_000_000
SCS_EARLY_ROUNDS = 8     # R1〜R8は弱カード消化
SCS_LATE_START = 9        # R9〜R12は強カード投入

# --- Bot 4: High-Prize Hunter ---
HPH_LOAN = 5_000_000
HPH_HIGH_PRIZE_RANK = 7  # 高賞金市場にはrank7+を使用
HPH_MID_PRIZE_RANK = 4   # 中賞金にはrank4+
HPH_LOW_PRIZE_RANK = 1   # 低賞金にはrank1

# --- Bot 5: Empty-Market Hunter ---
EMH_LOAN = 3_000_000

# --- Bot 6: Collusion ---
COLLUSION_LOAN = 3_000_000
COLLUSION_CARD = "HIGH_CARD"  # 談合で使うカード（最弱で山分け狙い）

# --- Bot 7: Betrayal ---
BETRAYAL_LOAN = 5_000_000
BETRAYAL_RANK_BOOST = 1  # 提案カードより何ランク上で裏切るか

# --- Bot 8: Honey-Pot ---
HONEYPOT_LOAN = 7_000_000
HONEYPOT_STRONG_INTERVAL = 2  # 何ラウンドに1回強カードを使うか
HONEYPOT_STRONG_RANK = 7      # 強カードの閾値ランク
