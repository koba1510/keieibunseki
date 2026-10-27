# FC本部 業務管理WEBアプリ β版

複数店舗・工場・本部・その他施設の売上、人件費、備品費、FCロイヤリティを月次で集約する、管理者向けDjangoアプリです。β版の収支は会計上の利益ではなく「暫定収支」です。

## ローカル起動

必要環境: Docker Engine / Docker Compose

```bash
cp .env.example .env
# .env の SECRET_KEY、POSTGRES_PASSWORD、ADMIN_PASSWORD を変更
docker compose up -d --build
docker compose exec web python manage.py create_initial_admin
```

Composeの`web`はDBへ `host.docker.internal`（公開ポート5432）経由で接続します。通常のサービス名`db`接続が使える環境では、`compose.yaml`の`POSTGRES_HOST`を`db`に変更しても構いません。

<http://localhost:8000/login/> を開き、`ADMIN_EMAIL` / `ADMIN_PASSWORD` でログインします。

停止:

```bash
docker compose down
```

DBボリュームも削除する場合だけ `docker compose down -v` を使ってください。

## Dockerを使わない開発

PostgreSQL用の環境変数を設定しなければSQLiteを使用できます。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py create_initial_admin --email admin@example.com --password '12文字以上の安全な値'
.venv/bin/python manage.py runserver
```

## CSV取込

`CSV取込` 画面で以下を選択し、プレビュー後に確定します。

- 売上: 1行目の支払月、2行目ヘッダー、A/B/C列
- FCロイヤリティ: 2行目の支払月、3行目ヘッダー、B/K/L列
- 給与: 1行目の和暦年月、2行目の `氏名（コード）`、項目ラベル行
- 備品: 1〜3行目から各月の「合計」列を検出。必要なら `2026-08,2026-09` のように月を指定

UTF-8（BOM含む）とCP932、最大10MBに対応します。プレビューは集計データを更新しません。未照合項目がある場合は、施設・CSV別名または従業員・所属履歴を登録してから確定してください。確定時は対象月だけをトランザクション置換します。

## 初期マスタ取込

`data/` 配下の3CSVを拠点→施設→従業員の順で取り込みます。施設IDは `FAC0001` 形式、拠点IDは `GRP001` 形式をそのまま使います。運営区分が空欄の施設は `NA`（対象外）で登録します。

```bash
docker compose exec web python manage.py import_initial_masters \
  --groups /app/data/01_拠点マスタ_初期登録.csv \
  --facilities /app/data/02_店舗施設マスタ_初期登録.csv \
  --employees /app/data/03_従業員マスタ_初期登録.csv
```

## 初期従業員移行

事前に施設を登録し、不変の施設IDを確認します。入力CSVの必須列:

```text
employee_code,name,facility_id,effective_month
00003,匿名 太郎,xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx,2026-08
```

```bash
docker compose exec web python manage.py import_initial_employees /app/data/employees.csv
```

全行の施設IDと年月を先に検証し、1件でも不正なら移行しません。

## バックアップと復元

バックアップ先はDBボリュームと異なる `./backups` です。90日を超えた同形式のバックアップは作成時に削除します。

```bash
docker compose exec web python manage.py backup_db --actor admin@example.com
docker compose exec web python manage.py restore_db /backups/keieibunseki-YYYYMMDD-HHMMSS.dump \
  --confirm RESTORE --actor admin@example.com
```

復元は現在のDBを置換します。必ず直前バックアップを取得し、利用者がいない状態で実行してください。

## テスト

```bash
.venv/bin/python manage.py test
```

PostgreSQLで実行する場合は `.env` の値をエクスポートして、テスト用DBを作成できるユーザーを利用してください。

## 未決事項・仮仕様

- 全店舗版売上CSV、修正済み備品CSV、実施設ID一覧は未受領のため、実ファイル突合が必要です。
- FC／直営の初期値は自動推測せず施設マスタで設定します。
- 給与の人件費は「支給額合計＋その他合計」。会社負担相当は「社会保険料合計＋雇用保険料×2.3」（円未満四捨五入）です。
- 弥生会計経費、賞与、取込履歴UI、通知、クラウド公開はβ版対象外です。
- 施設の所属拠点変更は全期間に現在値を適用します。履歴管理はしません。
- 税込ロイヤリティを使用します。売上・将来の会計経費との税込／税抜整合は未決定です。
- 括弧付き金額を負数として扱うかは業務未確定のため、現状は負数解釈する実装です。方針確定後に変更します。
- 直営／FCの `ownership` に履歴はなく、変更は過去月の集計にも遡及します。
- 売上取込済み月でCSVに無い店舗は0円、FCロイヤリティ取込済み月でCSVに無いFC店は未確定のまま残します。
