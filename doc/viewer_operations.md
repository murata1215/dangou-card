# Viewer運用マニュアル

談合カードの**公開Viewer**（`https://dangou-card-viewer.devrelay.io/`）の再起動・確認手順です。公開運用の正本はこの文書です。

## 現行構成

| 項目 | 現行値 |
|---|---|
| 常駐方式 | `uso8m` ユーザーのsystemd user service |
| service名 | `dangou-viewer.service` |
| unit | `~/.config/systemd/user/dangou-viewer.service` |
| working directory | `/home/uso8m/dangou-card` |
| ExecStart | `/home/uso8m/dangou-card/.venv/bin/python -c "from viewer.server import main; main()"` |
| Viewer bind | `127.0.0.1:9023` |
| 公開経路 | Caddy: `dangou-card-viewer.devrelay.io` → `localhost:9023` |

unitは`Restart=always`、`RestartSec=3`で動作する。通常は手動Python起動や`viewer.pid`の管理は不要である。

## 通常の再起動と確認

`uso8m` ユーザーでSSHログインして実行する。

```bash
# 1. 現在の状態
systemctl --user status dangou-viewer.service --no-pager

# 2. 再起動
systemctl --user restart dangou-viewer.service

# 3. 再起動後の状態
systemctl --user status dangou-viewer.service --no-pager

# 4. Viewer自身へのローカル疎通
curl -fsS http://127.0.0.1:9023/api/games
```

直近ログの確認:

```bash
journalctl --user -u dangou-viewer.service -n 100 --no-pager
```

ログの追尾:

```bash
journalctl --user -u dangou-viewer.service -f
```

別ユーザーから操作する必要がある場合は、対象ユーザーのuser systemd busへ接続できる正規のSSH／sudo運用を使う。root向けの`systemctl restart dangou-viewer.service`ではなく、`uso8m`の`systemctl --user`を使う。

## Caddyとの関係

通常のViewer再起動、Viewerコード更新、静的ファイル更新ではCaddy reloadは不要である。Caddyは`localhost:9023`へ転送するだけで、Viewer serviceの再起動後に同じupstreamへ接続する。

次の場合だけ、Caddy設定の確認・変更と、その変更に対する別途承認のうえでreloadを検討する。

- ドメイン、TLS、site設定を変更した。
- `reverse_proxy`のupstreamやポートを変更した。
- Caddy service自体が停止・異常となった。

その場合の候補コマンドは`sudo systemctl reload caddy`だが、Viewerの通常再起動では実行しない。

## DevRelay Agent経由の操作

Agent経由で再起動する場合も、対象環境でuser systemd busへアクセスできる権限がある場合に限る。安全な順序は次のとおり。

1. `systemctl --user status dangou-viewer.service --no-pager`
2. `systemctl --user restart dangou-viewer.service`
3. 再度statusを確認する。
4. `curl -fsS http://127.0.0.1:9023/api/games`でローカル疎通を確認する。

user busへ接続できない環境では、Agentが推測でnohup起動やkillを行わず、`uso8m`のSSH端末で上記を実行する。

## 神視点の有効化と確認

通常表示はDM本文・宛先、匿名発言の実発信者、契約条項を返さない。ゲーム内の秘匿情報を閲覧する神視点は、追加の`VIEWER_GOD_TOKEN`が設定されたViewerだけで利用できる。

tokenはリポジトリ、systemd unit本文、コマンド履歴、devlogに書かない。`uso8m`所有のリポジトリ外ファイルへ保管し、次の権限を守る。

```text
~/.config/dangou-viewer/                    # 0700
~/.config/dangou-viewer/god.env             # 0600, VIEWER_GOD_TOKENのみ
~/.config/systemd/user/dangou-viewer.service.d/god-token.conf
                                                # 0600, EnvironmentFileのパスだけ
```

drop-inは秘密値を含めず、次だけを設定する。

```ini
[Service]
EnvironmentFile=/home/uso8m/.config/dangou-viewer/god.env
```

envファイルを追加・変更したときだけ、通常の再起動前に反映する。

```bash
systemctl --user daemon-reload
systemctl --user restart dangou-viewer.service
```

`systemctl --user show -p Environment`やenvファイルの内容を端末・共有ログに出力してtokenを確認してはならない。Caddy設定は変わらないためreloadは不要である。

神視点のAPIは`view=god`と`X-Viewer-God-Token`ヘッダの両方を必要とする。tokenなし・誤tokenはHTTP 403、正しいtokenだけがHTTP 200となることを、本文を表示しない検証で確認する。UIでは詳細モーダルの「神視点を有効化」にtokenを一時入力し、`🔒 神視点 ON — 秘匿情報を表示中`、公開／秘匿のアイコン・ラベル・カード区別が出ることを確認する。tokenはURLやブラウザ保存領域に残さない。

## トラブルシューティング

| 症状 | 確認順序 |
|---|---|
| serviceが`inactive`／`failed` | `status`、`journalctl`で原因を確認してから`restart`。`viewer.pid`やnohupを使わない。 |
| 9023へのcurlが失敗 | service statusとjournalを確認し、`127.0.0.1:9023`のbind設定・Python起動例外を調べる。 |
| ローカルcurlは成功するが公開URLだけ失敗 | ViewerではなくCaddy経路を疑う。Caddy service、site設定、TLS／ドメインを別途確認する。 |
| 公開URLがフォールバック画面 | upstream `localhost:9023`への接続失敗の可能性がある。まずViewerのlocal curlを確認する。 |

## 旧方式との区別

`nohup uv run python -m viewer.server`、`viewer.pid`、`kill $(cat viewer.pid)`、および公開Viewerを`0.0.0.0:9025`で常駐させる手順は、systemd導入前の履歴上の方式またはローカル開発用である。現行公開Viewerの復旧・再起動には使用しない。

開発時にコード既定の`0.0.0.0:9025`を手動起動する方法は[`viewer/README.md`](../viewer/README.md)を参照する。ただし公開運用手順は本書を優先する。
