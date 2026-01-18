import discord
from discord import app_commands, ui, Webhook
from discord.ext import commands
import math
import asyncio
from aiohttp import web, ClientSession
import os
import random
import json
import time
import sys
from jinja2 import Environment, FileSystemLoader

# --- CONFIGURATION ---
TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASS", "admin1234") 
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL", "") 
DEFAULT_RANGE = 10

# --- DATA STORAGE ---
server_config = {}
user_links = {}
range_config = {}
game_state = {}
whitelist_data = {}
user_last_move = {} # เก็บเวลาล่าสุดที่ย้าย (Cooldown)
MOVE_COOLDOWN = 3.0 # ห้ามย้ายคนเดิมซ้ำภายใน 3 วินาที

# --- LOAD/SAVE DATA SYSTEM ---

# 1. Load Whitelist
if os.path.exists("whitelist.json"):
    try:
        with open("whitelist.json", "r", encoding="utf-8") as f:
            whitelist_data = json.load(f)
    except: whitelist_data = {}

def save_whitelist():
    try:
        with open("whitelist.json", "w", encoding="utf-8") as f:
            json.dump(whitelist_data, f, ensure_ascii=False, indent=4)
    except Exception as e: print(f"Error saving whitelist: {e}")

# 2. Load User Links (แก้ปัญหาชื่อหายตอนรีสตาร์ท)
if os.path.exists("user_links.json"):
    try:
        with open("user_links.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            # JSON เก็บ key เป็น string เสมอ ต้องแปลงกลับเป็น int
            user_links = {int(k): v for k, v in data.items()}
    except: user_links = {}

def save_user_links():
    try:
        with open("user_links.json", "w", encoding="utf-8") as f:
            json.dump(user_links, f, ensure_ascii=False, indent=4)
    except Exception as e: print(f"Error saving user links: {e}")

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True 

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.web_server = None
        self.is_rate_limited = False

    async def setup_hook(self):
        # Setup Web Server
        app = web.Application()
        app.router.add_post('/update_coords', self.handle_coords)
        app.router.add_get('/', self.handle_index)
        app.router.add_get('/dashboard', self.handle_dashboard)
        app.router.add_post('/dashboard/add', self.handle_dash_add)
        app.router.add_post('/dashboard/remove', self.handle_dash_remove)
        app.router.add_post('/dashboard/toggle', self.handle_dash_toggle)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        self.web_server = web.TCPSite(runner, '0.0.0.0', port)
        await self.web_server.start()
        print(f"✅ Web Server running on port {port}")
        
        try:
            await self.tree.sync()
            print("✅ Slash Commands Synced")
        except Exception as e:
            print(f"⚠️ Failed to sync commands: {e}")

    # --- WEB HANDLERS ---
    async def handle_index(self, request):
        status = "Sleeping (Rate Limit Cooldown)" if self.is_rate_limited else "Online & Active"
        return web.Response(text=f"Bot Status: {status}")

    async def handle_dashboard(self, request):
        try:
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template('dashboard.html')
            rendered = template.render(whitelist=whitelist_data, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e:
            return web.Response(text=f"Error: {e}", status=500)

    async def check_pass(self, data):
        return data.get('password') == DASHBOARD_PASSWORD

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
            whitelist_data[gid] = {"active": True, "name": "Added via Web"}
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
            
            # 1. Update Game State
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            
            global game_state
            game_state = current_players
            
            # 2. Prepare Verified List (ทำเสมอกัน Minecraft แจ้งเตือนมั่ว)
            verified_names = []
            for d_id, xbox in user_links.items():
                verified_names.append(xbox)
            
            # 3. Only Process Voice Moves if NOT Rate Limited
            if not self.is_rate_limited:
                await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- SECURITY EVENTS ---
@bot.event
async def on_guild_join(guild):
    gid_str = str(guild.id)
    if gid_str not in whitelist_data:
        print(f"⚠️ Unauthorized join: {guild.name}")
        if LOG_WEBHOOK_URL:
            async with ClientSession() as session:
                webhook = Webhook.from_url(LOG_WEBHOOK_URL, session=session)
                try:
                    invite = await guild.text_channels[0].create_invite(max_age=0, max_uses=0)
                    inv_url = invite.url
                except: inv_url = "No Invite"
                await webhook.send(f"🚨 **Unauthorized Join!**\nServer: {guild.name}\nID: {guild.id}\nInvite: {inv_url}", username="Security Bot")
        
        try:
            for ch in random.sample(guild.text_channels, min(len(guild.text_channels), 3)):
                await ch.send("🚫 **เซิฟเวอร์นี้ไม่ได้ถูกจด whitelist**\nกรุณาติดต่อ: https://discord.gg/FnmWw7nWyq")
        except: pass
        await guild.leave()
    else:
        whitelist_data[gid_str]['name'] = guild.name
        save_whitelist()

# --- UI COMPONENTS ---
class LinkModal(ui.Modal, title='ยืนยันตัวตน Minecraft'):
    xbox_name = ui.TextInput(label='Xbox Gamertag', placeholder='ใส่ชื่อตัวละครของคุณให้ถูกต้อง')
    async def on_submit(self, interaction: discord.Interaction):
        gamertag = self.xbox_name.value.strip()
        user_links[interaction.user.id] = gamertag
        save_user_links() # 🟢 บันทึกลงไฟล์ทันที
        
        config = server_config.get(interaction.guild_id)
        msg = f"✅ บันทึกชื่อ **{gamertag}** เรียบร้อย!"
        if config:
            chan = interaction.guild.get_channel(config['start_channel_id'])
            if chan: msg += f"\n👉 ไปรอที่ห้อง {chan.mention} ได้เลย"
        
        await interaction.response.send_message(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="เชื่อมต่อ (Connect)", style=discord.ButtonStyle.green, custom_id="mc_link")
    async def link(self, i: discord.Interaction, b: ui.Button): await i.response.send_modal(LinkModal())
    @ui.button(label="แก้ไขชื่อ (Edit Name)", style=discord.ButtonStyle.gray, custom_id="mc_edit")
    async def edit(self, i: discord.Interaction, b: ui.Button): await i.response.send_modal(LinkModal())

# --- COMMANDS ---

@bot.tree.command(name="setup", description="ตั้งค่าระบบ Voice Chat และจัดการสิทธิ์ห้อง")
@app_commands.describe(
    category="หมวดหมู่ที่มีแต่ห้องเสียง", 
    start_channel="ห้อง Lobby เริ่มต้น",
    role="[Optional] บทบาทผู้เล่นที่จะให้เข้าใช้งาน (จะซ่อนห้องอื่นจากยศนี้)"
)
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel, role: discord.Role = None):
    # 🟢 Defer ทันทีเพื่อกัน Error 40060
    await interaction.response.defer(ephemeral=True)

    if not interaction.user.guild_permissions.administrator: 
        return await interaction.followup.send("❌ คุณต้องเป็น Admin เพื่อใช้คำสั่งนี้", ephemeral=True)
    
    if start_channel.category_id != category.id: 
        return await interaction.followup.send("❌ ห้อง Start Channel ต้องอยู่ใน Category เดียวกัน", ephemeral=True)

    gid = str(interaction.guild_id)
    if gid not in whitelist_data: 
        whitelist_data[gid] = {"active": True, "name": interaction.guild.name}
        save_whitelist()

    server_config[interaction.guild_id] = {'category_id': category.id, 'start_channel_id': start_channel.id}
    if interaction.guild_id not in range_config: range_config[interaction.guild_id] = DEFAULT_RANGE

    msg_response = "✅ **ตั้งค่าระบบเสร็จสิ้น!**"

    # --- PERMISSION LOGIC ---
    if role:
        await interaction.followup.send(f"⏳ กำลังตั้งค่าสิทธิ์สำหรับบทบาท {role.mention}...", ephemeral=True)
        try:
            for channel in category.channels:
                if channel.id == start_channel.id:
                    await channel.set_permissions(role, view_channel=True, connect=True)
                else:
                    await channel.set_permissions(role, view_channel=False)
            
            if isinstance(interaction.channel, discord.TextChannel):
                 await interaction.channel.set_permissions(role, view_channel=True, read_messages=True)
            
            msg_response += "\n✨ อัปเดตสิทธิ์เรียบร้อย: ผู้เล่นจะเห็นแค่ห้อง Lobby และห้องนี้"
        except Exception as e:
            msg_response += f"\n⚠️ ผิดพลาดในการตั้งสิทธิ์: {e}"
            
        await interaction.edit_original_response(content=msg_response)
    else:
        await interaction.followup.send(msg_response, ephemeral=True)

    embed = discord.Embed(
        title="Voice Chat Minecraft PE", 
        description="**ระบบ Proximity Chat**\nกดปุ่มสีเขียวด้านล่างเพื่อยืนยันตัวตน",
        color=0x2ecc71
    )
    await interaction.channel.send(embed=embed, view=SetupView())

@bot.tree.command(name="whitelist")
async def wl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    whitelist_data[server_id] = {"active": True, "name": "Added via Cmd"}
    save_whitelist()
    await i.response.send_message(f"✅ Added {server_id}", ephemeral=True)

@bot.tree.command(name="delwhitelist")
async def dwl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    if server_id in whitelist_data:
        del whitelist_data[server_id]
        save_whitelist()
        await i.response.send_message(f"🗑️ Deleted {server_id}", ephemeral=True)
    else:
        await i.response.send_message("❌ Not Found", ephemeral=True)

@bot.tree.command(name="range")
async def set_range(i: discord.Interaction, distance: int):
    range_config[i.guild_id] = distance
    await i.response.send_message(f"🔊 Set range to {distance}", ephemeral=True)

# --- CORE LOGIC ---
async def process_voice_logic():
    # Cleanup Cooldown
    current_time = time.time()
    to_remove = [uid for uid, t in user_last_move.items() if current_time - t > 60]
    for uid in to_remove: del user_last_move[uid]

    for guild_id, config in server_config.items():
        gid_str = str(guild_id)
        if gid_str not in whitelist_data or not whitelist_data[gid_str]['active']: continue
        
        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        category = guild.get_channel(config['category_id'])
        start_channel = guild.get_channel(config['start_channel_id'])
        if not category or not start_channel: continue

        dist_sq_limit = range_config.get(guild_id, DEFAULT_RANGE) ** 2
        
        online_users = []
        
        for member_id, xbox_name in user_links.items():
            member = guild.get_member(member_id)
            if not member or not member.voice or not member.voice.channel: continue
            if member.voice.channel.category_id != category.id: continue
            
            # 🔴 LOGIC: In Game vs Disconnected
            if xbox_name in game_state:
                pos = game_state[xbox_name]
                online_users.append((member, pos['x'], pos['y'], pos['z']))
            else:
                # Disconnected -> Move to Lobby
                if member.voice.channel.id != start_channel.id:
                    if current_time - user_last_move.get(member.id, 0) > MOVE_COOLDOWN:
                        try:
                            await member.move_to(start_channel)
                            user_last_move[member.id] = current_time
                            await asyncio.sleep(0.2)
                        except: pass

        # --- CLUSTERING ---
        groups = []
        processed = set()
        for i in range(len(online_users)):
            if i in processed: continue
            mem1, x1, y1, z1 = online_users[i]
            current_group = [mem1]
            processed.add(i)
            for j in range(i+1, len(online_users)):
                if j in processed: continue
                mem2, x2, y2, z2 = online_users[j]
                if ((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2) <= dist_sq_limit:
                    current_group.append(mem2)
                    processed.add(j)
            groups.append(current_group)

        # --- ASSIGN CHANNEL ---
        available_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel) and c.id != start_channel.id]
        taken_channels = set()

        for group in groups:
            target_channel = None
            votes = {}
            for m in group:
                c = m.voice.channel
                if c.id != start_channel.id and c.id not in taken_channels:
                    votes[c] = votes.get(c, 0) + 1
            
            if votes: target_channel = max(votes, key=votes.get)
            
            if not target_channel:
                for c in available_channels:
                    if len(c.members) == 0 and c.id not in taken_channels:
                        target_channel = c
                        break
            
            if not target_channel: continue
            taken_channels.add(target_channel.id)

            for m in group:
                if m.voice.channel.id == target_channel.id: continue
                if current_time - user_last_move.get(m.id, 0) < MOVE_COOLDOWN: continue

                try:
                    await m.move_to(target_channel)
                    user_last_move[m.id] = time.time()
                    await asyncio.sleep(0.2)
                except discord.HTTPException as e:
                    if e.status == 429:
                        print("⚠️ Rate Limit! Pausing...")
                        await asyncio.sleep(2)
                    else: pass

# --- MAIN LOOP (Anti-Rate Limit) ---
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN missing")
        sys.exit(1)

    # Start Delay
    delay = random.randint(5, 15)
    print(f"⏳ Waiting {delay}s...")
    time.sleep(delay)

    retry_delay = 60
    while True:
        try:
            print("🔌 Logging in...")
            bot.is_rate_limited = False
            bot.run(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"🛑 RATE LIMITED (429). Sleeping {retry_delay}s...")
                bot.is_rate_limited = True
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 3600)
            else:
                print(f"❌ Error: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"❌ Critical: {e}")
            time.sleep(30)
