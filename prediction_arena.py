"""KMC Prediction Arena backend. Google Sheets is the source of truth for rewards."""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import jsonify, make_response, request
from gspread.exceptions import WorksheetNotFound
from sheets import get_spreadsheet, get_players_sheet

BET_SHEET = "Ставки🎯"
STATE_SHEET = "СтавкиСтан"
HEADERS = ["id", "Нік", "Гра", "Тип прогнозу", "Прогноз", "Правильна відповідь", "Збіги", "Сума МК", "Prediction ID", "Статус", "Подано о"]
STATE_HEADERS = ["Гра", "Тип прогнозу", "Статус", "Дедлайн ISO", "Правильна відповідь", "Відкрито о", "Закрито о", "Розраховано о", "Тривалість секунд"]
LABELS = {"winner": "Команда-переможець", "first_killed": "Перший убитий гравець", "first_exiled": "Перший вигнаний гравець", "best_move": "Найкращий хід"}
REWARDS = {"winner": 50, "first_killed": 50, "first_exiled": 50}
LOCK = threading.RLock()
_PLAYER_CACHE = {"at": 0.0, "data": {}}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int(v, default=None):
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _sheets():
    sh = get_spreadsheet()
    try:
        bets = sh.worksheet(BET_SHEET)
    except WorksheetNotFound:
        bets = sh.add_worksheet(title=BET_SHEET, rows=2000, cols=11)
    try:
        states = sh.worksheet(STATE_SHEET)
    except WorksheetNotFound:
        states = sh.add_worksheet(title=STATE_SHEET, rows=300, cols=9)
    old = bets.row_values(1)
    if not old or not old[0].strip():
        bets.update("A1:K1", [HEADERS])
    else:
        names = []
        for i, h in enumerate(HEADERS):
            current = old[i] if i < len(old) else ""
            names.append(current if i in (0, 1, 7) and current.strip() else h)
        current_header = (old + [""] * len(HEADERS))[:len(HEADERS)]
        if names != current_header:
            bets.update("A1:K1", [names])
    old_state = states.row_values(1)
    state_header = (old_state + [""] * len(STATE_HEADERS))[:len(STATE_HEADERS)]
    if state_header != STATE_HEADERS:
        states.update("A1:I1", [STATE_HEADERS])
    return bets, states


def _players():
    now = time.time()
    with LOCK:
        if now - _PLAYER_CACHE["at"] < 30:
            return dict(_PLAYER_CACHE["data"])
        result = {}
        for row in get_players_sheet().get_all_values()[1:]:
            if len(row) > 3 and row[3].strip() and len(row) > 1 and row[1].strip():
                result[str(row[3]).strip()] = row[1].strip()
        _PLAYER_CACHE["at"] = now
        _PLAYER_CACHE["data"] = result
        return dict(result)


def _is_admin(user_id):
    ids = {444726017}  # Preserve the existing admin ID used by MafMarketv2.
    for raw in os.getenv("ADMIN_TELEGRAM_ID", "").replace(";", ",").split(","):
        if raw.strip().isdigit():
            ids.add(int(raw.strip()))
    return user_id in ids


def _identity():
    raw = request.headers.get("X-Telegram-Init-Data", "")
    if not raw and request.is_json:
        raw = str((request.get_json(silent=True) or {}).get("initData", ""))
    token = os.getenv("BOT_TOKEN", "")
    if not token or not raw:
        raise PermissionError("Відкрий Mini App через кнопку в Telegram MafMarket.")
    data = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    received = data.pop("hash", "")
    check = "\n".join("%s=%s" % (k, v) for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        raise PermissionError("Не вдалося перевірити Telegram-сесію.")
    try:
        auth_date = int(data.get("auth_date", "0"))
        if auth_date < time.time() - 86400 or auth_date > time.time() + 60:
            raise PermissionError("Сесія застаріла або має некоректний час. Перезапусти Mini App.")
        user = json.loads(data.get("user", "{}"))
        uid = int(user["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise PermissionError("Не вдалося визначити Telegram-користувача.")
    nick = _players().get(str(uid))
    if not nick:
        raise PermissionError("Спочатку прив’яжи нік у MafMarket через /start.")
    return {"id": uid, "nick": nick, "is_admin": _is_admin(uid)}


def _state_rows(ws):
    return ws.get_all_values()[1:]


def _state_find(ws, game, kind):
    for row_no, row in enumerate(_state_rows(ws), start=2):
        if len(row) > 1 and _int(row[0]) == game and row[1] == kind:
            return row_no, row
    return None, None


def _write_state(ws, game, kind, status, deadline="", answer="", opened="", closed="", settled="", duration=60):
    row_no, _ = _state_find(ws, game, kind)
    values = [str(game), kind, status, deadline, answer, opened, closed, settled, str(duration)]
    if row_no:
        ws.update("A%d:I%d" % (row_no, row_no), [values])
    else:
        ws.append_row(values, value_input_option="RAW")


def _label(kind):
    return LABELS.get(kind, kind)


def _kind(kind):
    if kind not in LABELS:
        raise ValueError("Невідомий тип прогнозу.")
    return kind


def _answer(kind, value):
    if kind == "winner":
        aliases = {"мирні": "peaceful", "мирные": "peaceful", "peaceful": "peaceful", "мафія": "mafia", "мафия": "mafia", "mafia": "mafia"}
        ans = aliases.get(str(value or "").strip().lower())
        if not ans:
            raise ValueError("Обери «Мирні» або «Мафія».")
        return ans
    if kind in ("first_killed", "first_exiled"):
        ans = _int(value)
        if ans is None or ans < 1 or ans > 10:
            raise ValueError("Обери номер гравця від 1 до 10.")
        return ans
    if kind == "best_move":
        if not isinstance(value, list):
            raise ValueError("Для «Найкращого ходу» потрібно три номери.")
        ans = sorted([_int(x, -1) for x in value])
        if len(ans) != 3 or min(ans) < 1 or max(ans) > 10 or len(set(ans)) != 3:
            raise ValueError("Обери три різні номери від 1 до 10.")
        return ans
    raise ValueError("Невідомий тип прогнозу.")


def _decoded(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _score(kind, guess, correct):
    if kind == "best_move":
        matches = len(set(guess) & set(correct))
        return matches, {3: 100, 2: 60, 1: 20, 0: 0}[matches]
    return 0, REWARDS[kind] if guess == correct else 0


def _all_bets(ws):
    rows = ws.get_all_values()
    return [(i, row) for i, row in enumerate(rows[1:], start=2) if len(row) >= 4 and row[0].strip()]


def _close_expired(states):
    current = datetime.now(timezone.utc)
    for row_no, row in enumerate(_state_rows(states), start=2):
        if len(row) < 4 or row[2] != "open" or not row[3]:
            continue
        try:
            deadline = datetime.fromisoformat(row[3].replace("Z", "+00:00"))
        except ValueError:
            continue
        if deadline <= current:
            states.update_cell(row_no, 3, "locked")
            states.update_cell(row_no, 7, _now())


def _notify(text):
    chat_id = os.getenv("PREDICTIONS_CHAT_ID", "").strip()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not chat_id or not token:
        print("KMC predictions: PREDICTIONS_CHAT_ID is not configured; skipped group notification.")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status >= 300:
                print("KMC prediction notification returned HTTP", response.status)
    except Exception as exc:
        print("KMC prediction notification failed:", exc)


def _summary(user):
    bets, states = _sheets()
    _close_expired(states)
    state_data = []
    for row in _state_rows(states):
        if len(row) >= 3:
            state_data.append({
                "game": _int(row[0], 0), "type": row[1], "label": _label(row[1]), "status": row[2],
                "deadline": row[3] if len(row) > 3 else "",
                "correct_answer": _decoded(row[4]) if len(row) > 4 and row[4] else None,
            })
    mine, score, total = [], {}, 0
    for _, row in _all_bets(bets):
        uid = _int(row[0])
        nick = row[1]
        reward = _int(row[7] if len(row) > 7 else 0, 0) or 0
        score[nick] = score.get(nick, 0) + reward
        if uid == user["id"]:
            total += reward
            mine.append({
                "game": _int(row[2], 0), "type": row[3], "label": _label(row[3]),
                "answer": _decoded(row[4]) if len(row) > 4 else None,
                "correct_answer": _decoded(row[5]) if len(row) > 5 and row[5] else None,
                "matches": _int(row[6] if len(row) > 6 else 0, 0) or 0,
                "reward": reward, "status": row[9] if len(row) > 9 else "",
            })
    board = [{"nick": nick, "total": amount} for nick, amount in score.items()]
    board.sort(key=lambda v: (-v["total"], v["nick"].lower()))
    for pos, entry in enumerate(board, 1):
        entry["place"] = pos
    games = []
    for game in range(1, 11):
        current = [x for x in state_data if x["game"] == game]
        games.append({"game": game, "states": current})
    return {"user": user, "games": games, "states": state_data, "mine": mine, "total_mk": total, "leaderboard": board[:20]}


def register_prediction_routes(app):
    @app.get("/predictions")
    def prediction_page():
        response = make_response(_HTML)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/predictions/state", methods=["GET"])
    def prediction_state():
        try:
            user = _identity()
            with LOCK:
                return jsonify(_summary(user))
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401
        except Exception as exc:
            print("KMC prediction state error:", exc)
            return jsonify({"error": "Не вдалося завантажити прогнози. Спробуй ще раз."}), 503

    @app.post("/api/predictions/submit")
    def prediction_submit():
        try:
            user = _identity()
            data = request.get_json(silent=True) or {}
            game, kind = _int(data.get("game")), _kind(data.get("type"))
            if game is None or not 1 <= game <= 10:
                raise ValueError("Гра має бути від 1 до 10.")
            answer = _answer(kind, data.get("answer"))
            with LOCK:
                bets, states = _sheets()
                _close_expired(states)
                _, st = _state_find(states, game, kind)
                if not st or st[2] != "open":
                    return jsonify({"error": "Цей прогноз закрито."}), 409
                if len(st) > 3 and st[3] and datetime.fromisoformat(st[3].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    return jsonify({"error": "Час на прогноз завершився."}), 409
                key = "%s:%s:%s" % (game, kind, user["id"])
                if any(len(r) > 8 and r[8] == key for _, r in _all_bets(bets)):
                    return jsonify({"error": "Прогноз у цій категорії вже подано."}), 409
                bets.append_row([str(user["id"]), user["nick"], str(game), kind, json.dumps(answer, ensure_ascii=False), "", "0", "0", key, "submitted", _now()], value_input_option="RAW")
            return jsonify({"ok": True, "message": "Прогноз збережено!"})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print("KMC prediction submit error:", exc)
            return jsonify({"error": "Не вдалося зберегти прогноз."}), 503

    def admin_guard():
        try:
            user = _identity()
            if not user["is_admin"]:
                return None, (jsonify({"error": "Ця дія доступна лише організатору."}), 403)
            return user, None
        except PermissionError as exc:
            return None, (jsonify({"error": str(exc)}), 401)

    @app.post("/api/predictions/admin/open")
    def prediction_open():
        user, error = admin_guard()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        try:
            game, kind = _int(data.get("game")), _kind(data.get("type"))
            duration = _int(data.get("duration_seconds"), 60)
            if game is None or not 1 <= game <= 10:
                raise ValueError("Гра має бути від 1 до 10.")
            if duration is None or not 15 <= duration <= 3600:
                raise ValueError("Тривалість має бути від 15 до 3600 секунд.")
            with LOCK:
                _, states = _sheets()
                _, existing = _state_find(states, game, kind)
                if existing and existing[2] in ("open", "locked", "settled"):
                    raise ValueError("Цей прогноз уже відкривали. Повторно відкривати його не можна.")
                now = datetime.now(timezone.utc)
                deadline = datetime.fromtimestamp(now.timestamp() + duration, timezone.utc).isoformat(timespec="seconds")
                _write_state(states, game, kind, "open", deadline, "", now.isoformat(timespec="seconds"), "", "", duration)
            _notify("🎯 KMC Prediction Arena — гра %s/10\nВідкрито: %s\nЧас на відповідь: %s с." % (game, _label(kind), duration))
            return jsonify({"ok": True, "message": "Прогноз відкрито.", "deadline": deadline})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print("KMC prediction open error:", exc)
            return jsonify({"error": "Не вдалося відкрити прогноз."}), 503

    @app.post("/api/predictions/admin/close")
    def prediction_close():
        user, error = admin_guard()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        try:
            game, kind = _int(data.get("game")), _kind(data.get("type"))
            with LOCK:
                _, states = _sheets()
                row_no, row = _state_find(states, game, kind)
                if not row or row[2] != "open":
                    raise ValueError("Прогноз не відкритий.")
                states.update_cell(row_no, 3, "locked")
                states.update_cell(row_no, 7, _now())
            return jsonify({"ok": True, "message": "Прогноз закрито."})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print("KMC prediction close error:", exc)
            return jsonify({"error": "Не вдалося закрити прогноз."}), 503

    @app.post("/api/predictions/admin/settle")
    def prediction_settle():
        user, error = admin_guard()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        try:
            game, kind = _int(data.get("game")), _kind(data.get("type"))
            if game is None or not 1 <= game <= 10:
                raise ValueError("Некоректний номер гри.")
            answer = _answer(kind, data.get("correct_answer"))
            answer_json = json.dumps(answer, ensure_ascii=False, sort_keys=True)
            with LOCK:
                bets, states = _sheets()
                state_no, st = _state_find(states, game, kind)
                if not st:
                    raise ValueError("Прогноз не знайдений.")
                if st[2] == "settled":
                    return jsonify({"ok": True, "message": "Результат уже розрахований; дубль не створено."})
                if st[2] == "open":
                    states.update_cell(state_no, 3, "locked")
                    states.update_cell(state_no, 7, _now())
                updates, total, winners, count = [], 0, 0, 0
                for row_no, row in _all_bets(bets):
                    if _int(row[2]) != game or row[3] != kind:
                        continue
                    try:
                        guess = _answer(kind, _decoded(row[4]))
                    except ValueError:
                        continue
                    matches, reward = _score(kind, guess, answer)
                    total += reward
                    winners += int(reward > 0)
                    count += 1
                    updates.append({"range": "F%d:H%d" % (row_no, row_no), "values": [[answer_json, str(matches), str(reward)]]})
                    updates.append({"range": "J%d:J%d" % (row_no, row_no), "values": [["settled"]]})
                if updates:
                    bets.batch_update(updates, value_input_option="RAW")
                _, latest = _state_find(states, game, kind)
                states.update("A%d:I%d" % (state_no, state_no), [[str(game), kind, "settled", latest[3] if len(latest) > 3 else "", answer_json, latest[5] if len(latest) > 5 else "", latest[6] if len(latest) > 6 else _now(), _now(), latest[8] if len(latest) > 8 else "60"]])
            _notify("✅ KMC Prediction Arena — гра %s/10\n%s\nПрогнозів: %s\nНагороди: %s учасників, загалом %s МК." % (game, _label(kind), count, winners, total))
            return jsonify({"ok": True, "message": "Результат підтверджено.", "responses": count, "winners": winners, "total_awarded": total})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print("KMC prediction settlement error:", exc)
            return jsonify({"error": "Не вдалося розрахувати результати. Повторення має бути безпечним; перевір Google Sheets."}), 503


_HTML = r'''<!doctype html>
<html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#111317"><title>KMC Prediction Arena</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark;--bg:#101216;--panel:#191d23;--line:#303844;--text:#f6f7f9;--muted:#aab3c0;--accent:#d9b46b;--ok:#7bd8a1;--bad:#ff8d8d}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif;padding:env(safe-area-inset-top) 14px calc(20px + env(safe-area-inset-bottom))}main{max-width:760px;margin:auto}.hero{padding:22px 4px 14px}.eyebrow{font-size:11px;letter-spacing:.18em;color:var(--accent);font-weight:800}h1{font-size:25px;margin:7px 0}.muted{color:var(--muted)}.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 18px}.stat,.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}.stat strong{display:block;font-size:23px}.head{margin:20px 2px 10px;font-weight:800}.grid{display:grid;gap:10px}.card h3{margin:0 0 5px;font-size:16px}.tag{display:inline-block;border-radius:99px;padding:4px 9px;font-size:11px;background:#222832;color:var(--muted)}.open{color:var(--ok)}.locked{color:var(--accent)}.settled{color:#a8c5ff}.btn{border:0;border-radius:11px;padding:12px;background:var(--accent);color:#211a0e;font-weight:850;font-size:14px;cursor:pointer;width:100%;margin-top:10px}.btn.secondary{background:#222832;color:var(--text);border:1px solid var(--line)}.choices{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:12px}.choices button{background:#222832;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:11px 6px;font-weight:750}.choices .selected{border-color:var(--accent);background:#3a3121;color:#ffdfa0}.ten{grid-template-columns:repeat(5,1fr)}.form{width:100%;background:#222832;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:11px;margin:7px 0}.notice{padding:12px;border-radius:12px;background:#222832;color:var(--muted);margin:12px 0}.error{color:var(--bad)}.success{color:var(--ok)}.small{font-size:12px}.listrow{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}.listrow:last-child{border-bottom:0}.hidden{display:none!important}.footer{padding:24px 0;text-align:center;color:var(--muted);font-size:11px}
</style></head><body><main>
<header class="hero"><div class="eyebrow">KREMENCHUK MAFIA CLUB</div><h1>🏆 KMC Prediction Arena</h1><div id="welcome" class="muted">Прогнози на фінал сезону</div></header>
<div class="stats"><div class="stat"><span class="muted small">Зароблено у фіналі</span><strong id="total">— МК</strong></div><div class="stat"><span class="muted small">Місце в рейтингу</span><strong id="rank">—</strong></div></div>
<div id="message" class="notice hidden"></div><div id="loading" class="notice">Завантажую арену…</div>
<section id="participant" class="hidden"><div class="head">🎯 Доступні прогнози</div><div id="predictions" class="grid"></div><div class="head">📜 Мої результати</div><div id="mine" class="card muted">Поки що немає прогнозів.</div><div class="head">🥇 Рейтинг</div><div id="board" class="card"></div></section>
<section id="admin" class="hidden"><div class="head">🛠 Панель організатора</div><div class="card"><label>Гра<select id="agame" class="form"></select></label><label>Категорія<select id="atype" class="form"><option value="winner">Команда-переможець</option><option value="first_killed">Перший убитий гравець</option><option value="first_exiled">Перший вигнаний гравець</option><option value="best_move">Найкращий хід</option></select></label><label>Тривалість (сек.)<input id="duration" class="form" type="number" min="15" max="3600" value="60"></label><button class="btn" onclick="adminAction('open')">Відкрити прогноз</button><button class="btn secondary" onclick="adminAction('close')">Закрити прогноз</button><div class="head">Правильна відповідь</div><div id="editor"></div><button class="btn" onclick="adminAction('settle')">Підтвердити результат і нарахувати МК</button><div class="head">Стан прогнозів</div><div id="states"></div></div></section>
<div class="footer">MafMarket · За неправильні прогнози MafCoins не списуються.</div></main>
<script>
var tg=window.Telegram&&window.Telegram.WebApp;if(tg){tg.ready();tg.expand()}var initData=tg?tg.initData:"";var state=null;var best=new Set();var correct=new Set();var labels={winner:"Команда-переможець",first_killed:"Перший убитий гравець",first_exiled:"Перший вигнаний гравець",best_move:"Найкращий хід"};
function el(id){return document.getElementById(id)}function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function msg(s,cls){el("message").textContent=s;el("message").className="notice "+(cls||"");el("message").classList.remove("hidden")}
async function api(path,body){var opt={method:body?"POST":"GET",headers:{"X-Telegram-Init-Data":initData}};if(body){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body)}var r=await fetch(path,opt),d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.error||"Помилка запиту");return d}
function tag(s){var l={open:"Відкрито",locked:"Закрито",settled:"Розраховано"};return '<span class="tag '+esc(s)+'">'+esc(l[s]||s)+'</span>'}
function already(g,t){return state.mine.some(function(p){return p.game===g&&p.type===t})}
function choiceMarkup(type,g){var html="",i;if(type==="winner"){return '<div class="choices"><button onclick="submit('+g+',\'winner\',\'peaceful\')">🕊 Мирні</button><button onclick="submit('+g+',\'winner\',\'mafia\')">🔴 Мафія</button></div>'}if(type==="first_killed"||type==="first_exiled"){for(i=1;i<=10;i++)html+='<button onclick="submit('+g+',\''+type+'\','+i+')">'+i+'</button>';return '<div class="choices ten">'+html+'</div>'}for(i=1;i<=10;i++)html+='<button id="b'+g+'_'+i+'" onclick="toggleBest('+i+')">'+i+'</button>';return '<div class="muted small">Обери три різні номери. Порядок не має значення.</div><div class="choices ten">'+html+'</div><button class="btn" onclick="submitBest('+g+')">Підтвердити номери</button>'}
function render(){el("welcome").textContent="Привіт, "+state.user.nick+"! Фінал сезону · 10 ігор";el("total").textContent=state.total_mk+" МК";var me=state.leaderboard.find(function(x){return x.nick===state.user.nick});el("rank").textContent=me?"#"+me.place:"—";el("participant").classList.remove("hidden");var html="";state.states.forEach(function(s){if(s.status==="open"){var g=s.game,t=s.type;html+='<div class="card"><div class="row"><h3>'+esc(s.label)+'</h3>'+tag(s.status)+'</div><div class="muted small">Гра '+g+'/10 · '+(t==="best_move"?"100 / 60 / 20 МК":"50 МК")+' · дедлайн '+esc(s.deadline)+'</div>';html+=already(g,t)?'<div class="notice success">Твій прогноз уже прийнято.</div>':choiceMarkup(t,g);html+='</div>'}});el("predictions").innerHTML=html||'<div class="card muted">Наразі немає відкритих прогнозів. Дочекайся сповіщення організатора.</div>';html="";state.mine.slice().reverse().forEach(function(p){html+='<div class="listrow"><div><b>Гра '+p.game+' · '+esc(p.label)+'</b><div class="muted small">Прогноз: '+esc(Array.isArray(p.answer)?p.answer.join(", "):p.answer)+'</div>'+(p.correct_answer===null?'':'<div class="muted small">Правильно: '+esc(Array.isArray(p.correct_answer)?p.correct_answer.join(", "):p.correct_answer)+'</div>')+'</div><b>+'+p.reward+' МК</b></div>'});el("mine").innerHTML=html||"Поки що немає прогнозів.";html="";state.leaderboard.forEach(function(p){html+='<div class="listrow"><b>#'+p.place+' '+esc(p.nick)+'</b><b>'+p.total+' МК</b></div>'});el("board").innerHTML=html||"Рейтинг поки порожній.";if(state.user.is_admin){el("admin").classList.remove("hidden");if(!el("agame").options.length){for(var i=1;i<=10;i++)el("agame").add(new Option("Гра "+i,i))}var active=state.states.find(function(s){return s.status==="open"});if(active)el("agame").value=active.game;html="";state.states.slice().reverse().forEach(function(s){html+='<div class="listrow"><span>Гра '+s.game+' · '+esc(s.label)+'</span>'+tag(s.status)+'</div>'});el("states").innerHTML=html||"Ще немає відкриттів.";if(!el("editor").dataset.ready){renderEditor();el("editor").dataset.ready="1"}}}
function toggleBest(n){if(best.has(n))best.delete(n);else if(best.size<3)best.add(n);document.querySelectorAll("[id^=b]").forEach(function(b){b.classList.toggle("selected",best.has(Number(b.textContent)))});}
function renderEditor(){var t=el("atype").value,html="",i;correct.clear();if(t==="winner")html='<select class="form" id="correct"><option value="peaceful">Мирні</option><option value="mafia">Мафія</option></select>';else if(t==="best_move"){for(i=1;i<=10;i++)html+='<button class="btn secondary" style="width:18%;margin-right:1%" id="c'+i+'" onclick="toggleCorrect('+i+')">'+i+'</button>';html+='<div class="muted small">Обери три номери, названі першим убитим гравцем.</div>'}else{html='<select class="form" id="correct">';for(i=1;i<=10;i++)html+='<option value="'+i+'">Гравець №'+i+'</option>';html+='</select>'}el("editor").innerHTML=html}
function toggleCorrect(n){if(correct.has(n))correct.delete(n);else if(correct.size<3)correct.add(n);document.querySelectorAll("[id^=c]").forEach(function(b){b.classList.toggle("selected",correct.has(Number(b.textContent)))});}
async function submit(g,t,a){try{await api("/api/predictions/submit",{game:g,type:t,answer:a});msg("✅ Прогноз збережено! Повторна подача в цій категорії заблокована.","success");await refresh()}catch(e){msg(e.message,"error")}}
async function submitBest(g){if(best.size!==3){msg("Обери три різні номери.","error");return}await submit(g,"best_move",Array.from(best).sort(function(a,b){return a-b}));best.clear()}
async function adminAction(action){var g=Number(el("agame").value),t=el("atype").value,b={game:g,type:t};if(action==="open")b.duration_seconds=Number(el("duration").value||60);if(action==="settle"){if(t==="best_move"){if(correct.size!==3){msg("Обери три правильні номери.","error");return}b.correct_answer=Array.from(correct).sort(function(a,b){return a-b})}else b.correct_answer=el("correct").value}try{var d=await api("/api/predictions/admin/"+action,b);msg(d.message||"Готово.","success");correct.clear();await refresh()}catch(e){msg(e.message,"error")}}
el("atype").addEventListener("change",renderEditor);
async function refresh(){try{state=await api("/api/predictions/state");el("loading").classList.add("hidden");render()}catch(e){el("loading").textContent=e.message}}
if(!initData)el("loading").textContent="Відкрий сторінку через кнопку в Telegram MafMarket.";else{refresh();setInterval(refresh,15000)}
</script></body></html>'''
