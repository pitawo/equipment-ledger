from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from datetime import datetime, date
import json
import os
import atexit
import secrets
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
# 本番では SECRET_KEY を環境変数で渡す（未設定なら起動のたびに使い捨てを生成する）
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
# 非同期方式は threading を使う。eventlet / gevent は Python の版によって
# gunicorn から読めなくなることがあり、追加依存の割に得るものが少ない。
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'ログインしてください。'
login_manager.login_message_category = 'error'

USERS_DATA_FILE = os.path.join(os.path.dirname(__file__), 'users_data.json')

def load_users():
    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get('users', [])
        except:
            return []
    return []

def save_users(users_list):
    try:
        with open(USERS_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({'users': users_list}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] ユーザー情報の保存失敗: {e}")

class User(UserMixin):
    def __init__(self, id, name, email, password_hash):
        self.id = str(id)
        self.name = name
        self.email = email
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    users = load_users()
    for u in users:
        if str(u['id']) == str(user_id):
            return User(u['id'], u['name'], u['email'], u['password_hash'])
    return None

# --- メール送信用関数 ---
def send_notification_email(to_email, subject, body):
    # SMTPサーバーの設定 (環境変数から取得、未設定時はスキップ)
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')

    if not all([smtp_server, smtp_user, smtp_password]):
        print(f"[MAIL SKIP] SMTP設定が不十分なためメール送信をスキップします。To: {to_email}")
        return

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = smtp_user
        msg['To'] = to_email

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        print(f"[MAIL SUCCESS] {to_email} へ通知メールを送信しました。")
    except Exception as e:
        print(f"[MAIL ERROR] メール送信失敗: {e}")

# --- 日付ユーティリティ ---
def parse_date(date_str):
    """'1/15' や '2026/1/15' 形式の日付をdateオブジェクトに変換"""
    if not date_str:
        return None
    try:
        parts = date_str.strip().split('/')
        if len(parts) == 2:
            # '1/15' 形式 → 今年か来年
            month, day = int(parts[0]), int(parts[1])
            year = date.today().year
            result = date(year, month, day)
            # 過去の日付なら来年と判断
            if result < date.today():
                result = date(year + 1, month, day)
            return result
        elif len(parts) == 3:
            # '2026/1/15' 形式
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            return date(year, month, day)
    except:
        pass
    return None

def format_date_for_display(d):
    """dateオブジェクトを '2026/1/15' 形式に変換"""
    if isinstance(d, str):
        d = parse_date(d)
    if d:
        return f"{d.year}/{d.month}/{d.day}"
    return ""

def format_date_for_storage(d):
    """dateオブジェクトを '2026/1/15' 形式に変換（保存用）"""
    if d:
        return f"{d.year}/{d.month}/{d.day}"
    return ""

def dates_overlap(start1, end1, start2, end2):
    """2つの期間が重複しているかチェック"""
    return start1 <= end2 and end1 >= start2

# データファイルのパス
DATA_FILE = os.path.join(os.path.dirname(__file__), 'equipment_data.json')

# --- データの保存・読み込み ---
def save_data():
    """データをJSONファイルに保存"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] データを保存しました")
    except Exception as e:
        print(f"[ERROR] データ保存失敗: {e}")

def migrate_equipment_data(cam):
    """古い形式の備品データを新形式に変換"""
    if "reservations" not in cam:
        cam["reservations"] = []
        # 既存の貸出情報を予約リストに移行
        if cam.get("status") == "貸出中" and cam.get("user"):
            # 古い period 形式 "1/15 ～ 1/20" をパース
            period = cam.get("period", "")
            start_str, end_str = "", ""
            if "～" in period:
                parts = period.split("～")
                start_str = parts[0].strip()
                end_str = parts[1].strip() if len(parts) > 1 else ""

            start_date = parse_date(start_str)
            end_date = parse_date(end_str)

            cam["reservations"].append({
                "user": cam.get("user", ""),
                "start_date": format_date_for_storage(start_date) if start_date else "",
                "end_date": format_date_for_storage(end_date) if end_date else "",
                "purpose": cam.get("purpose", "")
            })
        # 古いフィールドを削除
        cam.pop("user", None)
        cam.pop("period", None)
        cam.pop("purpose", None)
        cam.pop("status", None)  # statusは動的に計算するため削除

    # 予約データのマイグレーション（period形式 → start_date/end_date形式）
    for res in cam.get("reservations", []):
        if "period" in res and "start_date" not in res:
            period = res.get("period", "")
            start_str, end_str = "", ""
            if "～" in period:
                parts = period.split("～")
                start_str = parts[0].strip()
                end_str = parts[1].strip() if len(parts) > 1 else ""
            start_date = parse_date(start_str)
            end_date = parse_date(end_str)
            res["start_date"] = format_date_for_storage(start_date) if start_date else ""
            res["end_date"] = format_date_for_storage(end_date) if end_date else ""
            res.pop("period", None)
        res.pop("is_current", None)  # 不要になったフラグを削除

    return cam

def ensure_data_file():
    """データファイルが無ければサンプルから作る（初回起動・クローン直後）。"""
    if os.path.exists(DATA_FILE):
        return
    sample = os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "equipment_data_sample.json")
    if os.path.exists(sample):
        with open(sample, "r", encoding="utf-8") as f:
            content = f.read()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        print("[INIT] サンプルから %s を作成しました" % DATA_FILE)

def load_data():
    """JSONファイルからデータを読み込み"""
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # データ形式をマイグレーション
            for cam in data["equipments"]:
                migrate_equipment_data(cam)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] データを読み込みました（{len(data['equipments'])}件）")
            return
        except Exception as e:
            print(f"[ERROR] データ読み込み失敗: {e}")

    # ファイルがない場合は初期データを使用
    data = {
        "equipments": [
            {"id": 1, "name": "備品1", "reservations": []},
            {"id": 2, "name": "備品2", "reservations": []},
            {"id": 3, "name": "備品3", "reservations": []},
        ]
    }
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 初期データを使用します")

# --- 擬似データベース ---
data = {}
ensure_data_file()
load_data()

# アプリ終了時にデータ保存
atexit.register(save_data)


def get_equipment_status(cam):
    """備品のステータスを動的に計算"""
    today = date.today()
    for res in cam.get("reservations", []):
        start = parse_date(res.get("start_date", ""))
        end = parse_date(res.get("end_date", ""))
        if start and end and start <= today <= end:
            return "貸出中", res  # 現在貸出中の予約情報も返す
    return "空き", None

def has_pending_reservations(cam):
    """今日以降に残っている予約（利用中・将来の予約）があるか。

    削除の可否はこちらで判定する。get_equipment_status() は「今日が期間内か」
    しか見ないため、来週の予約が入っている備品を空きと判定してしまう。
    """
    today = date.today()
    for res in cam.get("reservations", []):
        end = parse_date(res.get("end_date", ""))
        if end and end >= today:
            return True
    return False

def enrich_reservation(res):
    """予約データに表示用情報を追加"""
    today = date.today()
    start = parse_date(res.get("start_date", ""))
    end = parse_date(res.get("end_date", ""))

    # 表示用の期間文字列
    start_display = format_date_for_display(start) if start else ""
    end_display = format_date_for_display(end) if end else ""
    res["period_display"] = f"{start_display} ～ {end_display}"

    # キャンセル用に元の日付文字列を保持
    res["start_date"] = res.get("start_date", "")
    res["end_date"] = res.get("end_date", "")

    # ステータス判定
    if start and end:
        if today > end:
            res["status"] = "ended"  # 終了済み
        elif start <= today <= end:
            res["status"] = "current"  # 現在利用中
        else:
            res["status"] = "future"  # 将来の予約
    else:
        res["status"] = "unknown"

    return res

def get_all_data():
    """全データをJSON形式で取得（ステータスを動的計算）"""
    today = date.today()
    equipments_with_status = []

    for cam in data["equipments"]:
        status, current_res = get_equipment_status(cam)

        # 予約を開始日でソートし、終了済みを除外
        valid_reservations = []
        for res in cam.get("reservations", []):
            enriched = enrich_reservation(res.copy())
            if enriched["status"] != "ended":  # 終了済みは表示しない
                valid_reservations.append(enriched)

        # 開始日でソート
        valid_reservations.sort(key=lambda r: parse_date(r.get("start_date", "")) or date.max)

        cam_data = {
            "id": cam["id"],
            "name": cam["name"],
            "status": status,
            "reservations": valid_reservations
        }
        equipments_with_status.append(cam_data)

    available_count = len([c for c in equipments_with_status if c["status"] == "空き"])
    total_count = len(equipments_with_status)
    busy_count = total_count - available_count

    return {
        "cameras": equipments_with_status,
        "available": available_count,
        "total": total_count,
        "busy": busy_count
    }

@app.route('/')
@login_required
def index():
    all_data = get_all_data()
    error = request.args.get('error', '')
    return render_template('index.html', equipments=all_data["cameras"], available=all_data["available"], total=all_data["total"], busy=all_data["busy"], error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        users = load_users()
        user_data = next((u for u in users if u['email'] == email), None)
        
        if user_data and check_password_hash(user_data['password_hash'], password):
            user = User(user_data['id'], user_data['name'], user_data['email'], user_data['password_hash'])
            login_user(user)
            return redirect(url_for('index'))
        else:
            error = "メールアドレスまたはパスワードが正しくありません"
            
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if not name or not email or not password:
            error = "すべての項目を入力してください"
        else:
            users = load_users()
            if any(u['email'] == email for u in users):
                error = "このメールアドレスは既に登録されています"
            else:
                new_id = str(max([int(u['id']) for u in users], default=0) + 1)
                new_user = {
                    'id': new_id,
                    'name': name,
                    'email': email,
                    'password_hash': generate_password_hash(password)
                }
                users.append(new_user)
                save_users(users)
                flash('アカウントを作成しました。ログインしてください。', 'success')
                return redirect(url_for('login'))
                
    return render_template('register.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/api/data')
@login_required
def api_data():
    """APIエンドポイント: 全データ取得"""
    return jsonify(get_all_data())

@app.route('/reserve', methods=['POST'])
@login_required
def reserve():
    cam_id = int(request.form.get('cam_id'))
    # Use current_user.name instead of form input
    user_name = current_user.name
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')
    purpose = request.form.get('purpose', '')

    # 日付をパース
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    today = date.today()

    # バリデーション
    error = None
    if not start_date or not end_date:
        error = "日付の形式が正しくありません（例: 1/15）"
    elif start_date < today:
        error = "開始日は今日以降の日付を指定してください"
    elif end_date < start_date:
        error = "終了日は開始日以降の日付を指定してください"
    else:
        # 重複チェック
        for cam in data["equipments"]:
            if cam["id"] == cam_id:
                for res in cam.get("reservations", []):
                    res_start = parse_date(res.get("start_date", ""))
                    res_end = parse_date(res.get("end_date", ""))
                    if res_start and res_end and dates_overlap(start_date, end_date, res_start, res_end):
                        error = f"この期間は既に予約があります（{format_date_for_display(res_start)} ～ {format_date_for_display(res_end)}）"
                        break
                break

    if error:
        # エラーがある場合はJSONで返す（フロントでハンドリング）
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": error}), 400
        # 通常のフォーム送信の場合はクエリパラメータでエラーを渡す
        return redirect(url_for('index', error=error))

    # 予約を追加
    for cam in data["equipments"]:
        if cam["id"] == cam_id:
            reservation = {
                "user": user_name,
                "user_email": current_user.email, # Save email for notifications
                "start_date": format_date_for_storage(start_date),
                "end_date": format_date_for_storage(end_date),
                "purpose": purpose
            }
            cam["reservations"].append(reservation)
            cam_name = cam["name"]
            break

    save_data()
    
    # 予約完了メール送信
    subject = f"【備品台帳】「{cam_name}」の予約が完了しました"
    body = f"""{current_user.name} 様

備品の予約を受け付けました。

■ 備品名: {cam_name}
■ 利用期間: {format_date_for_display(start_date)} ～ {format_date_for_display(end_date)}
■ 利用目的: {purpose or '未入力'}

このメールは送信専用です。
"""
    send_notification_email(current_user.email, subject, body)

    socketio.emit('data_updated', get_all_data())
    return redirect(url_for('index'))

@app.route('/return/<int:cam_id>')
@login_required
def return_cam(cam_id):
    today = date.today()
    cam_name = ""
    res_user_email = ""
    res_start = ""
    res_end = ""
    
    for cam in data["equipments"]:
        if cam["id"] == cam_id:
            cam_name = cam["name"]
            # 現在利用中の予約（今日が期間内）を削除
            new_reservations = []
            for res in cam.get("reservations", []):
                start = parse_date(res.get("start_date", ""))
                end = parse_date(res.get("end_date", ""))
                # 現在利用中でないものだけ残す
                if not (start and end and start <= today <= end):
                    new_reservations.append(res)
                else:
                    # 削除対象（返却）のメールアドレスと情報を保持
                    res_user_email = res.get("user_email")
                    res_start = format_date_for_display(start)
                    res_end = format_date_for_display(end)
            cam["reservations"] = new_reservations
            break

    save_data()
    
    # 返却完了メール送信
    if res_user_email:
        subject = f"【備品台帳】「{cam_name}」の返却が完了しました"
        body = f"""備品「{cam_name}」の返却処理が完了いたしました。
ご利用ありがとうございました。

■ 備品名: {cam_name}
■ 予約期間: {res_start} ～ {res_end}

このメールは送信専用です。
"""
        send_notification_email(res_user_email, subject, body)

    socketio.emit('data_updated', get_all_data())
    return redirect(url_for('index'))

@app.route('/cancel/<int:cam_id>')
@login_required
def cancel_reservation(cam_id):
    """予約をキャンセル"""
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')

    cam_name = ""
    res_user_email = ""

    for cam in data["equipments"]:
        if cam["id"] == cam_id:
            cam_name = cam["name"]
            # 指定された期間の予約を削除
            new_reservations = []
            for res in cam.get("reservations", []):
                if res.get("start_date") == start_date_str and res.get("end_date") == end_date_str:
                    res_user_email = res.get("user_email")
                    continue  # この予約をスキップ（削除）
                new_reservations.append(res)
            cam["reservations"] = new_reservations
            break

    save_data()
    
    # キャンセル完了メール送信
    if res_user_email:
        subject = f"【備品台帳】「{cam_name}」の予約をキャンセルしました"
        body = f"""備品「{cam_name}」の以下の予約をキャンセルいたしました。

■ 備品名: {cam_name}
■ キャンセルした期間: {start_date_str} ～ {end_date_str}

このメールは送信専用です。
"""
        send_notification_email(res_user_email, subject, body)

    socketio.emit('data_updated', get_all_data())
    return redirect(url_for('index'))

@app.route('/settings', methods=['POST'])
@login_required
def update_master():
    action = request.form.get('action')

    if action == 'add':
        # 備品追加
        new_name = request.form.get('new_name', '').strip()
        if new_name:
            new_id = max([c["id"] for c in data["equipments"]], default=0) + 1
            data["equipments"].append({"id": new_id, "name": new_name, "reservations": []})

    elif action == 'delete':
        # 備品削除
        delete_id = request.form.get('delete_id', '')
        if delete_id:
            cam_id = int(delete_id)
            # 予約が残っているものは削除しない（利用中だけでなく将来の予約も含む）
            new_equipments = []
            blocked = None
            for c in data["equipments"]:
                if c["id"] != cam_id:
                    new_equipments.append(c)
                elif has_pending_reservations(c):
                    new_equipments.append(c)
                    blocked = c
            if blocked:
                flash('「%s」には予約が残っているため削除できません。先に返却するか予約を取り消してください。'
                      % blocked["name"], 'error')
            data["equipments"] = new_equipments

    elif action == 'rename':
        # 備品名変更
        rename_id = request.form.get('rename_id', '')
        rename_name = request.form.get('rename_name', '').strip()
        if rename_id and rename_name:
            cam_id = int(rename_id)
            for cam in data["equipments"]:
                if cam["id"] == cam_id:
                    cam["name"] = rename_name
                    break

    save_data()
    socketio.emit('data_updated', get_all_data())
    return redirect(url_for('index'))

# 手動保存エンドポイント（管理用）
@app.route('/api/save')
@login_required
def manual_save():
    save_data()
    return {"status": "ok", "message": "データを保存しました"}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    socketio.run(app, host='0.0.0.0', port=port, debug=debug, use_reloader=False)
