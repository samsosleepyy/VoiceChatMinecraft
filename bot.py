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
DATA_FILE = "server_data.json"
MOVE_COOLDOWN = 3.0

server_data = {}  
game_state = {}   
user_last_move = {}
testing_guilds = set()
DYNAMIC_RANGE = DEFAULT_RANGE
active_calls = {} # เก็บสถานะคู่สาย { "playerA": "playerB", "playerB": "playerA" }

# --- LOCAL STORAGE SYSTEM ---
def load_data():
    global server_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                server_data = {int(k): v for k, v in raw_data.items()}
                # แปลง User ID กลับเป็น int
                for gid in server_data:
                    if 'users' in server_data[gid]:
                        server_data[gid]['users'] = {int(uid): data for uid, data in server_data[gid]['users'].items()}
            print(f"✅ Loaded Data: {len(server_data)} Servers")
        except Exception as e:
            print(f"⚠️ Load Error: {e}")
            server_data = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(server_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ Save Error: {e}")

load_data()

def get_guild_data(guild_id):
    if guild_id not in server_data:
        server_data[guild_id] = {'whitelist': {}, 'config': {}, 'users': {}}
    return server_data[guild_id]

def update_whitelist(guild_id, name, active=True):
    data = get_guild_data(guild_id)
    data['whitelist'] = {'active': active, 'name': name}
    save_data()

def remove_whitelist(guild_id):
    if guild_id in server_data:
        if 'whitelist' in server_data[guild_id]:
            del server_data[guild_id]['whitelist']
            save_data()

def toggle_whitelist(guild_id):
    data = get_guild_data(guild_id)
    if data.get('whitelist'):
        data['whitelist']['active'] = not data['whitelist'].get('active', False)
        save_data()

def update_config(guild_id, category_id, start_channel_id, range_val):
    data = get_guild_data(guild_id)
    data['config'] = {'category_id': category_id, 'start_channel_id': start_channel_id, 'range': range_val}
    save_data()

# อัปเดตข้อมูลผู้ใช้ (Gamertag + IC Name)
def update_user(guild_id, user_id, gamertag, ic_name):
    data = get_guild_data(guild_id)
    if 'users' not in data: data['users'] = {}
    data['users'][user_id] = {'gamertag': gamertag, 'ic_name': ic_name}
    save_data()

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
        try: await self.tree.sync()
        except: pass

    async def handle_index(self, request):
        return web.Response(text=f"Bot Online")
    
    # ... (Dashboard Handlers คงเดิม ละไว้เพื่อความสั้น) ...
    async def handle_dashboard(self, request): return web.Response(text="Dashboard Active")
    async def handle_dash_add(self, request): return web.Response(text="OK")
    async def handle_dash_remove(self, request): return web.Response(text="OK")
    async def handle_dash_toggle(self, request): return web.Response(text="OK")
    async def check_pass(self, data): return True

    # --- 🟢 HANDLE COORDS API (UPDATED) 🟢 ---
    async def handle_coords(self, request):
        try:
            data = await request.json()
            global game_state
            global DYNAMIC_RANGE
            global active_calls

            user_list = []
            server_calls = [] # รายชื่อคนที่กำลังโทรหากัน (Connected)

            if isinstance(data, dict):
                user_list = data.get('users', [])
                received_range = data.get('range')
                if received_range: DYNAMIC_RANGE = int(received_range)
                server_calls = data.get('calls', []) # รับสถานะการโทรจาก Addon

            current = {}
            # สร้าง Map IC Name กลับไปให้ Addon
            # Format: { "Gamertag": "IC Name" }
            ic_map = {}
            
            # อัปเดตสายที่กำลังโทร
            active_calls.clear()
            for c in server_calls:
                # c = { "p1": "Gamertag1", "p2": "Gamertag2" }
                active_calls[c['p1']] = c['p2']
                active_calls[c['p2']] = c['p1']

            # ค้นหา IC Name จาก Database
            # วิธีนี้อาจจะช้าถ้ายูสเซอร์เยอะ แต่สำหรับเซิร์ฟย่อยโอเค
            # เราจะส่ง IC Map ของ "ทุกคนที่ลงทะเบียนไว้" ไปให้ Addon (เพื่อให้เลือกรายชื่อโทรได้)
            
            all_registered_users = {}
            for gid, gdata in server_data.items():
                for uid, udata in gdata.get('users', {}).items():
                    # udata = {'gamertag': 'xxx', 'ic_name': 'yyy'}
                    all_registered_users[udata['gamertag']] = udata['ic_name']

            for p in user_list: 
                current[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            game_state = current
            
            if not self.is_rate_limited:
                await process_voice_logic()
            
            # ส่ง IC Map กลับไปให้ Addon ใช้แสดงผล
            return web.json_response({'status': 'ok', 'ic_map': all_registered_users})
            
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# ... (Event on_guild_join คงเดิม) ...
@bot.event
async def on_guild_join(guild): pass # ละไว้

class LinkModal(ui.Modal, title='ลงทะเบียน Voice Chat'):
    xbox_name = ui.TextInput(label='Xbox Gamertag', placeholder='ชื่อในเกม Minecraft...', min_length=3, max_length=20)
    ic_name = ui.TextInput(label='ชื่อ IC (ภาษาไทย)', placeholder='ชื่อตัวละครในบทบาท...', min_length=1, max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gamertag = self.xbox_name.value.strip()
        ic = self.ic_name.value.strip()
        
        update_user(interaction.guild_id, interaction.user.id, gamertag, ic)
        
        data = get_guild_data(interaction.guild_id)
        cfg = data.get('config', {})
        msg = f"✅ ลงทะเบียนเรียบร้อย!\n🎮 Xbox: **{gamertag}**\n🎭 IC: **{ic}**"
        if 'start_channel_id' in cfg:
            chan = interaction.guild.get_channel(cfg['start_channel_id'])
            if chan: msg += f"\n👉 เข้าห้องรอที่: {chan.mention}"
        await interaction.followup.send(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="ลงทะเบียน / แก้ไข", style=discord.ButtonStyle.green, custom_id="mc_link", emoji="📝")
    async def link(self, i: discord.Interaction, b: ui.Button):
        await i.response.send_modal(LinkModal())

    @ui.button(label="เช็คสถานะ", style=discord.ButtonStyle.primary, custom_id="mc_status", emoji="📊")
    async def status(self, i: discord.Interaction, b: ui.Button):
        data = get_guild_data(i.guild_id)
        users = data.get('users', {})
        if i.user.id not in users:
            return await i.response.send_message("❌ คุณยังไม่ได้ลงทะเบียน", ephemeral=True)
        
        udata = users[i.user.id] # {'gamertag': '...', 'ic_name': '...'}
        gamertag = udata['gamertag']
        ic = udata['ic_name']
        is_online = gamertag in game_state
        
        embed = discord.Embed(title="📊 ข้อมูลผู้ใช้", color=0x3498db)
        embed.add_field(name="🎮 Gamertag", value=gamertag, inline=True)
        embed.add_field(name="🎭 IC Name", value=ic, inline=True)
        embed.add_field(name="สถานะ", value="🟢 Online" if is_online else "🔴 Offline", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

# ... (Commands setup, wl, range, test คงเดิม) ...
@bot.tree.command(name="setup")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel, role: discord.Role = None):
    # (Setup logic แบบเดิมแต่เปลี่ยน View เป็นตัวใหม่)
    await interaction.response.defer(ephemeral=True)
    update_whitelist(interaction.guild_id, interaction.guild.name)
    update_config(interaction.guild_id, category.id, start_channel.id, DEFAULT_RANGE)
    
    embed = discord.Embed(title="🎙️ Voice Chat System", description="กดปุ่มด้านล่างเพื่อลงทะเบียน IC Name", color=0x2ecc71)
    await interaction.channel.send(embed=embed, view=SetupView())
    await interaction.followup.send("✅ Setup Complete", ephemeral=True)

@bot.tree.command(name="test")
async def test_mode(interaction: discord.Interaction):
    # (Logic เดิม)
    pass

# --- 🟢 CORE LOGIC (PHONE & PROXIMITY) 🟢 ---
async def process_voice_logic():
    curr = time.time()
    # Cleanup logic...
    
    for guild_id, data in server_data.items():
        if not data.get('whitelist', {}).get('active'): continue
        cfg = data.get('config', {})
        if not cfg: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        cat = guild.get_channel(cfg.get('category_id'))
        start = guild.get_channel(cfg.get('start_channel_id'))
        if not cat or not start: continue
        
        config_range = cfg.get('range', DEFAULT_RANGE)
        active_range = DYNAMIC_RANGE if DYNAMIC_RANGE > 0 else config_range
        dist_sq = active_range ** 2
        
        users_map = data.get('users', {})
        
        # 1. รวบรวมคน & แยกคนโทรศัพท์
        online = []
        in_call_users = set() # เก็บ User ID คนที่โทรอยู่
        
        # Mapping Gamertag -> Member Object
        tag_to_member = {}

        for uid, udata in users_map.items():
            gamertag = udata['gamertag']
            mem = guild.get_member(uid)
            if not mem or not mem.voice: continue
            if mem.voice.channel.category_id != cat.id: continue
            
            tag_to_member[gamertag] = mem

            # เช็คว่าคนนี้อยู่ในสายไหม
            if gamertag in active_calls:
                in_call_users.add(uid)
                # Logic การโทรจะจัดการแยกต่างหาก
                continue
            
            if gamertag in game_state:
                p = game_state[gamertag]
                online.append((mem, p['x'], p['y'], p['z']))
            else:
                # กลับ Lobby
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: await mem.move_to(start)
                        except: pass

        # --- 📞 PHONE LOGIC (ย้ายคู่สายไปห้องส่วนตัว) ---
        processed_calls = set()
        taken_rooms = set()

        for tag1, tag2 in active_calls.items():
            if tag1 in processed_calls: continue
            
            m1 = tag_to_member.get(tag1)
            m2 = tag_to_member.get(tag2)
            
            if m1 and m2:
                # หาห้องว่างให้คู่นี้
                target_room = None
                
                # ถ้าทั้งคู่อยู่ห้องเดียวกันและห้องนั้นไม่มีคนอื่น -> ใช้ห้องเดิม
                if m1.voice.channel.id == m2.voice.channel.id:
                    # เช็คว่าห้องนี้มีคนอื่นปนไหม
                    if len(m1.voice.channel.members) == 2:
                        target_room = m1.voice.channel
                
                # ถ้ายังไม่มีห้อง หรือห้องไม่ส่วนตัว -> หาห้องใหม่
                if not target_room:
                    avail = [c for c in cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
                    for c in avail:
                        if len(c.members) == 0 and c.id not in taken_rooms:
                            target_room = c
                            break
                
                if target_room:
                    taken_rooms.add(target_room.id)
                    processed_calls.add(tag1)
                    processed_calls.add(tag2)
                    
                    for m in [m1, m2]:
                        if m.voice.channel.id != target_room.id:
                            try: await m.move_to(target_room)
                            except: pass
        
        # --- 🌐 PROXIMITY LOGIC (คนปกติ) ---
        # (เหมือนเดิมเป๊ะ แต่ข้ามคนใน in_call_users)
        
        if not online: continue

        # 2. จัดกลุ่ม
        groups = []
        processed = set()
        for i in range(len(online)):
            if i in processed: continue
            m1, x1, y1, z1 = online[i]
            grp = [m1]
            processed.add(i)
            for j in range(i+1, len(online)):
                if j in processed: continue
                m2, x2, y2, z2 = online[j]
                if ((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2) <= dist_sq:
                    grp.append(m2)
                    processed.add(j)
            groups.append(grp)
            
        groups.sort(key=len, reverse=True)
        avail = [c for c in cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
        
        for g in groups:
            # (Logic เดิมของคุณ: คนเยอะครองห้อง คนน้อยย้าย)
            room_counts = {}
            for m in g:
                if m.voice.channel:
                    c = m.voice.channel
                    room_counts[c] = room_counts.get(c, 0) + 1
            
            if not room_counts: majority_channel = start 
            else: majority_channel = max(room_counts, key=room_counts.get)

            need_new_room = (majority_channel.id == start.id) or (majority_channel.id in taken_rooms)
            target = None
            
            if need_new_room:
                for c in avail:
                    if len(c.members) == 0 and c.id not in taken_rooms:
                        target = c; break
                if not target:
                    for c in avail:
                        if c.id not in taken_rooms: target = c; break
            else:
                target = majority_channel

            if not target: continue
            if target.id != start.id: taken_rooms.add(target.id)
            
            for m in g:
                if m.voice.channel.id != target.id:
                    if curr - user_last_move.get(m.id, 0) < MOVE_COOLDOWN: continue
                    try: 
                        await m.move_to(target)
                        user_last_move[m.id] = curr
                        await asyncio.sleep(0.2)
                    except: pass
