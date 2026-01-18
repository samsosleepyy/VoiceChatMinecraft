import discord
from discord import app_commands, ui, Webhook
from discord.ext import commands
import math
import asyncio
from aiohttp import web, ClientSession
import os
import random
import json

# --- CONFIGURATION ---
TOKEN = os.getenv("DISCORD_TOKEN")
# รหัสผ่านสำหรับเข้าหน้าเว็บ Dashboard
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASS", "admin1234") 
# Webhook ของคุณเอง เอาไว้รับ Link Invite เวลาบอทหลุดเข้าไปในเซิร์ฟเถื่อน
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL", "") 

DEFAULT_RANGE = 10

# --- DATA STORAGE ---
# เก็บ Config: {guild_id: {'category_id': int, 'start_channel_id': int}}
server_config = {}
# เก็บ Link: {user_id: gamertag}
user_links = {}
# เก็บ Range: {guild_id: int}
range_config = {}
# เก็บ Game State
game_state = {}

# --- WHITELIST SYSTEM ---
# format: {str(guild_id): {"active": bool, "name": str}}
whitelist_data = {}

# โหลด Whitelist จากไฟล์ (ถ้ามี) *บน Render ข้อมูลนี้จะหายถ้ารีสตาร์ทถ้าไม่ใช้ Database*
if os.path.exists("whitelist.json"):
    try:
        with open("whitelist.json", "r", encoding="utf-8") as f:
            whitelist_data = json.load(f)
    except:
        whitelist_data = {}

def save_whitelist():
    with open("whitelist.json", "w", encoding="utf-8") as f:
        json.dump(whitelist_data, f, ensure_ascii=False, indent=4)

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True # ต้องเปิด Message Content Intent ใน Developer Portal ด้วย

# --- HTML TEMPLATE (DASHBOARD) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Bot Control Panel</title>
    <style>
        body { font-family: sans-serif; background: #1e1e2e; color: #fff; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .card { background: #313244; padding: 15px; margin-bottom: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; }
        .status-on { color: #a6e3a1; font-weight: bold; }
        .status-off { color: #f38ba8; font-weight: bold; }
        button { cursor: pointer; padding: 8px 15px; border: none; border-radius: 4px; font-weight: bold; }
        .btn-toggle { background: #89b4fa; color: #1e1e2e; }
        .btn-del { background: #f38ba8; color: #1e1e2e; }
        .btn-add { background: #a6e3a1; color: #1e1e2e; width: 100%; margin-top: 10px; }
        input { padding: 8px; border-radius: 4px; border: none; width: 70%; }
        h2 { border-bottom: 2px solid #45475a; padding-bottom: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎛️ Minecraft Voice Bot Dashboard</h1>
        
        <h2>➕ Add Whitelist</h2>
        <form action="/dashboard/add" method="post" style="background:#313244; padding:15px; border-radius:8px;">
            <input type="text" name="guild_id" placeholder="Server ID" required>
            <input type="password" name="password" placeholder="Admin Password" required>
            <button type="submit" class="btn-add">Add Server</button>
        </form>

        <h2>📋 Whitelisted Servers</h2>
        {% for gid, data in whitelist.items() %}
        <div class="card">
            <div>
                <strong>{{ data.name }}</strong> (ID: {{ gid }})<br>
                Status: <span class="{{ 'status-on' if data.active else 'status-off' }}">{{ 'WORKING' if data.active else 'STOPPED' }}</span>
            </div>
            <div>
                <form action="/dashboard/toggle" method="post" style="display:inline;">
                    <input type="hidden" name="guild_id" value="{{ gid }}">
                    <input type="hidden" name="password" value="{{ password }}">
                    <button type="submit" class="btn-toggle">{{ 'Turn OFF' if data.active else 'Turn ON' }}</button>
                </form>
                <form action="/dashboard/remove" method="post" style="display:inline;">
                    <input type="hidden" name="guild_id" value="{{ gid }}">
                    <input type="hidden" name="password" value="{{ password }}">
                    <button type="submit" class="btn-del">Delete</button>
                </form>
            </div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.web_server = None

    async def setup_hook(self):
        app = web.Application()
        app.router.add_post('/update_coords', self.handle_coords)
        app.router.add_get('/', self.handle_index)
        
        # Dashboard Routes
        app.router.add_get('/dashboard', self.handle_dashboard)
        app.router.add_post('/dashboard/add', self.handle_dash_add)
        app.router.add_post('/dashboard/remove', self.handle_dash_remove)
        app.router.add_post('/dashboard/toggle', self.handle_dash_toggle)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        self.web_server = web.TCPSite(runner, '0.0.0.0', port)
        await self.web_server.start()
        print(f"✅ Bot started on port {port}")
        await self.tree.sync()

    # --- WEB HANDLERS ---
    async def handle_index(self, request):
        return web.Response(text="Bot is running. Go to /dashboard to manage.")

    async def handle_dashboard(self, request):
        # Render HTML แบบบ้านๆ
        from jinja2 import Template
        template = Template(HTML_TEMPLATE)
        # รับ password จาก query param เล็กน้อยเพื่อความง่าย (เช่น ?pass=admin1234)
        # แต่ใน Form ใช้ post param
        rendered = template.render(whitelist=whitelist_data, password=DASHBOARD_PASSWORD)
        return web.Response(text=rendered, content_type='text/html')

    async def check_pass(self, data):
        if data.get('password') != DASHBOARD_PASSWORD:
            return False
        return True

    async def handle_dash_toggle(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        
        gid = data.get('guild_id')
        if gid in whitelist_data:
            whitelist_data[gid]['active'] = not whitelist_data[gid]['active']
            save_whitelist()
        return web.HTTPFound('/dashboard')

    async def handle_dash_add(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        
        gid = data.get('guild_id')
        if gid:
            # พยายามหาชื่อเซิร์ฟเวอร์
            g_obj = bot.get_guild(int(gid))
            name = g_obj.name if g_obj else "Unknown Server"
            whitelist_data[gid] = {"active": True, "name": name}
            save_whitelist()
        return web.HTTPFound('/dashboard')

    async def handle_dash_remove(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        
        gid = data.get('guild_id')
        if gid in whitelist_data:
            del whitelist_data[gid]
            save_whitelist()
        return web.HTTPFound('/dashboard')

    async def handle_coords(self, request):
        try:
            data = await request.json()
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            global game_state
            game_state = current_players
            verified_names = []
            for d_id, xbox in user_links.items():
                if xbox in game_state: verified_names.append(xbox)
            
            await process_voice_logic()
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- SECURITY EVENTS ---
@bot.event
async def on_guild_join(guild):
    # ตรวจสอบว่า Server นี้อยู่ใน Whitelist หรือไม่
    gid_str = str(guild.id)
    
    if gid_str not in whitelist_data:
        # ❌ ไม่ผ่าน Whitelist
        print(f"⚠️ Unauthorized join: {guild.name} ({guild.id})")
        
        # 1. สร้าง Invite Link (ไม่หมดอายุ)
        invite_url = "Cannot Create Invite"
        try:
            # หาช่องข้อความสักช่องเพื่อสร้าง invite
            if guild.text_channels:
                invite = await guild.text_channels[0].create_invite(max_age=0, max_uses=0)
                invite_url = invite.url
        except:
            pass

        # 2. แจ้งเตือนเจ้าของผ่าน Webhook
        if LOG_WEBHOOK_URL:
            async with ClientSession() as session:
                webhook = Webhook.from_url(LOG_WEBHOOK_URL, session=session)
                await webhook.send(f"🚨 **Bot joined unauthorized server!**\nName: {guild.name}\nID: {guild.id}\nInvite: {invite_url}", username="Security Bot")

        # 3. Spam ข้อความเตือน 3 ช่อง (แบบสุ่ม)
        channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
        target_channels = random.sample(channels, min(len(channels), 3))
        
        warning_msg = "🚫 **เซิฟเวอร์นี้ไม่ได้ถูกจด Whitelist**\nบอทจะออกจากเซิร์ฟเวอร์อัตโนมัติ\nกรุณาติดต่อขออนุญาตที่: https://discord.gg/FnmWw7nWyq"
        
        for channel in target_channels:
            try:
                await channel.send(warning_msg)
            except:
                pass
        
        # 4. ออกจากเซิร์ฟเวอร์
        await guild.leave()
    
    else:
        # ✅ ผ่าน Whitelist -> อัปเดตชื่อเซิร์ฟเวอร์ใน DB
        whitelist_data[gid_str]['name'] = guild.name
        save_whitelist()

# --- COMMANDS ---

@bot.tree.command(name="whitelist", description="[Admin] เพิ่มเซิร์ฟเวอร์ลง whitelist")
async def add_whitelist(interaction: discord.Interaction, server_id: str):
    # เช็คว่าเป็นเจ้าของบอทไหม (แก้ ID ตรงนี้เป็น ID ของคุณ)
    # หรือใช้ interaction.user.id == YOUR_ID
    if not interaction.user.guild_permissions.administrator: 
        return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)

    whitelist_data[server_id] = {"active": True, "name": "Pre-Added Server"}
    save_whitelist()
    await interaction.response.send_message(f"✅ เพิ่ม ID {server_id} ลง Whitelist แล้ว", ephemeral=True)

@bot.tree.command(name="delwhitelist", description="[Admin] ลบเซิร์ฟเวอร์จาก whitelist")
async def remove_whitelist(interaction: discord.Interaction, server_id: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)

    if server_id in whitelist_data:
        del whitelist_data[server_id]
        save_whitelist()
        await interaction.response.send_message(f"🗑️ ลบ ID {server_id} เรียบร้อย", ephemeral=True)
    else:
        await interaction.response.send_message("❌ ไม่พบ ID นี้ในระบบ", ephemeral=True)

# ... (ส่วน Setup, LinkModal, SetupView คงเดิม) ...
class LinkModal(ui.Modal, title='เชื่อมต่อ ID Minecraft'):
    xbox_name = ui.TextInput(label='ชื่อ Xbox Gamertag', placeholder='ใส่ชื่อตัวละครให้ตรงเป๊ะๆ')
    async def on_submit(self, interaction: discord.Interaction):
        gamertag = self.xbox_name.value.strip()
        user_links[interaction.user.id] = gamertag
        config = server_config.get(interaction.guild_id)
        msg = f"✅ ยืนยันเรียบร้อย! คุณคือ **{gamertag}**"
        if config:
            chan = interaction.guild.get_channel(config['start_channel_id'])
            if chan: msg += f"\n👉 ไปรอที่ห้อง {chan.mention}"
        await interaction.response.send_message(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="เชื่อมต่อ (Connect)", style=discord.ButtonStyle.green, custom_id="mc_link_btn")
    async def link_button(self, interaction: discord.Interaction, button: ui.Button): await interaction.response.send_modal(LinkModal())
    @ui.button(label="แก้ไขชื่อ (Edit Name)", style=discord.ButtonStyle.gray, custom_id="mc_edit_btn")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button): await interaction.response.send_modal(LinkModal())

@bot.tree.command(name="setup", description="ตั้งค่าระบบ")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("❌ Admin only", ephemeral=True)
    if start_channel.category_id != category.id: return await interaction.response.send_message("❌ Channel ไม่อยู่ใน Category", ephemeral=True)
    server_config[interaction.guild_id] = {'category_id': category.id, 'start_channel_id': start_channel.id}
    if interaction.guild_id not in range_config: range_config[interaction.guild_id] = DEFAULT_RANGE
    
    # บันทึกชื่อเซิร์ฟลง Whitelist (ถ้ามีอยู่แล้ว)
    gid = str(interaction.guild_id)
    if gid in whitelist_data:
        whitelist_data[gid]['name'] = interaction.guild.name
        save_whitelist()

    await interaction.channel.send(embed=discord.Embed(title="Minecraft Voice System", description="กดปุ่มเพื่อเชื่อมต่อ", color=0x00ff00), view=SetupView())
    await interaction.response.send_message("✅ Setup Done", ephemeral=True)

@bot.tree.command(name="range", description="Set Distance")
async def set_rage(interaction: discord.Interaction, distance: int):
    range_config[interaction.guild_id] = distance
    await interaction.response.send_message(f"✅ Set range to {distance}", ephemeral=True)

# --- CORE LOGIC ---
async def process_voice_logic():
    for guild_id, config in server_config.items():
        gid_str = str(guild_id)
        
        # 🔴 CHECK WHITELIST & ACTIVE STATUS 🔴
        # ถ้าไม่อยู่ใน whitelist หรือ สถานะ Active = False ให้ข้ามการทำงาน
        if gid_str not in whitelist_data: continue
        if not whitelist_data[gid_str]['active']: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        category = guild.get_channel(config['category_id'])
        start_channel = guild.get_channel(config['start_channel_id'])
        if not category or not start_channel: continue

        dist_limit = range_config.get(guild_id, DEFAULT_RANGE)
        
        # ... (Logic เดิม: Few to Many) ...
        online_users = [] 
        for member_id, xbox_name in user_links.items():
            member = guild.get_member(member_id)
            if not member or not member.voice or not member.voice.channel: continue
            if member.voice.channel.category_id == category.id:
                if xbox_name in game_state:
                    pos = game_state[xbox_name]
                    online_users.append((member, pos['x'], pos['y'], pos['z']))
                else:
                    if member.voice.channel.id != start_channel.id:
                        try: await member.move_to(start_channel)
                        except: pass

        groups = []
        processed_indices = set()
        for i in range(len(online_users)):
            if i in processed_indices: continue
            mem1, x1, y1, z1 = online_users[i]
            current_group = [mem1]
            processed_indices.add(i)
            for j in range(i + 1, len(online_users)):
                if j in processed_indices: continue
                mem2, x2, y2, z2 = online_users[j]
                d = math.sqrt((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2)
                if d <= dist_limit:
                    current_group.append(mem2)
                    processed_indices.add(j)
            groups.append(current_group)

        available_game_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel) and c.id != start_channel.id]
        taken_channels = set()

        for group in groups:
            target_channel = None
            channel_votes = {}
            for member in group:
                curr_chan = member.voice.channel
                if curr_chan.id != start_channel.id and curr_chan.id not in taken_channels:
                    channel_votes[curr_chan] = channel_votes.get(curr_chan, 0) + 1
            if channel_votes: target_channel = max(channel_votes, key=channel_votes.get)
            if not target_channel:
                for chan in available_game_channels:
                    if len(chan.members) == 0 and chan.id not in taken_channels:
                        target_channel = chan
                        break
            if not target_channel: continue
            taken_channels.add(target_channel.id)
            for member in group:
                if member.voice.channel.id != target_channel.id:
                    try: 
                        await member.move_to(target_channel)
                        await asyncio.sleep(0.1)
                    except: pass

if __name__ == "__main__":
    if TOKEN: bot.run(TOKEN)
