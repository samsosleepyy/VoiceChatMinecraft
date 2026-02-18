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
active_calls = {} # เก็บสถานะคู่สาย { "gamertag1": "gamertag2" }

# --- LOCAL STORAGE SYSTEM (ROBUST VERSION) ---
def load_data():
    global server_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                # แปลง Key กลับเป็น int (Guild ID)
                temp_data = {int(k): v for k, v in raw_data.items()}
                
                # --- 🛡️ DATA MIGRATION FIX (กันบอทดับ) ---
                for gid, gdata in temp_data.items():
                    if 'users' in gdata:
                        new_users = {}
                        for uid, udata in gdata['users'].items():
                            # ถ้าเป็นข้อมูลเก่า (String) -> แปลงเป็น Dict
                            if isinstance(udata, str):
                                new_users[int(uid)] = {'gamertag': udata, 'ic_name': udata}
                            # ถ้าเป็นข้อมูลใหม่ (Dict) -> ใช้ได้เลย
                            elif isinstance(udata, dict):
                                new_users[int(uid)] = udata
                        
                        gdata['users'] = new_users # อัปเดตกลับเข้าไป
                
                server_data = temp_data
            print(f"✅ Loaded Data: {len(server_data)} Servers")
        except Exception as e:
            print(f"⚠️ Load Error (Data Reset): {e}")
            server_data = {} # ถ้าไฟล์เสียจริงๆ ให้รีเซ็ตใหม่เพื่อกันดับ

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(server_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ Save Error: {e}")

load_data() # โหลดข้อมูลทันทีเมื่อเริ่ม

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

    async def handle_dashboard(self, request):
        try:
            whitelist_flat = {str(gid): d.get('whitelist') for gid, d in server_data.items() if d.get('whitelist')}
            env = Environment(loader=FileSystemLoader('templates'))
            # เช็คว่ามีโฟลเดอร์ templates ไหม (กันแครช)
            if not os.path.exists('templates/dashboard.html'):
                 return web.Response(text="Template not found", status=404)
            template = env.get_template('dashboard.html')
            rendered = template.render(whitelist=whitelist_flat, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e: return web.Response(text=str(e), status=500)

    async def check_pass(self, data): return data.get('password') == DASHBOARD_PASSWORD
    async def handle_dash_toggle(self, request): return web.HTTPFound('/dashboard')
    async def handle_dash_add(self, request): return web.HTTPFound('/dashboard')
    async def handle_dash_remove(self, request): return web.HTTPFound('/dashboard')

    # --- 🟢 HANDLE COORDS API 🟢 ---
    async def handle_coords(self, request):
        try:
            data = await request.json()
            global game_state
            global DYNAMIC_RANGE
            global active_calls

            user_list = []
            server_calls = [] 

            # รับข้อมูลแบบยืดหยุ่น (กัน Error)
            if isinstance(data, dict):
                user_list = data.get('users', [])
                received_range = data.get('range')
                if received_range: DYNAMIC_RANGE = int(received_range)
                server_calls = data.get('calls', []) 
            elif isinstance(data, list): # เผื่อ Addon เก่าส่งมา
                user_list = data

            # อัปเดต Game State
            current = {}
            for p in user_list: 
                current[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            game_state = current
            
            # อัปเดต Active Calls (รับมาจาก Addon ว่าใครคุยกับใคร)
            active_calls.clear()
            for c in server_calls:
                # c = { "p1": "Gamertag1", "p2": "Gamertag2" }
                p1, p2 = c.get('p1'), c.get('p2')
                if p1 and p2:
                    active_calls[p1] = p2
                    active_calls[p2] = p1

            # เตรียม Map IC Name ส่งกลับไปให้ Addon
            all_ic_map = {}
            for gid, gdata in server_data.items():
                for uid, udata in gdata.get('users', {}).items():
                    # เช็คอีกทีว่าเป็น Dict ไหม (เพื่อความชัวร์)
                    if isinstance(udata, dict):
                        all_ic_map[udata['gamertag']] = udata['ic_name']
                    elif isinstance(udata, str): # Fallback
                        all_ic_map[udata] = udata

            if not self.is_rate_limited:
                await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'ic_map': all_ic_map})
            
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

@bot.event
async def on_guild_join(guild):
    data = get_guild_data(guild.id)
    if not data.get('whitelist'):
        try: await guild.leave()
        except: pass
    else:
        update_whitelist(guild.id, guild.name, data['whitelist'].get('active', True))

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
        
        udata = users[i.user.id]
        # รองรับข้อมูลเก่า (String)
        gamertag = udata if isinstance(udata, str) else udata['gamertag']
        ic = udata if isinstance(udata, str) else udata['ic_name']
        is_online = gamertag in game_state
        
        embed = discord.Embed(title="📊 ข้อมูลผู้ใช้", color=0x3498db)
        embed.add_field(name="🎮 Gamertag", value=gamertag, inline=True)
        embed.add_field(name="🎭 IC Name", value=ic, inline=True)
        embed.add_field(name="สถานะ", value="🟢 Online" if is_online else "🔴 Offline", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="setup")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel, role: discord.Role = None):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.administrator: return await interaction.followup.send("❌ Admin Only", ephemeral=True)
    
    update_whitelist(interaction.guild_id, interaction.guild.name)
    update_config(interaction.guild_id, category.id, start_channel.id, DEFAULT_RANGE)
    
    embed = discord.Embed(title="🎙️ Voice Chat System", description="กดปุ่มด้านล่างเพื่อลงทะเบียน IC Name", color=0x2ecc71)
    await interaction.channel.send(embed=embed, view=SetupView())
    await interaction.followup.send("✅ Setup Complete", ephemeral=True)

@bot.tree.command(name="whitelist")
async def wl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    update_whitelist(int(server_id), "Added via Cmd")
    await i.response.send_message(f"✅ Whitelisted {server_id}", ephemeral=True)

@bot.tree.command(name="range")
async def set_range(i: discord.Interaction, distance: int):
    data = get_guild_data(i.guild_id)
    cfg = data.get('config', {})
    if 'category_id' in cfg:
        update_config(i.guild_id, cfg['category_id'], cfg['start_channel_id'], distance)
        await i.response.send_message(f"🔊 Range set to {distance}", ephemeral=True)
    else:
        await i.response.send_message("❌ Please run /setup first", ephemeral=True)

@bot.tree.command(name="test")
async def test_mode(interaction: discord.Interaction):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.administrator: return await interaction.followup.send("❌ Admin Only", ephemeral=True)
    gid = interaction.guild_id
    if gid in testing_guilds:
        testing_guilds.remove(gid)
        if interaction.guild.voice_client: await interaction.guild.voice_client.disconnect()
        await interaction.followup.send("🛑 **Test Mode: OFF**", ephemeral=True)
    else:
        testing_guilds.add(gid)
        await interaction.followup.send("🧪 **Test Mode: ON**", ephemeral=True)

# --- 🟢 CORE LOGIC 🟢 ---
async def process_voice_logic():
    curr = time.time()
    for u in [k for k,v in user_last_move.items() if curr-v > 60]: del user_last_move[u]

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
        
        online = []
        in_call_users = set()
        tag_to_member = {}

        # 1. รวบรวมสมาชิก
        for uid, udata in users_map.items():
            # รองรับข้อมูลเก่า/ใหม่
            gamertag = udata if isinstance(udata, str) else udata['gamertag']
            
            mem = guild.get_member(uid)
            if not mem or not mem.voice: continue
            if mem.voice.channel.category_id != cat.id: continue
            
            tag_to_member[gamertag] = mem

            # เช็คว่าอยู่ในสายโทรศัพท์ไหม?
            if gamertag in active_calls:
                in_call_users.add(uid)
                continue # แยกไปจัดการใน Phone Logic
            
            if gamertag in game_state:
                p = game_state[gamertag]
                online.append((mem, p['x'], p['y'], p['z']))
            else:
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: await mem.move_to(start)
                        except: pass

        # --- 📞 PHONE LOGIC ---
        processed_calls = set()
        taken_rooms = set()

        for tag1, tag2 in active_calls.items():
            if tag1 in processed_calls: continue
            
            m1 = tag_to_member.get(tag1)
            m2 = tag_to_member.get(tag2)
            
            if m1 and m2:
                target_room = None
                # ถ้าอยู่ด้วยกันแล้ว 2 คน
                if m1.voice.channel.id == m2.voice.channel.id:
                    if len(m1.voice.channel.members) == 2:
                        target_room = m1.voice.channel
                
                # ถ้ายังไม่ได้ที่ -> หาห้องว่าง
                if not target_room:
                    avail = [c for c in cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
                    for c in avail:
                        if len(c.members) == 0 and c.id not in taken_rooms:
                            target_room = c; break
                
                if target_room:
                    taken_rooms.add(target_room.id)
                    processed_calls.add(tag1)
                    processed_calls.add(tag2)
                    for m in [m1, m2]:
                        if m.voice.channel.id != target_room.id:
                            try: await m.move_to(target_room)
                            except: pass

        # --- 🌐 PROXIMITY LOGIC (NORMAL) ---
        if not online: continue

        # Grouping
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
            # Majority Rule
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

if __name__ == "__main__":
    if not TOKEN: sys.exit(1)
    time.sleep(random.randint(5, 10))
    while True:
        try:
            bot.is_rate_limited = False
            bot.run(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                bot.is_rate_limited = True
                time.sleep(60)
            else: time.sleep(10)
        except: time.sleep(30)
