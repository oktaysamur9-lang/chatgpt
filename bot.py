import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, request, jsonify
import random
import string
import asyncio
import threading
import aiohttp
import json
import os
from datetime import datetime, timezone

# ==================== AYARLAR ====================
TOKEN = os.environ.get("TOKEN")
SUNUCU_ID = 1494391061798191334
VERIFIED_ROL_IDS = [1494391062205169920, 1494391062205169917, 1494391062205169915]
UNVERIFIED_ROL_ID = 1494391062104510684
ROBLOX_GROUP_ID = 754066477

# /kontrol için minimum rütbe (OF-6 ve üzeri kullanabilir)
KONTROL_MIN_OF_LEVEL = 6
# =================================================

pending_verifications = {}
verified_results = {}
verified_users = set()
verified_roblox = set()
user_roblox_map = {}

# ==================== VERİ KAYIT SİSTEMİ ====================
KAYIT_DOSYASI = "verified_data.json"

def veriyi_yukle():
    global verified_users, verified_roblox, user_roblox_map
    try:
        if not os.path.exists(KAYIT_DOSYASI):
            print("[Kayıt] verified_data.json bulunamadı, boş başlatılıyor.")
            return
        with open(KAYIT_DOSYASI, "r", encoding="utf-8") as f:
            data = json.load(f)
        verified_users  = set(int(x) for x in data.get("verified_users", []))
        verified_roblox = set(data.get("verified_roblox", []))
        user_roblox_map = {int(k): v for k, v in data.get("user_roblox_map", {}).items()}
        print(f"[Kayıt] ✅ {len(user_roblox_map)} kayıt yüklendi.")
    except Exception as e:
        print(f"[Kayıt] ❌ Yükleme hatası: {e}")

def veriyi_kaydet():
    try:
        data = {
            "verified_users":  list(verified_users),
            "verified_roblox": list(verified_roblox),
            "user_roblox_map": {str(k): v for k, v in user_roblox_map.items()}
        }
        with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Kayıt] 💾 Kaydedildi. ({len(user_roblox_map)} kayıt)")
    except Exception as e:
        print(f"[Kayıt] ❌ Kaydetme hatası: {e}")

veriyi_yukle()

# ==================== BOT BAŞLATMA ====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== FLASK ====================
app = Flask(__name__)

import logging
logging.basicConfig(level=logging.DEBUG)

@app.before_request
def log_request():
    print(f"[Flask] 📥 {request.method} {request.path} — port: {app._got_first_request}")

@app.route('/ping', methods=['GET'])
def ping():
    port = int(os.environ.get("PORT", "YOK"))
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "YOK")
    return jsonify({
        "status": "ok",
        "port": port,
        "domain": railway_domain,
        "message": "Flask çalışıyor!"
    })

@app.route('/get_pending', methods=['GET'])
def get_pending():
    username = request.args.get("username", "").lower()
    for discord_id, data in pending_verifications.items():
        if data["roblox_username"].lower() == username:
            return jsonify({
                "status": "found",
                "code": data["code"],
                "discord_username": data["discord_username"]
            })
    return jsonify({"status": "not_found"})

@app.route('/verify_response', methods=['POST'])
def verify_response():
    data = request.json
    code = data.get("code")
    result = data.get("result")
    roblox_username = data.get("roblox_username")
    if not code or not result:
        return jsonify({"status": "error"}), 400
    verified_results[code] = {"result": result, "roblox_username": roblox_username}
    return jsonify({"status": "ok"})

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    print(f"🔧 PORT env değeri: {os.environ.get('PORT', 'TANIMSIZ')}")
    print(f"🔧 Flask {port} portunda başlıyor...")
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if railway_url:
        print(f"🌐 Public URL: https://{railway_url}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== ROBLOX API ====================
async def get_roblox_user_id(username: str):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data["data"]:
                    return data["data"][0]["id"]
    return None

async def get_group_rank(roblox_user_id: int):
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://groups.roblox.com/v2/users/{roblox_user_id}/groups/roles"
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                for group in data.get("data", []):
                    if group["group"]["id"] == ROBLOX_GROUP_ID:
                        return group["role"]["name"]
    return None

async def get_roblox_full_info(roblox_user_id: int):
    info = {
        "display_name": "?",
        "username": "?",
        "created": None,
        "is_banned": False,
        "avatar_url": None,
        "friend_count": 0,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://users.roblox.com/v1/users/{roblox_user_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                info["display_name"] = data.get("displayName", "?")
                info["username"] = data.get("name", "?")
                info["created"] = data.get("created")
                info["is_banned"] = data.get("isBanned", False)

        async with session.get(
            f"https://thumbnails.roblox.com/v1/users/avatar-headshot"
            f"?userIds={roblox_user_id}&size=420x420&format=Png"
        ) as resp:
            if resp.status == 200:
                thumb = await resp.json()
                if thumb.get("data"):
                    info["avatar_url"] = thumb["data"][0].get("imageUrl")

        async with session.get(
            f"https://friends.roblox.com/v1/users/{roblox_user_id}/friends/count"
        ) as resp:
            if resp.status == 200:
                fdata = await resp.json()
                info["friend_count"] = fdata.get("count", 0)

    return info

# ==================== ASKERİ GRUP KONTROLÜ ====================
MILITARY_KEYWORDS = [
    "military", "army", "ordu", "asker", "askeri", "taktik", "tactical",
    "corps", "kolordu", "komando", "commando", "brigade", "tugay",
    "legion", "lejyon", "forces", "kuvvet", "division", "tümen",
    "battalion", "tabur", "regiment", "alay", "squad", "manga",
    "platoon", "takım", "special forces", "özel kuvvet", "warfare",
    "savaş", "combat", "muharebe", "infantry", "piyade", "guard",
    "muhafız", "defense", "savunma", "nato", "military rp", "silahlı"
]

async def check_military_groups(roblox_user_id: int):
    urls = [
        f"https://groups.roproxy.com/v2/users/{roblox_user_id}/groups/roles",
        f"https://groups.roblox.com/v2/users/{roblox_user_id}/groups/roles",
    ]

    groups_data = None
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        groups_data = payload.get("data", [])
                        break
                    elif resp.status == 403:
                        return ("hidden", [])
            except Exception:
                continue

    if groups_data is None:
        return ("hidden", [])

    military_found = []
    for entry in groups_data:
        group_name = entry.get("group", {}).get("name", "")
        name_lower = group_name.lower()
        for kw in MILITARY_KEYWORDS:
            if kw in name_lower:
                military_found.append(group_name)
                break

    if military_found:
        return ("found", military_found)
    return ("clean", [])

# ==================== YARDIMCI FONKSIYONLAR ====================
import re as _re

OF6_ROL_ADI = "OF-6 Tuğgeneral"

def extract_of_level(role_name: str):
    cleaned = role_name.strip().upper()
    match = _re.search(r'OF[\s\-]?(\d+)', cleaned)
    if match:
        return int(match.group(1))
    return None

def get_of6_position(guild: discord.Guild) -> int:
    role = discord.utils.get(guild.roles, name=OF6_ROL_ADI)
    return role.position if role else 0

def has_required_rank(member: discord.Member) -> bool:
    of6_pos = get_of6_position(member.guild)
    for role in member.roles:
        if role.name == "@everyone":
            continue
        if role.position >= of6_pos and of6_pos > 0:
            return True
    return False

def get_member_rank_name(member: discord.Member) -> str:
    roles = [r for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "Bilinmiyor"
    return max(roles, key=lambda r: r.position).name

def debug_roles(member: discord.Member) -> str:
    of6_pos = get_of6_position(member.guild)
    lines = [f"*(OF-6 Tuğgeneral pozisyonu: {of6_pos})*"]
    for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
        if role.name == "@everyone":
            continue
        if role.position >= of6_pos and of6_pos > 0:
            lines.append(f"✅ `{role.name}` (pos:{role.position}) → YETERLİ")
        else:
            lines.append(f"⬜ `{role.name}` (pos:{role.position}) → yetersiz")
    return "\n".join(lines) if lines else "(hiç rol bulunamadı)"

def calculate_age(created_iso: str):
    try:
        created_dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created_dt
        total_days = delta.days
        years = total_days // 365
        months = (total_days % 365) // 30
        days_rem = total_days % 30
        return created_dt, total_days, years, months, days_rem
    except Exception:
        return None, 0, 0, 0, 0



def build_age_bar(total_days: int) -> str:
    max_days = 365 * 5
    filled = min(int((total_days / max_days) * 10), 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"`[{bar}]`"

# ==================== /KONTROL KOMUTU ====================
@bot.tree.command(name="kontrol", description="Verify edilmiş bir üyenin Roblox personik dosyasını görüntüle")
@app_commands.describe(kullanici="Kontrol edilecek Discord üyesi")
async def kontrol(interaction: discord.Interaction, kullanici: discord.Member):
    await interaction.response.defer(ephemeral=False)

    if not has_required_rank(interaction.user):
        await interaction.followup.send("⛔ Bu komutu kullanmak için yetkin yok.", ephemeral=True)
        return

    target_id = kullanici.id
    if target_id not in user_roblox_map:
        await interaction.followup.send(f"**{kullanici.display_name}** sisteme kayıtlı değil.", ephemeral=True)
        return

    data = user_roblox_map[target_id]
    roblox_id       = data["roblox_id"]
    roblox_username = data["roblox_username"]
    current_rank    = data.get("current_rank_name") or "Grupta Değil"

    info, mil_result = await asyncio.gather(
        get_roblox_full_info(roblox_id),
        check_military_groups(roblox_id)
    )

    mil_status, mil_groups = mil_result

    created_dt, total_days, _, _, _ = calculate_age(info["created"] or "")

    age_str = f"{total_days:,} gün"
    created_ts = f"<t:{int(created_dt.timestamp())}:D>" if created_dt else "Bilinmiyor"

    if mil_status == "hidden":
        mil_field_value = "Gizlenmiş"
    elif mil_status == "found":
        group_list = ", ".join(mil_groups[:5])
        mil_field_value = f"Evet — {group_list}"
    else:
        mil_field_value = "Hayır"

    ban_line = "> ⚠️ Bu hesap Roblox tarafından banlanmıştır!\n\n" if info["is_banned"] else ""

    embed = discord.Embed(timestamp=datetime.now(timezone.utc))
    embed.title = roblox_username

    if info["avatar_url"]:
        embed.set_thumbnail(url=info["avatar_url"])

    embed.description = (
        f"{ban_line}"
        f"**Hesap Yaşı:** {age_str}\n"
        f"**Kayıt Tarihi:** {created_ts}\n"
        f"**Grup Rütbesi:** {current_rank}\n"
        f"**Asker Grubu:** {mil_field_value}\n\n"
        f"**Discord:** {kullanici.display_name} (`{kullanici}`)\n"
        f"**Roblox ID:** `{roblox_id}`"
    )

    embed.set_footer(text=f"Sorgulayan: {interaction.user.display_name} • {get_member_rank_name(interaction.user)}")

    await interaction.followup.send(embed=embed)

# ── BUTONLU VIEW ──────────────────────────────────────────────────
class KontrolView(discord.ui.View):
    def __init__(self, roblox_username: str, roblox_id: int, discord_user: discord.Member):
        super().__init__(timeout=120)
        self.roblox_username = roblox_username
        self.roblox_id = roblox_id

        self.add_item(discord.ui.Button(
            label="Roblox Profili",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/users/{roblox_id}/profile"
        ))
        self.add_item(discord.ui.Button(
            label="Grubu Görüntüle",
            emoji="🏛️",
            style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/groups/{ROBLOX_GROUP_ID}"
        ))

    @discord.ui.button(label="Arkadaş Listesi", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def friend_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_required_rank(interaction.user):
            await interaction.response.send_message("⛔ Yetkin yok!", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔗 **{self.roblox_username}** arkadaş listesi:\n"
            f"<https://www.roblox.com/users/{self.roblox_id}/friends>",
            ephemeral=True
        )

    @discord.ui.button(label="Grup Rankını Yenile", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def refresh_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_required_rank(interaction.user):
            await interaction.response.send_message("⛔ Yetkin yok!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        new_rank = await get_group_rank(self.roblox_id)
        await interaction.followup.send(
            f"🔄 **{self.roblox_username}** güncel grup rankı: **{new_rank or 'Grupta Değil'}**",
            ephemeral=True
        )


# ==================== VERİFY DURDURMA VIEW ====================
class VerifyView(discord.ui.View):
    """
    /verify komutunun ephemeral mesajına eklenen view.
    Kullanıcı 'Doğrulamayı Durdur' butonuna basarsa pending_verifications'dan
    kaydı silinir ve wait_for_result görevi bir sonraki döngüde 'iptal edildi'
    durumunu fark ederek durur.
    """
    def __init__(self, discord_id: int, roblox_username: str):
        super().__init__(timeout=300)  # 5 dakika
        self.discord_id = discord_id
        self.roblox_username = roblox_username
        self.cancelled = False

        # Roblox oyun linki butonu
        self.add_item(discord.ui.Button(
            label="Roblox Oyununa Git",
            emoji="🎮",
            style=discord.ButtonStyle.link,
            url="https://www.roblox.com/tr/games/130926747712224/TTC-I-Verify"
        ))

    @discord.ui.button(label="Doğrulamayı Durdur", emoji="🛑", style=discord.ButtonStyle.danger, row=1)
    async def cancel_verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Sadece kendi doğrulamasını iptal edebilir
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("⛔ Bu buton sana ait değil!", ephemeral=True)
            return

        if self.discord_id not in pending_verifications:
            await interaction.response.send_message(
                "ℹ️ Doğrulama zaten tamamlanmış veya daha önce iptal edilmiş.",
                ephemeral=True
            )
            self.stop()
            return

        # Pending listesinden sil
        data = pending_verifications.pop(self.discord_id, None)
        self.cancelled = True

        # Butonu devre dışı bırak
        button.disabled = True
        button.label = "İptal Edildi"
        await interaction.response.edit_message(
            content=(
                f"🛑 **Doğrulama iptal edildi.**\n"
                f"Roblox hesabı: **{self.roblox_username}**\n\n"
                f"> Yanlış hesap yazdıysanız `/verify` ile tekrar başlayabilirsiniz."
            ),
            view=self
        )
        self.stop()
        print(f"[Bot] 🛑 {interaction.user} doğrulamayı iptal etti ({self.roblox_username})")


# ==================== DISCORD BOT ====================
@bot.event
async def on_ready():
    guild = discord.Object(id=SUNUCU_ID)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    print(f"✅ {bot.user} aktif! {len(synced)} komut sync edildi.")
    activity = discord.Activity(type=discord.ActivityType.playing, name="Roblox TTC")
    await bot.change_presence(activity=activity)
    asyncio.create_task(rank_check_loop())

@bot.event
async def on_member_join(member: discord.Member):
    try:
        role = member.guild.get_role(UNVERIFIED_ROL_ID)
        if role:
            await member.add_roles(role)
            print(f"[Bot] Yeni üye {member} -> unverified rolü verildi")
    except Exception as e:
        print(f"[Bot] Unverified rol hatası: {e}")

@bot.tree.command(name="verify", description="Roblox hesabını doğrula")
@app_commands.describe(roblox_kullanici_adi="Roblox kullanıcı adın")
async def verify(interaction: discord.Interaction, roblox_kullanici_adi: str):
    await interaction.response.defer(ephemeral=True)

    discord_id = interaction.user.id
    discord_username = str(interaction.user)

    # ── Kalıcı liste kontrolü ──────────────────────────────────
    # verified_data.json'dan yüklenen verified_users seti bot yeniden başlasa
    # bile dolu gelir; aşağıdaki kontrol her zaman çalışır.
    if discord_id in verified_users:
        await interaction.followup.send(
            "⛔ Bu Discord hesabı zaten doğrulanmış!\n"
            "> Eğer hesabını değiştirmek istiyorsan bir yetkiliyle iletişime geç.",
            ephemeral=True
        )
        return

    if roblox_kullanici_adi.lower() in verified_roblox:
        await interaction.followup.send(
            f"⛔ **{roblox_kullanici_adi}** Roblox hesabı zaten başkası tarafından doğrulanmış!\n"
            "> Başka bir hesap dene ya da yetkililere başvur.",
            ephemeral=True
        )
        return

    if discord_id in pending_verifications:
        await interaction.followup.send(
            "⏳ Zaten bekleyen bir doğrulaman var!\n"
            "> İptal etmek için önceki mesajdaki **Doğrulamayı Durdur** butonuna bas.",
            ephemeral=True
        )
        return

    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    pending_verifications[discord_id] = {
        "roblox_username": roblox_kullanici_adi,
        "discord_username": discord_username,
        "code": code
    }

    print(f"[Bot] Verify başlatıldı: {discord_username} -> {roblox_kullanici_adi}")

    # ── DM bildirimi ───────────────────────────────────────────
    try:
        dm_embed = discord.Embed(
            title="🔐 Doğrulama Başlatıldı",
            description=(
                f"Merhaba **{interaction.user.display_name}**!\n\n"
                f"**{roblox_kullanici_adi}** Roblox hesabın için doğrulama süreci başlatıldı.\n\n"
                f"**Ne yapmalısın?**\n"
                f"> 1️⃣ Aşağıdaki Roblox oyununa gir\n"
                f"> 2️⃣ Oyun içinde doğrulama ekranına gel\n"
                f"> 3️⃣ **Evet** seçeneğini seç\n\n"
                f"⚠️ Yanlış hesap yazdıysanız sunucudaki mesajdan **Doğrulamayı Durdur** butonuna basın."
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(
            name="🎮 Roblox Oyunu",
            value="[TTC-I-Verify](https://www.roblox.com/tr/games/130926747712224/TTC-I-Verify)",
            inline=False
        )
        dm_embed.add_field(name="👤 Roblox Hesabı", value=f"`{roblox_kullanici_adi}`", inline=True)
        dm_embed.add_field(name="⏰ Süre", value="5 dakika", inline=True)
        dm_embed.set_footer(text="TTC Doğrulama Sistemi")
        dm_embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.user.send(embed=dm_embed)
        print(f"[Bot] 📨 DM gönderildi: {discord_username}")
    except discord.Forbidden:
        # Kullanıcı DM'leri kapalıysa sessizce geç
        print(f"[Bot] ⚠️ DM gönderilemedi (kapalı): {discord_username}")
    except Exception as e:
        print(f"[Bot] ❌ DM hatası: {e}")

    # ── Verify view (durdur butonu + oyun linki) ───────────────
    view = VerifyView(discord_id=discord_id, roblox_username=roblox_kullanici_adi)

    await interaction.followup.send(
        embed=discord.Embed(
            title="⏳ Doğrulama Bekleniyor",
            description=(
                f"**Roblox Hesabı:** `{roblox_kullanici_adi}`\n\n"
                f"Roblox oyununa gir ve doğrulama ekranında **Evet**'e bas.\n\n"
                f"> ⚠️ Yanlış hesap yazdıysanız aşağıdaki **Doğrulamayı Durdur** butonuna basın."
            ),
            color=0xFFCC00,
            timestamp=datetime.now(timezone.utc)
        ),
        view=view,
        ephemeral=True
    )

    asyncio.create_task(wait_for_result(interaction, discord_id, roblox_kullanici_adi, code, view))


async def apply_group_role(member: discord.Member, roblox_user_id: int, roblox_username: str, old_rank_name: str = None):
    guild = bot.get_guild(SUNUCU_ID)
    rank_name = await get_group_rank(roblox_user_id)

    if rank_name is None:
        print(f"[Rank] {roblox_username} grupta değil")
        return None

    print(f"[Rank] {roblox_username} grup rankı: {rank_name}")

    if old_rank_name and old_rank_name != rank_name:
        old_role = discord.utils.get(guild.roles, name=old_rank_name)
        if old_role and old_role in member.roles:
            try:
                await member.remove_roles(old_role)
                print(f"[Rank] Eski rol kaldırıldı: {old_rank_name}")
            except Exception as e:
                print(f"[Rank] Eski rol kaldırma hatası: {e}")

    new_role = discord.utils.get(guild.roles, name=rank_name)
    if new_role:
        try:
            await member.add_roles(new_role)
            print(f"[Rank] Yeni rol verildi: {rank_name}")
        except Exception as e:
            print(f"[Rank] Yeni rol verme hatası: {e}")
    else:
        print(f"[Rank] '{rank_name}' adlı Discord rolü bulunamadı!")

    return rank_name


async def wait_for_result(interaction, discord_id, roblox_username, code, verify_view: VerifyView = None):
    for _ in range(300):
        await asyncio.sleep(1)

        # ── İptal kontrolü ─────────────────────────────────────
        # Kullanıcı butona bastıysa pending'den silindi; kod da artık yok.
        if discord_id not in pending_verifications:
            # Eğer verified_results'ta da yoksa kullanıcı iptal etti
            if code not in verified_results:
                print(f"[Bot] 🛑 wait_for_result: {roblox_username} iptal nedeniyle durdu")
                return

        if code in verified_results:
            result_data = verified_results.pop(code)
            pending_verifications.pop(discord_id, None)
            result = result_data["result"]

            if verify_view:
                verify_view.stop()

            if result == "yes":
                guild = bot.get_guild(SUNUCU_ID)
                member = guild.get_member(discord_id)

                if member:
                    try:
                        await member.edit(nick=roblox_username)
                        print(f"[Bot] ✅ Nickname değiştirildi: {member} -> {roblox_username}")
                    except discord.Forbidden:
                        print(f"[Bot] ⚠️ Nickname değiştirilemedi: {member}")
                    except Exception as e:
                        print(f"[Bot] ❌ Nickname hatası: {e}")

                    try:
                        unverified_role = guild.get_role(UNVERIFIED_ROL_ID)
                        if unverified_role and unverified_role in member.roles:
                            await member.remove_roles(unverified_role)
                    except Exception as e:
                        print(f"[Bot] Unverified kaldırma hatası: {e}")

                    for rol_id in VERIFIED_ROL_IDS:
                        try:
                            verified_role = guild.get_role(rol_id)
                            if verified_role:
                                await member.add_roles(verified_role)
                        except Exception as e:
                            print(f"[Bot] ❌ Rol hatası {rol_id}: {e}")

                    roblox_user_id = await get_roblox_user_id(roblox_username)
                    rank_name = None
                    if roblox_user_id:
                        rank_name = await apply_group_role(member, roblox_user_id, roblox_username)

                    verified_users.add(discord_id)
                    verified_roblox.add(roblox_username.lower())
                    user_roblox_map[discord_id] = {
                        "roblox_id": roblox_user_id,
                        "roblox_username": roblox_username,
                        "current_rank_name": rank_name
                    }
                    veriyi_kaydet()

                    # ── Başarı DM'i ────────────────────────────────────
                    try:
                        success_embed = discord.Embed(
                            title="✅ Doğrulama Başarılı!",
                            description=(
                                f"**{roblox_username}** Roblox hesabın başarıyla doğrulandı!\n\n"
                                f"Artık sunucuya tam erişimin var. Hoş geldin! 🎉"
                            ),
                            color=0x00BB77,
                            timestamp=datetime.now(timezone.utc)
                        )
                        success_embed.set_footer(text="TTC Doğrulama Sistemi")
                        await member.send(embed=success_embed)
                    except Exception:
                        pass

                await interaction.followup.send(
                    f"✅ **{interaction.user.mention}** başarıyla doğrulandı! Roblox adı: **{roblox_username}**"
                )
            else:
                # ── Başarısız DM'i ─────────────────────────────────
                try:
                    fail_embed = discord.Embed(
                        title="❌ Doğrulama Başarısız",
                        description=(
                            f"**{roblox_username}** hesabı için doğrulama reddedildi.\n\n"
                            f"> Oyun içinde **Hayır** seçeneğini seçtiniz.\n"
                            f"> Tekrar denemek için `/verify` komutunu kullanabilirsiniz."
                        ),
                        color=0xFF2222,
                        timestamp=datetime.now(timezone.utc)
                    )
                    fail_embed.set_footer(text="TTC Doğrulama Sistemi")
                    await interaction.user.send(embed=fail_embed)
                except Exception:
                    pass

                await interaction.followup.send(
                    f"❌ **{interaction.user.mention}** doğrulaması başarısız! Kullanıcı hayırı seçti.",
                    ephemeral=True
                )
            return

    # ── Zaman aşımı ────────────────────────────────────────────
    pending_verifications.pop(discord_id, None)
    if verify_view:
        verify_view.stop()

    try:
        timeout_embed = discord.Embed(
            title="⏰ Doğrulama Süresi Doldu",
            description=(
                f"**{roblox_username}** hesabı için doğrulama 5 dakika içinde tamamlanamadı.\n\n"
                f"> Tekrar denemek için `/verify` komutunu kullanabilirsiniz."
            ),
            color=0xFF8800,
            timestamp=datetime.now(timezone.utc)
        )
        timeout_embed.set_footer(text="TTC Doğrulama Sistemi")
        await interaction.user.send(embed=timeout_embed)
    except Exception:
        pass

    await interaction.followup.send("⏰ Zaman aşımı! Tekrar dene.", ephemeral=True)


async def rank_check_loop():
    await bot.wait_until_ready()
    print("[Rank] Rank kontrol döngüsü başladı!")

    while not bot.is_closed():
        await asyncio.sleep(5)
        guild = bot.get_guild(SUNUCU_ID)
        if not guild:
            continue

        for discord_id, data in list(user_roblox_map.items()):
            roblox_user_id = data["roblox_id"]
            roblox_username = data["roblox_username"]
            old_rank = data["current_rank_name"]

            if not roblox_user_id:
                continue

            new_rank = await get_group_rank(roblox_user_id)

            if new_rank != old_rank:
                print(f"[Rank] ⚡ {roblox_username} rank değişti: {old_rank} -> {new_rank}")
                member = guild.get_member(discord_id)
                if member:
                    new_rank_applied = await apply_group_role(member, roblox_user_id, roblox_username, old_rank)
                    user_roblox_map[discord_id]["current_rank_name"] = new_rank_applied
                    veriyi_kaydet()

# ==================== BAŞLAT ====================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask sunucusu başlatıldı!")
    bot.run(TOKEN)
