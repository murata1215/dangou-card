#!/usr/bin/env python3
"""
キャラ立ち絵42枚の白背景を透明化するスクリプト。

処理方針（「白を消す」ではなく「背景を消す」）:
1. 各画素の輝度と彩度を計算
2. 輝度が高く彩度が低い画素を「背景候補」とする
3. 連結成分ラベリングで、画像の縁に接している成分のみを「背景」と判定
   → 内側の白（目の白目・ハイライト等）は残す
4. 背景画素のアルファを0に
5. アルファにガウスぼかしをかけてジャギー軽減
6. 不透明領域のバウンディングボックスでトリミング

入力: viewer/static/emotions/*.png（読み取りのみ）
出力: viewer/static/emotions_alpha/*.png + _contact_sheet.png
"""

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

# ---------------------------------------------------------------------------
# 調整可能パラメータ
# ---------------------------------------------------------------------------

LUMA_THRESH = 108      # 背景候補の輝度下限（0-255）
SAT_THRESH = 45        # 背景候補の彩度上限（0-255）
BLUR_SIGMA = 1.2       # アルファぼかし半径（px）
TRIM_PADDING = 4       # トリミング時の余白（px）

# ---------------------------------------------------------------------------
# ベンダー・感情の定義
# ---------------------------------------------------------------------------

VENDORS = ["anthropic", "deepseek", "google", "moonshot", "openai", "xai"]
EMOTIONS = ["anger", "doubt", "ease", "joy", "panic", "sadness", "smirk"]

INPUT_DIR = Path("viewer/static/emotions")
OUTPUT_DIR = Path("viewer/static/emotions_alpha")
CONTACT_SHEET_BG = (0x0d, 0x0d, 0x10)  # #0d0d10


def transparentize(img: Image.Image) -> Image.Image:
    """白背景を透明化する（縁接触連結成分方式）。"""
    # RGBA 変換
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img, dtype=np.float32)
    r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]

    # 輝度と彩度
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    rgb_stack = np.stack([r, g, b], axis=-1)
    sat = rgb_stack.max(axis=-1) - rgb_stack.min(axis=-1)

    # 背景候補マスク
    bg_candidate = (luma > LUMA_THRESH) & (sat < SAT_THRESH)

    # 連結成分ラベリング
    labeled, num_features = ndimage.label(bg_candidate)

    # 縁に接している成分を特定
    h, w = labeled.shape
    edge_labels = set()
    edge_labels.update(labeled[0, :].tolist())      # 上辺
    edge_labels.update(labeled[h - 1, :].tolist())  # 下辺
    edge_labels.update(labeled[:, 0].tolist())       # 左辺
    edge_labels.update(labeled[:, w - 1].tolist())   # 右辺
    edge_labels.discard(0)  # 0 は非候補

    # 背景マスク（縁接触成分のみ）
    bg_mask = np.isin(labeled, list(edge_labels))

    # アルファを設定
    new_alpha = np.where(bg_mask, 0.0, a)

    # ガウスぼかしでジャギー軽減
    new_alpha = ndimage.gaussian_filter(new_alpha, sigma=BLUR_SIGMA)
    new_alpha = np.clip(new_alpha, 0, 255)

    # 結果画像
    result = arr.copy()
    result[:, :, 3] = new_alpha
    result_img = Image.fromarray(result.astype(np.uint8), "RGBA")

    # バウンディングボックスでトリミング
    bbox = result_img.getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        x0 = max(0, x0 - TRIM_PADDING)
        y0 = max(0, y0 - TRIM_PADDING)
        x1 = min(result_img.width, x1 + TRIM_PADDING)
        y1 = min(result_img.height, y1 + TRIM_PADDING)
        result_img = result_img.crop((x0, y0, x1, y1))

    return result_img


def create_contact_sheet(
    output_dir: Path,
    cell_size: int = 480,
    label_height: int = 30,
) -> Path:
    """6×7 のコンタクトシートを作成する。"""
    cols = len(EMOTIONS)
    rows = len(VENDORS)
    sheet_w = cols * cell_size
    sheet_h = rows * (cell_size + label_height) + label_height  # +header
    sheet = Image.new("RGBA", (sheet_w, sheet_h), CONTACT_SHEET_BG + (255,))
    draw = ImageDraw.Draw(sheet)

    # ヘッダ行（感情名）
    for j, emo in enumerate(EMOTIONS):
        x = j * cell_size + cell_size // 2
        draw.text((x, 4), emo, fill=(255, 255, 255, 255), anchor="mt")

    for i, vendor in enumerate(VENDORS):
        for j, emo in enumerate(EMOTIONS):
            fname = f"{vendor}_{emo}.png"
            fpath = output_dir / fname
            if not fpath.exists():
                continue

            thumb = Image.open(fpath)
            # セルサイズに収まるよう縮小
            thumb.thumbnail((cell_size - 8, cell_size - 8 - label_height))

            x_offset = j * cell_size + (cell_size - thumb.width) // 2
            y_offset = label_height + i * (cell_size + label_height) + (cell_size - thumb.height) // 2
            sheet.paste(thumb, (x_offset, y_offset), thumb)

            # ラベル
            lx = j * cell_size + cell_size // 2
            ly = label_height + i * (cell_size + label_height) + cell_size
            draw.text((lx, ly), f"{vendor}", fill=(200, 200, 200, 255), anchor="mt")

    out_path = output_dir / "_contact_sheet.png"
    sheet.save(out_path, "PNG")
    return out_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for vendor in VENDORS:
        for emo in EMOTIONS:
            fname = f"{vendor}_{emo}.png"
            src = INPUT_DIR / fname
            if not src.exists():
                print(f"  SKIP (not found): {fname}")
                continue

            img = Image.open(src)
            result = transparentize(img)
            dst = OUTPUT_DIR / fname
            result.save(dst, "PNG")

            # 透明比率
            arr = np.array(result)
            total_px = arr.shape[0] * arr.shape[1]
            transparent_px = np.sum(arr[:, :, 3] == 0)
            alpha_ratio = transparent_px / total_px

            results.append({
                "file": fname,
                "size": f"{result.width}x{result.height}",
                "alpha_ratio": alpha_ratio,
            })
            print(f"  OK: {fname} → {result.width}x{result.height} (transparent: {alpha_ratio:.1%})")

    print(f"\n変換完了: {len(results)}/{len(VENDORS) * len(EMOTIONS)} 枚")
    print(f"出力先: {OUTPUT_DIR}")

    # 極端な値を報告
    print("\n=== 極端な透明比率 ===")
    for r in results:
        if r["alpha_ratio"] > 0.9:
            print(f"  ⚠ 消えすぎ (>{90}%): {r['file']} = {r['alpha_ratio']:.1%}")
        elif r["alpha_ratio"] < 0.2:
            print(f"  ⚠ 残りすぎ (<20%): {r['file']} = {r['alpha_ratio']:.1%}")

    anomalies = [r for r in results if r["alpha_ratio"] > 0.9 or r["alpha_ratio"] < 0.2]
    if not anomalies:
        print("  なし（全画像が 20%〜90% の範囲内）")

    # コンタクトシート
    print("\nコンタクトシート作成中...")
    cs_path = create_contact_sheet(OUTPUT_DIR)
    print(f"コンタクトシート: {cs_path}")

    # .devrelay-output にコピー
    devrelay = Path(".devrelay-output")
    devrelay.mkdir(exist_ok=True)
    import shutil
    shutil.copy(cs_path, devrelay / "_contact_sheet.png")
    print(f"コピー: {devrelay / '_contact_sheet.png'}")


if __name__ == "__main__":
    main()
