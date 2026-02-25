import logging
import random
import sqlite3
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ------------------ الإعدادات الأساسية ------------------
BOT_TOKEN = "8735004353:AAEsYjk1jmLE3m5Buhyoi6vFbDCgRGOgLx4"  # تم إدخال التوكن الخاص بك
DB_PATH = "tournament.db"       # ملف قاعدة البيانات

# إعدادات التسجيل (لرؤية الأخطاء)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------ دوال قاعدة البيانات ------------------
def init_db():
    """إنشاء الجداول إذا لم تكن موجودة."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # جدول الفرق
    c.execute('''CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL)''')
    # جدول اللاعبين
    c.execute('''CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    team_id INTEGER NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
                    UNIQUE(name, team_id))''')  # منع تكرار اللاعب في نفس الفريق
    # جدول المباريات
    c.execute('''CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name TEXT NOT NULL,
                    team1_id INTEGER NOT NULL,
                    team2_id INTEGER NOT NULL,
                    score1 INTEGER DEFAULT 0,
                    score2 INTEGER DEFAULT 0,
                    played BOOLEAN DEFAULT 0,
                    FOREIGN KEY(team1_id) REFERENCES teams(id),
                    FOREIGN KEY(team2_id) REFERENCES teams(id))''')
    # جدول الأهداف
    c.execute('''CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
                    FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE)''')
    conn.commit()
    conn.close()

def db_execute(query: str, params: tuple = ()):
    """تنفيذ استعلام وإرجاع النتيجة (للاستعلامات)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchall()
    conn.close()
    return result

def db_insert(query: str, params: tuple) -> int:
    """إدراج وإرجاع آخر id."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    last_id = c.lastrowid
    conn.close()
    return last_id

# ------------------ دوال المساعدة ------------------
def team_count() -> int:
    """عدد الفرق المسجلة."""
    res = db_execute("SELECT COUNT(*) FROM teams")
    return res[0][0]

def get_team_id(name: str) -> Optional[int]:
    """إرجاع id الفريق حسب الاسم."""
    res = db_execute("SELECT id FROM teams WHERE name = ?", (name,))
    return res[0][0] if res else None

def get_team_name(team_id: int) -> Optional[str]:
    """إرجاع اسم الفريق حسب id."""
    res = db_execute("SELECT name FROM teams WHERE id = ?", (team_id,))
    return res[0][0] if res else None

def get_player_id(name: str, team_id: int) -> Optional[int]:
    """إرجاع id اللاعب إذا كان موجوداً في الفريق."""
    res = db_execute("SELECT id FROM players WHERE name = ? AND team_id = ?", (name, team_id))
    return res[0][0] if res else None

def list_teams() -> List[str]:
    """إرجاع قائمة بأسماء الفرق."""
    res = db_execute("SELECT name FROM teams ORDER BY name")
    return [row[0] for row in res]

def get_team_players(team_id: int) -> List[str]:
    """إرجاع قائمة بأسماء لاعبي فريق معين."""
    res = db_execute("SELECT name FROM players WHERE team_id = ? ORDER BY name", (team_id,))
    return [row[0] for row in res]

def create_groups():
    """تقسيم الفرق عشوائياً إلى مجموعتين (A, B) متساويتين تقريباً."""
    teams = db_execute("SELECT id FROM teams")
    team_ids = [row[0] for row in teams]
    random.shuffle(team_ids)
    mid = (len(team_ids) + 1) // 2
    group_a = team_ids[:mid]
    group_b = team_ids[mid:]
    return group_a, group_b

def generate_fixtures(group_a: List[int], group_b: List[int]):
    """إنشاء مباريات دوري من دور واحد داخل كل مجموعة."""
    # مسح المباريات السابقة (اختياري: يمكن إبقاؤها، لكن لتجنب التكرار نحذف)
    db_execute("DELETE FROM matches")
    # مجموعة A
    for i in range(len(group_a)):
        for j in range(i+1, len(group_a)):
            db_insert(
                "INSERT INTO matches (group_name, team1_id, team2_id) VALUES (?, ?, ?)",
                ("A", group_a[i], group_a[j])
            )
    # مجموعة B
    for i in range(len(group_b)):
        for j in range(i+1, len(group_b)):
            db_insert(
                "INSERT INTO matches (group_name, team1_id, team2_id) VALUES (?, ?, ?)",
                ("B", group_b[i], group_b[j])
            )

def get_standings() -> Dict[str, List[Tuple]]:
    """
    إرجاع ترتيب الفرق في كل مجموعة.
    المفتاح: اسم المجموعة (A, B)
    القيمة: قائمة tuples (team_name, played, wins, draws, losses, goals_for, goals_against, points)
    مرتبة حسب النقث ثم فارق الأهداف.
    """
    teams = db_execute("SELECT id, name FROM teams")
    team_info = {row[0]: row[1] for row in teams}
    # هيكل البيانات: {team_id: {"played":0, "wins":0, "draws":0, "losses":0, "gf":0, "ga":0, "pts":0}}
    stats = {tid: {"played":0, "wins":0, "draws":0, "losses":0, "gf":0, "ga":0, "pts":0} for tid in team_info}

    matches = db_execute("SELECT team1_id, team2_id, score1, score2, played FROM matches WHERE played=1")
    for t1, t2, s1, s2, _ in matches:
        # تحديث إحصائيات الفريق الأول
        stats[t1]["played"] += 1
        stats[t1]["gf"] += s1
        stats[t1]["ga"] += s2
        # الفريق الثاني
        stats[t2]["played"] += 1
        stats[t2]["gf"] += s2
        stats[t2]["ga"] += s1

        if s1 > s2:  # فوز الأول
            stats[t1]["wins"] += 1
            stats[t1]["pts"] += 3
            stats[t2]["losses"] += 1
        elif s1 < s2:  # فوز الثاني
            stats[t2]["wins"] += 1
            stats[t2]["pts"] += 3
            stats[t1]["losses"] += 1
        else:  # تعادل
            stats[t1]["draws"] += 1
            stats[t1]["pts"] += 1
            stats[t2]["draws"] += 1
            stats[t2]["pts"] += 1

    # تجميع الفرق حسب المجموعة
    groups = {"A": [], "B": []}
    # نحتاج لمعرفة مجموعة كل فريق: من جدول المباريات نأخذ أول مباراة لكل فريق لنعرف مجموعته
    team_group = {}
    for g in ['A', 'B']:
        teams_in_group = db_execute("SELECT DISTINCT team1_id FROM matches WHERE group_name=? UNION SELECT DISTINCT team2_id FROM matches WHERE group_name=?", (g, g))
        for (tid,) in teams_in_group:
            team_group[tid] = g

    for tid, name in team_info.items():
        group = team_group.get(tid, "?")
        s = stats[tid]
        groups[group].append((
            name,
            s["played"],
            s["wins"],
            s["draws"],
            s["losses"],
            s["gf"],
            s["ga"],
            s["pts"],
            s["gf"] - s["ga"]  # فارق الأهداف للترتيب
        ))

    # ترتيب كل مجموعة: حسب النقاط ثم فارق الأهداف
    for g in groups:
        groups[g].sort(key=lambda x: (x[7], x[8]), reverse=True)  # pts then goal diff

    return groups

def get_top_scorers(limit: int = 10) -> List[Tuple[str, int]]:
    """إرجاع قائمة بأفضل الهدافين مع عدد الأهداف."""
    res = db_execute('''
        SELECT p.name, COUNT(g.id) as goals
        FROM players p
        LEFT JOIN goals g ON p.id = g.player_id
        GROUP BY p.id
        ORDER BY goals DESC
        LIMIT ?
    ''', (limit,))
    return [(row[0], row[1]) for row in res]

def get_match_info(match_id: int) -> Optional[Dict]:
    """إرجاع معلومات مباراة محددة."""
    res = db_execute('''
        SELECT m.id, m.group_name, t1.name, t2.name, m.score1, m.score2, m.played
        FROM matches m
        JOIN teams t1 ON m.team1_id = t1.id
        JOIN teams t2 ON m.team2_id = t2.id
        WHERE m.id = ?
    ''', (match_id,))
    if not res:
        return None
    row = res[0]
    return {
        "id": row[0],
        "group": row[1],
        "team1": row[2],
        "team2": row[3],
        "score1": row[4],
        "score2": row[5],
        "played": bool(row[6])
    }

def get_goals_in_match(match_id: int) -> List[Tuple[str, str]]:
    """إرجاع قائمة بالأهداف في مباراة: (اسم اللاعب, اسم الفريق)."""
    res = db_execute('''
        SELECT p.name, t.name
        FROM goals g
        JOIN players p ON g.player_id = p.id
        JOIN teams t ON p.team_id = t.id
        WHERE g.match_id = ?
    ''', (match_id,))
    return [(row[0], row[1]) for row in res]

# ------------------ أوامر البوت ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب وعرض الأوامر."""
    text = (
        "⚽ بوت إدارة البطولة ⚽\n\n"
        "الأوامر المتاحة:\n"
        "/addteam <اسم الفريق> - إضافة فريق (حد أقصى 8)\n"
        "/delteam <اسم الفريق> - حذف فريق\n"
        "/addplayer <اسم الفريق> <اسم اللاعب> - إضافة لاعب لفريق\n"
        "/players <اسم الفريق> - عرض لاعبي فريق\n"
        "/creategroups - تقسيم عشوائي إلى مجموعتين (يُنشئ جدول المباريات)\n"
        "/matches - عرض جدول المباريات\n"
        "/match <رقم المباراة> - تفاصيل مباراة\n"
        "/setscore <رقم المباراة> <نتيجة1> <نتيجة2> - تسجيل نتيجة المباراة\n"
        "/addgoal <رقم المباراة> <اسم اللاعب> - إضافة هدف للاعب (بعد تسجيل النتيجة)\n"
        "/standings - جدول ترتيب الفرق\n"
        "/topscorers - أفضل 10 هدافين\n"
        "/help - عرض هذه الرسالة"
    )
    await update.message.reply_text(text)

async def add_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة فريق جديد."""
    if not context.args:
        await update.message.reply_text("❗ استخدم: /addteam <اسم الفريق>")
        return
    name = " ".join(context.args).strip()
    if team_count() >= 8:
        await update.message.reply_text("❌ لا يمكن إضافة المزيد من الفرق، الحد الأقصى 8 فرق.")
        return
    try:
        db_insert("INSERT INTO teams (name) VALUES (?)", (name,))
        await update.message.reply_text(f"✅ تم إضافة الفريق {name} بنجاح.")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"❌ الفريق {name} موجود بالفعل.")

async def del_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف فريق (سيتم حذف اللاعبين والمباريات المرتبطة تلقائياً بسبب ON DELETE CASCADE)."""
    if not context.args:
        await update.message.reply_text("❗ استخدم: /delteam <اسم الفريق>")
        return
    name = " ".join(context.args).strip()
    team_id = get_team_id(name)
    if not team_id:
        await update.message.reply_text(f"❌ الفريق {name} غير موجود.")
        return
    db_execute("DELETE FROM teams WHERE id = ?", (team_id,))
    await update.message.reply_text(f"✅ تم حذف الفريق {name} وجميع بياناته.")

async def add_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة لاعب إلى فريق."""
    if len(context.args) < 2:
        await update.message.reply_text("❗ استخدم: /addplayer <اسم الفريق> <اسم اللاعب>")
        return
    team_name = context.args[0]
    player_name = " ".join(context.args[1:]).strip()
    team_id = get_team_id(team_name)
    if not team_id:
        await update.message.reply_text(f"❌ الفريق {team_name} غير موجود.")
        return
    try:
        db_insert("INSERT INTO players (name, team_id) VALUES (?, ?)", (player_name, team_id))
        await update.message.reply_text(f"✅ تم إضافة اللاعب {player_name} إلى فريق {team_name}.")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"❌ اللاعب {player_name} موجود بالفعل في فريق {team_name}.")

async def players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لاعبي فريق."""
    if not context.args:
        await update.message.reply_text("❗ استخدم: /players <اسم الفريق>")
        return
    team_name = " ".join(context.args).strip()
    team_id = get_team_id(team_name)
    if not team_id:
        await update.message.reply_text(f"❌ الفريق {team_name} غير موجود.")
        return
    players_list = get_team_players(team_id)
    if not players_list:
        await update.message.reply_text(f"⚽ فريق {team_name} لا يوجد به لاعبون بعد.")
    else:
        text = f"لاعبو فريق {team_name}:\n" + "\n".join(f"• {p}" for p in players_list)
        await update.message.reply_text(text)

async def create_groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقسيم الفرق إلى مجموعتين وإنشاء جدول المباريات."""
    if team_count() < 2:
        await update.message.reply_text("❌ يجب وجود فريقين على الأقل لإنشاء المجموعات.")
        return
    group_a, group_b = create_groups()
    generate_fixtures(group_a, group_b)
    # عرض المجموعات
    names_a = [get_team_name(tid) for tid in group_a]
    names_b = [get_team_name(tid) for tid in group_b]
    text = "✅ تم إنشاء المجموعات وجدول المباريات:\n\n"
    text += "المجموعة A:\n" + "\n".join(f"• {name}" for name in names_a) + "\n\n"
    text += "المجموعة B:\n" + "\n".join(f"• {name}" for name in names_b)
    await update.message.reply_text(text)

async def list_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع المباريات."""
    matches = db_execute('''
        SELECT m.id, m.group_name, t1.name, t2.name, m.score1, m.score2, m.played
        FROM matches m
        JOIN teams t1 ON m.team1_id = t1.id
        JOIN teams t2 ON m.team2_id = t2.id
        ORDER BY m.id
    ''')
    if not matches:
        await update.message.reply_text("❌ لا توجد مباريات بعد. استخدم /creategroups أولاً.")
        return
    lines = []
    for m in matches:
        status = "✅" if m[6] else "⏳"
        lines.append(f"{status} ID {m[0]} | مجموعة {m[1]}: {m[2]} vs {m[3]} - {m[4]}:{m[5]}")
    text = "📅 جدول المباريات:\n" + "\n".join(lines)
    await update.message.reply_text(text)

async def match_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تفاصيل مباراة محددة (بما في ذلك الأهداف)."""
    if not context.args:
        await update.message.reply_text("❗ استخدم: /match <رقم المباراة>")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❗ رقم المباراة يجب أن يكون رقماً.")
        return
    match = get_match_info(match_id)
    if not match:
        await update.message.reply_text("❌ لا توجد مباراة بهذا الرقم.")
        return
    text = f"📌 مباراة ID {match['id']} (مجموعة {match['group']}):\n"
    text += f"{match['team1']} vs {match['team2']}\n"
    if match['played']:
        text += f"النتيجة: {match['score1']} - {match['score2']}\n"
        goals = get_goals_in_match(match_id)
        if goals:
            text += "⚽ الأهداف:\n"
            for player, team in goals:
                text += f"   • {player} ({team})\n"
        else:
            text += "لم يسجل أي هدف.\n"
    else:
        text += "لم تلعب بعد."
    await update.message.reply_text(text)

async def set_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل نتيجة مباراة (يجب أن تكون المباراة غير ملعوبة)."""
    if len(context.args) != 3:
        await update.message.reply_text("❗ استخدم: /setscore <رقم المباراة> <نتيجة1> <نتيجة2>")
        return
    try:
        match_id = int(context.args[0])
        score1 = int(context.args[1])
        score2 = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❗ الأرقام غير صحيحة.")
        return
    match = get_match_info(match_id)
    if not match:
        await update.message.reply_text("❌ لا توجد مباراة بهذا الرقم.")
        return
    if match['played']:
        await update.message.reply_text("❌ هذه المباراة مسجل نتيجتها مسبقاً. لا يمكن التعديل.")
        return
    # تحديث النتيجة
    db_execute("UPDATE matches SET score1=?, score2=?, played=1 WHERE id=?", (score1, score2, match_id))
    await update.message.reply_text(f"✅ تم تسجيل نتيجة المباراة {match_id}: {match['team1']} {score1} - {score2} {match['team2']}")

async def add_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة هدف للاعب في مباراة معينة (بعد تسجيل النتيجة)."""
    if len(context.args) < 2:
        await update.message.reply_text("❗ استخدم: /addgoal <رقم المباراة> <اسم اللاعب>")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❗ رقم المباراة يجب أن يكون رقماً.")
        return
    player_name = " ".join(context.args[1:]).strip()
    match = get_match_info(match_id)
    if not match:
        await update.message.reply_text("❌ لا توجد مباراة بهذا الرقم.")
        return
    if not match['played']:
        await update.message.reply_text("❌ يجب تسجيل نتيجة المباراة أولاً باستخدام /setscore.")
        return

    # البحث عن اللاعب: نحتاج معرفة الفريق الذي سجل الهدف. نفترض أن الاسم فريد أو نطلب اسم الفريق.
    # لتسهيل الاستخدام، سنبحث عن اللاعب في جميع الفرق ونتأكد من أنه ينتمي لأحد الفريقين في المباراة.
    players_in_match = db_execute('''
        SELECT p.id, p.name, t.name as team_name
        FROM players p
        JOIN teams t ON p.team_id = t.id
        WHERE t.id IN (?, ?)
    ''', (match['team1_id'], match['team2_id']))
    candidates = [(pid, pname, tname) for pid, pname, tname in players_in_match if pname == player_name]
    if not candidates:
        await update.message.reply_text(f"❌ اللاعب {player_name} غير موجود في أي من الفريقين المشاركين في هذه المباراة.")
        return
    # إذا تكرر الاسم في الفريقين (نادر)، نأخذ الأول
    player_id, _, team_name = candidates[0]

    # إضافة الهدف
    try:
        db_insert("INSERT INTO goals (match_id, player_id) VALUES (?, ?)", (match_id, player_id))
        await update.message.reply_text(f"✅ تم تسجيل هدف للاعب {player_name} ({team_name}) في المباراة {match_id}.")
    except sqlite3.IntegrityError:
        # يمكن أن يحدث إذا كررنا نفس اللاعب في نفس المباراة (لا مانع)
        await update.message.reply_text(f"⚠️ الهدف مضاف مسبقاً (أو خطأ في الإدراج).")

async def standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جدول ترتيب الفرق."""
    groups = get_standings()
    text = ""
    for group_name, standings_list in groups.items():
        if not standings_list:
            continue
        text += f"📊 المجموعة {group_name}:\n"
        header = "فريق                لعب فوز تعادل خسارة له عليه نقاط\n"
        text += header
        for team in standings_list:
            # team: (name, played, wins, draws, losses, gf, ga, pts, gd)
            name = team[0][:15]  # تقطيع الاسم الطويل
            text += f"{name:<16} {team[1]:<3} {team[2]:<3} {team[3]:<3} {team[4]:<3} {team[5]:<3} {team[6]:<3} {team[7]:<3}\n"
        text += "\n"
    if not text:
        text = "لا توجد إحصائيات بعد."
    await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML")

async def topscorers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أفضل 10 هدافين."""
    scorers = get_top_scorers(10)
    if not scorers or all(goals == 0 for _, goals in scorers):
        await update.message.reply_text("⚽ لم يسجل أي هدف حتى الآن.")
        return
    text = "🥇 أفضل الهدافين:\n"
    for i, (name, goals) in enumerate(scorers, 1):
        if goals > 0:
            text += f"{i}. {name} - {goals} هدف\n"
    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة عرض المساعدة."""
    await start(update, context)

# ------------------ تشغيل البوت ------------------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة معالجات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addteam", add_team))
    app.add_handler(CommandHandler("delteam", del_team))
    app.add_handler(CommandHandler("addplayer", add_player))
    app.add_handler(CommandHandler("players", players))
    app.add_handler(CommandHandler("creategroups", create_groups_command))
    app.add_handler(CommandHandler("matches", list_matches))
    app.add_handler(CommandHandler("match", match_detail))
    app.add_handler(CommandHandler("setscore", set_score))
    app.add_handler(CommandHandler("addgoal", add_goal))
    app.add_handler(CommandHandler("standings", standings))
    app.add_handler(CommandHandler("topscorers", topscorers))

    logger.info("البوت يعمل...")
    app.run_polling()

if __name__ == "__main__":
    main()
