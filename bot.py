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
MOVE_COOLDOWN = 3.0
DATA_FILE = "server_data.json"

# --- DATA STORES ---
server_data = {}  
game_state = {}   
user_last_move = {}

# --- LOCAL STORAGE SYSTEM ---
def load_data():
    global server_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                server_data = {int(k): v for k, v in raw_data.items()}
                for gid in server_data:
                    if 'users' in server_data[gid]:
                        # รองรับข้อมูลเก่าและใหม่
                        server_data[gid]['users'] = {int(uid): tag for uid, tag in server_data[gid]['users'].items()}
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
        server_data[guild_id] = {'whitelist': {}, 'config': {}, 'users': {}, 'zones': {}}
    return server_data[guild_id]

def update_user_info(guild_id, user_id, xbox_name, ic_name):
    data = get_guild_data(guild_id)
    if 'users' not in data: data['users'] = {}
    data['users'][user_id] = {'xbox': xbox_name, 'ic': ic_name}
    save_data()

# --- BOT SETUP ---
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
        app.router.add_post('/zone_api', self.handle_zone_api)
        app.router.add_get('/', lambda r: web.Response(text="Bot Online"))

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        self.web_server = web.TCPSite(runner, '0.0.0.0', port)
        await self.web_server.start()
        print(f"✅ Web Server running on port {port}")
        try: await self.tree.sync()
        except: pass

    # --- ZONE API ---
    async def handle_zone_api(self, request):
        try:
            data = await request.json()
            admin_tag = data.get('admin')
            action = data.get('action')

            guild_id = None
            for gid, g_data in server_data.items():
                users = g_data.get('users', {})
                for u_data in users.values():
                    # เช็คข้อมูลแบบ dict (ของใหม่) หรือ string (ของเก่า)
                    xbox = u_data.get('xbox') if isinstance(u_data, dict) else u_data
                    if xbox == admin_tag:
                        guild_id = gid
                        break
                if guild_id: break

            if not guild_id: return web.json_response({'status': 'error', 'msg': 'Admin not found'})

            g_data = get_guild_data(guild_id)
            if 'zones' not in g_data: g_data['zones'] = {}
            zones = g_data['zones']

            if action == 'list': return web.json_response({'status': 'ok', 'zones': list(zones.keys())})
            elif action == 'set':
                zone_name = data.get('zone')
                if zone_name in zones:
                    zones[zone_name]['pos1'] = data.get('pos1')
                    zones[zone_name]['pos2'] = data.get('pos2')
                    save_data()
                    return web.json_response({'status': 'ok'})
                return web.json_response({'status': 'error'})
            elif action == 'delete':
                zone_name = data.get('zone')
                if zone_name in zones:
                    zones[zone_name]['pos1'] = None
                    zones[zone_name]['pos2'] = None
                    save_data()
                    return web.json_response({'status': 'ok'})
        except Exception as e: return web.json_response({'status': 'error', 'msg': str(e)})

    # --- COORDS API ---
    async def handle_coords(self, request):
        try:
            data = await request.json()
            global game_state
            
            current = {}
            for p in data: current[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            game_state = current
            
            verified_names = []
            for g_data in server_data.values():
                for u_data in g_data.get('users', {}).values():
                    xbox = u_data.get('xbox') if isinstance(u_data, dict) else u_data
                    if xbox: verified_names.append(xbox)
            
            if not self.is_rate_limited:
                await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': list(set(verified_names))})
        except Exception as e: return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# ==========================================
# 🟢 UI FORMS & BUTTONS (เพิ่มปุ่ม IC / Status)
# ==========================================
class LinkModal(ui.Modal, title='ลงทะเบียนเชื่อมต่อระบบ'):
    xbox_name = ui.TextInput(label='ชื่อ Xbox Gamertag', placeholder='ใส่ชื่อในเกม Minecraft ที่แสดงบนหัว', required=True)
    ic_name = ui.TextInput(label='ชื่อตัวละคร (IC)', placeholder='ใส่ชื่อ-นามสกุลตัวละคร', required=True)

    async def on_submit(self, i: discord.Interaction):
        try:
            if not i.response.is_done(): await i.response.defer(ephemeral=True)
            xbox = self.xbox_name.value.strip()
            ic = self.ic_name.value.strip()
            
            update_user_info(i.guild_id, i.user.id, xbox, ic)
            
            embed = discord.Embed(title="✅ บันทึกข้อมูลสำเร็จ", color=0x2ecc71)
            embed.add_field(name="🎮 Xbox Gamertag", value=f"`{xbox}`", inline=False)
            embed.add_field(name="🎭 ชื่อ IC", value=f"`{ic}`", inline=False)
            embed.set_footer(text="ระบบได้ซิงค์ข้อมูลเข้ากับระบบในเกมแล้ว")
            
            await i.followup.send(embed=embed, ephemeral=True)
        except discord.errors.HTTPException: pass

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @ui.button(label="📝 กรอกชื่อ Xbox และชื่อ IC", style=discord.ButtonStyle.green, custom_id="mc_link_btn", emoji="📱")
    async def link_btn(self, i: discord.Interaction, b: ui.Button): 
        await i.response.send_modal(LinkModal())
        
    @ui.button(label="🔍 ตรวจสอบสถานะ", style=discord.ButtonStyle.blurple, custom_id="mc_status_btn", emoji="📊")
    async def status_btn(self, i: discord.Interaction, b: ui.Button):
        data = get_guild_data(i.guild_id)
        user_info = data.get('users', {}).get(i.user.id)
        
        if user_info:
            xbox = user_info.get('xbox', 'ไม่ระบุ') if isinstance(user_info, dict) else user_info
            ic = user_info.get('ic', 'ไม่ระบุ') if isinstance(user_info, dict) else "ไม่มีข้อมูล (ระบบเก่า)"
            
            embed = discord.Embed(title="📊 สถานะการเชื่อมต่อของคุณ", color=0x3498db)
            embed.add_field(name="🎮 Xbox", value=f"`{xbox}`", inline=True)
            embed.add_field(name="🎭 ชื่อ IC", value=f"`{ic}`", inline=True)
            embed.add_field(name="🟢 สถานะ", value="**เชื่อมต่อแล้ว**", inline=False)
            await i.response.send_message(embed=embed, ephemeral=True)
        else:
            await i.response.send_message("❌ **คุณยังไม่ได้ลงทะเบียน** กรุณากดปุ่มกรอกชื่อก่อนครับ", ephemeral=True)

# ==========================================
# COMMANDS
# ==========================================
@bot.tree.command(name="setup", description="ตั้งค่าเริ่มต้นและสร้างแผงเชื่อมต่อ")
async def setup(i: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel):
    try:
        if not i.response.is_done(): await i.response.defer(ephemeral=True)
        if not i.user.guild_permissions.administrator: return await i.followup.send("❌ Admin Only", ephemeral=True)
        
        data = get_guild_data(i.guild_id)
        data['whitelist'] = {'active': True, 'name': i.guild.name}
        data['config'] = {'category_id': category.id, 'start_channel_id': start_channel.id, 'range': DEFAULT_RANGE}
        save_data()
        
        # ส่ง Embed ควบคุม
        embed = discord.Embed(title="🎙️ ระบบ Voice Chat & โทรศัพท์", 
                              description="กรุณากดปุ่มด้านล่างเพื่อเชื่อมต่อชื่อ Xbox และชื่อ IC เข้ากับระบบเซิร์ฟเวอร์", 
                              color=0xf1c40f)
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3616/3616182.png")
        
        await i.channel.send(embed=embed, view=SetupView())
        await i.followup.send("✅ แผง Setup ถูกสร้างเรียบร้อยแล้ว!", ephemeral=True)
    except discord.errors.HTTPException:
        print("⚠️ Rate Limit Triggered on Setup")

@bot.tree.command(name="zone", description="สร้างห้องแยกพื้นที่ (โซน)")
async def make_zone(i: discord.Interaction, name: str, category: discord.CategoryChannel):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    
    data = get_guild_data(i.guild_id)
    if 'zones' not in data: data['zones'] = {}
    data['zones'][name] = {'category_id': category.id, 'pos1': None, 'pos2': None}
    save_data()
    await i.response.send_message(f"✅ สร้างโซน **{name}** ในหมวดหมู่ **{category.name}** แล้ว", ephemeral=True)

# --- CORE LOGIC ---
def is_in_zone(x, y, z, pos1, pos2):
    if not pos1 or not pos2: return False
    min_x, max_x = min(pos1['x'], pos2['x']), max(pos1['x'], pos2['x'])
    min_y, max_y = min(pos1['y'], pos2['y']), max(pos1['y'], pos2['y'])
    min_z, max_z = min(pos1['z'], pos2['z']), max(pos1['z'], pos2['z'])
    return (min_x <= x <= max_x) and (min_y <= y <= max_y) and (min_z <= z <= max_z)

async def process_voice_logic():
    curr = time.time()
    for u in [k for k,v in user_last_move.items() if curr-v > 60]: del user_last_move[u]

    for guild_id, data in server_data.items():
        if not data.get('whitelist', {}).get('active'): continue
        cfg = data.get('config', {})
        if not cfg: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        main_cat = guild.get_channel(cfg.get('category_id'))
        start = guild.get_channel(cfg.get('start_channel_id'))
        if not main_cat or not start: continue
        
        dist_sq = cfg.get('range', DEFAULT_RANGE) ** 2
        y_penalty = 3.0 
        
        zones = data.get('zones', {})
        buckets = {'__global__': {'category_id': main_cat.id, 'players': []}}
        for zname, zdata in zones.items():
            if zdata.get('category_id'): buckets[zname] = {'category_id': zdata['category_id'], 'players': []}
        
        users_map = data.get('users', {})
        for uid, user_info in users_map.items():
            gamertag = user_info.get('xbox') if isinstance(user_info, dict) else user_info
            if not gamertag: continue
            
            mem = guild.get_member(uid)
            if not mem or not mem.voice or not mem.voice.channel: continue
            
            if gamertag in game_state:
                p = game_state[gamertag]
                px, py, pz = p['x'], p['y'], p['z']
                
                current_zone = '__global__'
                for zname, zdata in zones.items():
                    if is_in_zone(px, py, pz, zdata.get('pos1'), zdata.get('pos2')):
                        current_zone = zname
                        break
                
                buckets[current_zone]['players'].append((mem, px, py, pz))
            else:
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: 
                            await mem.move_to(start)
                            user_last_move[mem.id] = curr
                            await asyncio.sleep(0.2)
                        except: pass
        
        taken = set()
        for b_name, bucket in buckets.items():
            if not bucket['players']: continue
            target_cat = guild.get_channel(bucket['category_id'])
            if not target_cat: continue
            avail = [c for c in target_cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
            online = bucket['players']
            
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
                    if ((x2-x1)**2 + ((y2-y1)*y_penalty)**2 + (z2-z1)**2) <= dist_sq:
                        grp.append(m2)
                        processed.add(j)
                groups.append(grp)
            
            for g in groups:
                target = None
                room_counts = {}
                for m in g: room_counts[m.voice.channel] = room_counts.get(m.voice.channel, 0) + 1
                maj_chan = max(room_counts, key=room_counts.get)
                
                if maj_chan.category_id != target_cat.id or maj_chan.id == start.id:
                    target = next((c for c in avail if len(c.members) == 0 and c.id not in taken), None)
                else: target = maj_chan
                
                if not target:
                    target = next((c for c in avail if c.id not in taken), None)
                    if not target and avail: target = avail[0] 
                
                if not target: continue
                taken.add(target.id)
                
                for m in g:
                    if m.voice.channel.id != target.id:
                        if curr - user_last_move.get(m.id, 0) < MOVE_COOLDOWN: continue
                        try:
                            await m.move_to(target)
                            user_last_move[m.id] = curr
                            await asyncio.sleep(0.2)
                        except discord.HTTPException as e:
                            if e.status == 429: await asyncio.sleep(2)

if __name__ == "__main__":
    if not TOKEN: sys.exit(1)
    time.sleep(random.randint(5, 10))
    while True:
        try:
            bot.is_rate_limited = False
            bot.run(TOKEN)
        except Exception as e:
            time.sleep(10)
