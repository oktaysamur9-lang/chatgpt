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
    """Bot başlarken JSON dosyasından verileri yükler."""
    global verified_users, verified_roblox, user_roblox_map
    try:
        import os
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
    """Değişiklik olduğunda JSON dosyasına yazar."""
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

# Başlangıçta yükle
veriyi_yukle()

# ==================== BOT BAŞLATMA ====================
# bot buraya taşındı — tüm @bot.tree.command dekoratörlerinden ÖNCE olması şart
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== FLASK ====================
app = Flask(__name__)

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
    app.run(port=5000, debug=False, use_reloader=False)

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
    """Roblox kullanıcısının tüm bilgilerini çeker."""
    info = {
        "display_name": "?",
        "username": "?",
        "created": None,
        "is_banned": False,
        "avatar_url": None,
        "friend_count": 0,
    }
    async with aiohttp.ClientSession() as session:
        # Kullanıcı profili
        async with session.get(f"https://users.roblox.com/v1/users/{roblox_user_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                info["display_name"] = data.get("displayName", "?")
                info["username"] = data.get("name", "?")
                info["created"] = data.get("created")
                info["is_banned"] = data.get("isBanned", False)

        # Avatar (headshot)
        async with session.get(
            f"https://thumbnails.roblox.com/v1/users/avatar-headshot"
            f"?userIds={roblox_user_id}&size=420x420&format=Png"
        ) as resp:
            if resp.status == 200:
                thumb = await resp.json()
                if thumb.get("data"):
                    info["avatar_url"] = thumb["data"][0].get("imageUrl")

        # Arkadaş sayısı
        async with session.get(
            f"https://friends.roblox.com/v1/users/{roblox_user_id}/friends/count"
        ) as resp:
            if resp.status == 200:
                fdata = await resp.json()
                info["friend_count"] = fdata.get("count", 0)

    return info

# ==================== YARDIMCI FONKSIYONLAR ====================
import re as _re

# OF-6 rolünün tam adı — hiyerarşi karşılaştırması için referans nokta
OF6_ROL_ADI = "OF-6 Tuğgeneral"

def extract_of_level(role_name: str):
    cleaned = role_name.strip().upper()
    match = _re.search(r'OF[\s\-]?(\d+)', cleaned)
    if match:
        return int(match.group(1))
    return None

def get_of6_position(guild: discord.Guild) -> int:
    """Sunucuda OF-6 rolünün pozisyonunu döner."""
    role = discord.utils.get(guild.roles, name=OF6_ROL_ADI)
    return role.position if role else 0

def has_required_rank(member: discord.Member) -> bool:
    """
    OF-6 rolünün Discord hiyerarşisindeki pozisyonundan
    YÜKSEK veya EŞİT pozisyonda en az bir rolü olan herkes geçer.
    Baş Developer, Admin vb. tüm üst roller otomatik dahil olur.
    """
    of6_pos = get_of6_position(member.guild)
    for role in member.roles:
        if role.name == "@everyone":
            continue
        if role.position >= of6_pos and of6_pos > 0:
            return True
    return False

def get_member_rank_name(member: discord.Member) -> str:
    """Kullanıcının en yüksek rolünü döner."""
    roles = [r for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "Bilinmiyor"
    return max(roles, key=lambda r: r.position).name

def debug_roles(member: discord.Member) -> str:
    """Erişim reddedilince rol pozisyonlarını gösterir."""
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
    """ISO tarihinden hesap yaşını hesaplar."""
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

def age_tier(total_days: int):
    """
    Hesap yaşına göre güven seviyesi döner.
    Dönüş: (emoji_bar, etiket, renk_hex)
    """
    if total_days < 30:
        return ("🔴🔴🔴🔴🔴", "ŞÜPHELI — Çok Yeni", 0xFF2222)
    elif total_days < 90:
        return ("🟠🟠🟠🟠⬜", "DİKKAT — Yeni Hesap", 0xFF8800)
    elif total_days < 180:
        return ("🟡🟡🟡⬜⬜", "ORTA — Gelişmekte", 0xFFCC00)
    elif total_days < 365:
        return ("🟢🟢🟢🟢⬜", "İYİ — 6+ Aylık", 0x44CC44)
    elif total_days < 365 * 2:
        return ("🟢🟢🟢🟢🟢", "GÜVENLI — 1+ Yıllık", 0x00BB77)
    elif total_days < 365 * 4:
        return ("💎💎💎💎💎", "VETERAN — 2+ Yıllık", 0x00BFFF)
    else:
        return ("👑👑👑👑👑", "EFSANEVİ — 4+ Yıllık", 0xFFD700)

def build_age_bar(total_days: int) -> str:
    """Görsel progress bar (30 gün birimleriyle, max 5 yıl)."""
    max_days = 365 * 5
    filled = min(int((total_days / max_days) * 10), 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"`[{bar}]`"

# ==================== /KONTROL KOMUTU ====================
@bot.tree.command(name="kontrol", description="Verify edilmiş bir üyenin Roblox personik dosyasını görüntüle")
@app_commands.describe(kullanici="Kontrol edilecek Discord üyesi")
async def kontrol(interaction: discord.Interaction, kullanici: discord.Member):
    await interaction.response.defer(ephemeral=False)

    # ── 1. YETKİ KONTROLÜ ───────────────────────────────────────
    if not has_required_rank(interaction.user):
        rol_debug = debug_roles(interaction.user)
        embed = discord.Embed(
            title="⛔  ERİŞİM REDDEDİLDİ",
            description=(
                "Bu dosyaya erişim yetkiniz bulunmamaktadır.\n"
                f"Minimum gereklilik: **OF-{KONTROL_MIN_OF_LEVEL}** veya üzeri rütbe.\n\n"
                f"**Rollerinizin algılanma durumu:**\n{rol_debug}"
            ),
            color=0xFF2222,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Erişim Günlüğü Kaydedildi — Rol adı OF-X formatında değilse bot algılayamaz")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # ── 2. VERİFY KONTROLÜ ──────────────────────────────────────
    target_id = kullanici.id
    if target_id not in user_roblox_map:
        embed = discord.Embed(
            title="🔍  KAYIT BULUNAMADI",
            description=(
                f"**{kullanici.display_name}** adlı kullanıcı sisteme kayıtlı değil.\n\n"
                f"> Bu kişi ya hiç doğrulama yapmamış,\n"
                f"> ya da başka bir hesapla giriş yapmış olabilir."
            ),
            color=0xFF6600,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=kullanici.display_avatar.url)
        embed.set_footer(text=f"Sorgulayan: {interaction.user} • {get_member_rank_name(interaction.user)}")
        await interaction.followup.send(embed=embed)
        return

    # ── 3. BİLGİLERİ ÇEK ───────────────────────────────────────
    data = user_roblox_map[target_id]
    roblox_id       = data["roblox_id"]
    roblox_username = data["roblox_username"]
    current_rank    = data.get("current_rank_name") or "Grupta Değil"

    # Roblox API'den detaylı bilgi al
    info = await get_roblox_full_info(roblox_id)

    # Hesap yaşı hesapla
    created_dt, total_days, years, months, days_rem = calculate_age(info["created"] or "")
    age_bar_str, age_label, embed_color = age_tier(total_days)
    progress_bar = build_age_bar(total_days)

    if created_dt:
        created_fmt = created_dt.strftime("%d %B %Y")
        created_ts  = f"<t:{int(created_dt.timestamp())}:D>"
    else:
        created_fmt = "Bilinmiyor"
        created_ts  = "Bilinmiyor"

    # Yaş yazısı
    age_parts = []
    if years  > 0: age_parts.append(f"{years} yıl")
    if months > 0: age_parts.append(f"{months} ay")
    if days_rem > 0 or not age_parts: age_parts.append(f"{days_rem} gün")
    age_str = " · ".join(age_parts)

    # Banlı mı?
    banned_field = "```\n⚠️  HESAP BANLANDI\n```" if info["is_banned"] else ""

    # ── 4. EMBED OLUŞTUR ────────────────────────────────────────
    embed = discord.Embed(
        color=embed_color,
        timestamp=datetime.now(timezone.utc)
    )

    # BAŞLIK BLOĞU
    embed.set_author(
        name=f"📂  PERSONİK DOSYA  ·  GİZLİ",
        icon_url="https://i.imgur.com/4M34hi2.png"
    )

    embed.title = (
        f"🪖  {roblox_username}"
        + (f"  ›  {info['display_name']}" if info['display_name'] != roblox_username else "")
    )

    embed.description = (
        f"{'⚠️  **BU HESAP ROBLOX TARAFINDAN BANLANMIŞTIR!**' + chr(10) if info['is_banned'] else ''}"
        f"```ml\n"
        f"  DOSYA NO   : RBX-{roblox_id}\n"
        f"  DURUM      : {'❌ BANLI' if info['is_banned'] else '✅ AKTİF'}\n"
        f"  KAYIT      : {created_fmt}\n"
        f"```"
    )

    # ROBLOX BİLGİLERİ
    embed.add_field(
        name="👤  ROBLOX KİMLİĞİ",
        value=(
            f"```\n"
            f"Kullanıcı Adı : {info['username']}\n"
            f"Görünen Ad    : {info['display_name']}\n"
            f"Kullanıcı ID  : {roblox_id}\n"
            f"Arkadaş       : {info['friend_count']:,}\n"
            f"```"
        ),
        inline=False
    )

    # HESAP YAŞI BLOĞU
    embed.add_field(
        name="📅  HESAP YAŞI VE GÜVENİLİRLİK",
        value=(
            f"{progress_bar}  **{age_str}**\n"
            f"{age_bar_str}\n"
            f"**Sınıflandırma:** `{age_label}`\n"
            f"**Kayıt Tarihi:** {created_ts}"
        ),
        inline=False
    )

    # GRUP & DISCORD BİLGİSİ
    embed.add_field(
        name="🎖️  GRUP & DISCORD",
        value=(
            f"```\n"
            f"Grup Rütbesi  : {current_rank}\n"
            f"Discord Nick  : {kullanici.display_name}\n"
            f"Discord Tag   : {kullanici}\n"
            f"```"
        ),
        inline=True
    )

    # KONTROL YAPAN BLOK
    invoker_rank = get_member_rank_name(interaction.user)
    embed.add_field(
        name="🔎  SORGULAYAN YETKİLİ",
        value=(
            f"```\n"
            f"İsim    : {interaction.user.display_name}\n"
            f"Rütbe   : {invoker_rank}\n"
            f"```"
        ),
        inline=True
    )

    # Avatar
    if info["avatar_url"]:
        embed.set_thumbnail(url=info["avatar_url"])

    # Discord avatarı (küçük resim)
    embed.set_image(url=None)  # Gerekirse büyük görsel eklenebilir

    embed.set_footer(
        text=f"TTC Personik Kayıt Sistemi  ·  Roblox ID: {roblox_id}",
        icon_url=kullanici.display_avatar.url
    )

    # ── 5. GÖNDER ────────────────────────────────────────────────
    view = KontrolView(
        roblox_username=roblox_username,
        roblox_id=roblox_id,
        discord_user=kullanici
    )
    await interaction.followup.send(embed=embed, view=view)


# ── BUTONLU VIEW ──────────────────────────────────────────────────
class KontrolView(discord.ui.View):
    def __init__(self, roblox_username: str, roblox_id: int, discord_user: discord.Member):
        super().__init__(timeout=120)
        self.roblox_username = roblox_username
        self.roblox_id = roblox_id

        # Roblox profil linki butonu
        self.add_item(discord.ui.Button(
            label="Roblox Profili",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/users/{roblox_id}/profile"
        ))
        # Roblox gruba git butonu
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


# ==================== DISCORD BOT (on_ready ve diğer eventlar) ====================
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

    if discord_id in verified_users:
        await interaction.followup.send("⛔ Bu Discord hesabı zaten doğrulanmış!", ephemeral=True)
        return

    if roblox_kullanici_adi.lower() in verified_roblox:
        await interaction.followup.send(f"⛔ **{roblox_kullanici_adi}** zaten başkası tarafından doğrulanmış!", ephemeral=True)
        return

    if discord_id in pending_verifications:
        await interaction.followup.send("⏳ Zaten bekleyen bir doğrulaman var!", ephemeral=True)
        return

    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    pending_verifications[discord_id] = {
        "roblox_username": roblox_kullanici_adi,
        "discord_username": discord_username,
        "code": code
    }

    print(f"[Bot] Verify baslatildi: {discord_username} -> {roblox_kullanici_adi}")

    await interaction.followup.send(
        f"🔍 **{roblox_kullanici_adi}** için doğrulama bekleniyor... Roblox oyununa gir!",
        ephemeral=True
    )

    asyncio.create_task(wait_for_result(interaction, discord_id, roblox_kullanici_adi, code))

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

async def wait_for_result(interaction, discord_id, roblox_username, code):
    for _ in range(300):
        await asyncio.sleep(1)

        if code in verified_results:
            result_data = verified_results.pop(code)
            pending_verifications.pop(discord_id, None)
            result = result_data["result"]

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

                await interaction.followup.send(
                    f"✅ **{interaction.user.mention}** başarıyla doğrulandı! Roblox adı: **{roblox_username}**"
                )
            else:
                await interaction.followup.send(
                    f"❌ **{interaction.user.mention}** doğrulaması başarısız! Kullanıcı hayırı seçti."
                )
            return

    pending_verifications.pop(discord_id, None)
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
    print("🌐 Flask sunucusu port 5000'de başlatıldı!")
    bot.run(TOKEN)
