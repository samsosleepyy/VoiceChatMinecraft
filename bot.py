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
                    if 'zones' not in server_data[gid]:
                        server_data[gid]['zones'] = {}
                    if 'users' in server_data[gid]:
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

# --- HELPER FUNCTIONS ---
def get_guild_data(guild_id):
    if guild_id not in server_data:
        server_data[guild_id] = {'whitelist': {}, 'config': {}, 'users': {}, 'zones': {}}
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

def update_user(guild_id, user_id, gamertag):
    data = get_guild_data(guild_id)
    if 'users' not in data: data['users'] = {}
    data['users'][user_id] = gamertag
    save_data()

def upsert_zone(guild_id, zone_name, category_id):
    data = get_guild_data(guild_id)
    if 'zones' not in data:
        data['zones'] = {}
    existing = data['zones'].get(zone_name, {})
    data['zones'][zone_name] = {
        'name': zone_name,
        'category_id': category_id,
        'active': existing.get('active', True),
        'corner1': existing.get('corner1'),
        'corner2': existing.get('corner2')
    }
    save_data()

def delete_zone(guild_id, zone_name):
    data = get_guild_data(guild_id)
    if 'zones' not in data:
        data['zones'] = {}
    if zone_name in data['zones']:
        del data['zones'][zone_name]
        save_data()
        return True
    return False

def set_zone_bounds(guild_id, zone_name, corner1, corner2):
    data = get_guild_data(guild_id)
    zones = data.get('zones', {})
    if zone_name not in zones:
        return False
    zones[zone_name]['corner1'] = corner1
    zones[zone_name]['corner2'] = corner2
    save_data()
    return True

def point_in_zone(pos, zone):
    c1 = zone.get('corner1')
    c2 = zone.get('corner2')
    if not c1 or not c2:
        return False
    min_x, max_x = sorted([c1['x'], c2['x']])
    min_y, max_y = sorted([c1['y'], c2['y']])
    min_z, max_z = sorted([c1['z'], c2['z']])
    return (min_x <= pos['x'] <= max_x and
            min_y <= pos['y'] <= max_y and
            min_z <= pos['z'] <= max_z)

def resolve_player_zone(data, pos):
    for zone_name, zone in data.get('zones', {}).items():
        if not zone.get('active', True):
            continue
        if point_in_zone(pos, zone):
            return zone_name, zone
    return None, None

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
        app.router.add_get('/zones', self.handle_zones)
        app.router.add_post('/zone/bounds', self.handle_zone_bounds)
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
        count = sum(1 for g in server_data.values() if g.get('whitelist', {}).get('active'))
        return web.Response(text=f"Bot Online (Local Mode) | Active Whitelists: {count}")

    async def handle_dashboard(self, request):
        try:
            whitelist_flat = {str(gid): d.get('whitelist') for gid, d in server_data.items() if d.get('whitelist')}
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template('dashboard.html')
            rendered = template.render(whitelist=whitelist_flat, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e: return web.Response(text=str(e), status=500)

    async def check_pass(self, data): return data.get('password') == DASHBOARD_PASSWORD

    async def handle_dash_toggle(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        toggle_whitelist(int(data.get('guild_id')))
        return web.HTTPFound('/dashboard')

    async def handle_dash_add(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        gid = data.get('guild_id')
        if gid: update_whitelist(int(gid), "Added via Web")
        return web.HTTPFound('/dashboard')

    async def handle_dash_remove(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        remove_whitelist(int(data.get('guild_id')))
        return web.HTTPFound('/dashboard')

    async def handle_coords(self, request):
        try:
            data = await request.json()
            global game_state
            
            # อัปเดตตำแหน่งล่าสุดของผู้เล่นทุกคน
            current = {}
            for p in data: current[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            game_state = current
            
            # ส่งรายชื่อที่ยืนยันแล้วกลับไป (ป้องกัน Invalid Name ในเกม)
            verified_names = []
            for g_data in server_data.values():
                verified_names.extend(g_data.get('users', {}).values())
            
            # เรียกฟังก์ชันจัดการห้องเสียง
            if not self.is_rate_limited:
                await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': list(set(verified_names))})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

    async def handle_zones(self, request):
        guild_id = request.query.get('guild_id')
        if not guild_id:
            return web.json_response({'status': 'error', 'message': 'guild_id required'}, status=400)
        try:
            gid = int(guild_id)
            data = get_guild_data(gid)
            zones = []
            for z in data.get('zones', {}).values():
                zones.append({
                    'name': z.get('name'),
                    'category_id': z.get('category_id'),
                    'active': z.get('active', True),
                    'has_bounds': bool(z.get('corner1') and z.get('corner2'))
                })
            return web.json_response({'status': 'ok', 'zones': zones})
        except Exception as e:
            return web.json_response({'status': 'error', 'message': str(e)}, status=500)

    async def handle_zone_bounds(self, request):
        try:
            data = await request.json()
            if data.get('password') != DASHBOARD_PASSWORD:
                return web.json_response({'status': 'error', 'message': 'forbidden'}, status=403)
            gid = int(data['guild_id'])
            zone_name = data['zone_name']
            corner1 = data['corner1']
            corner2 = data['corner2']
            ok = set_zone_bounds(gid, zone_name, corner1, corner2)
            if not ok:
                return web.json_response({'status': 'error', 'message': 'zone not found'}, status=404)
            return web.json_response({'status': 'ok'})
        except Exception as e:
            return web.json_response({'status': 'error', 'message': str(e)}, status=500)

bot = MyBot()

@bot.event
async def on_guild_join(guild):
    data = get_guild_data(guild.id)
    if not data.get('whitelist'):
        if LOG_WEBHOOK_URL:
            async with ClientSession() as session:
                webhook = Webhook.from_url(LOG_WEBHOOK_URL, session=session)
                try: inv = await guild.text_channels[0].create_invite()
                except: inv = "None"
                await webhook.send(f"🚨 Unauthorized Join: {guild.name} ({guild.id})", username="Security")
        try:
            for ch in random.sample(guild.text_channels, min(len(guild.text_channels), 3)):
                await ch.send("🚫 Not Whitelisted: https://discord.gg/FnmWw7nWyq")
        except: pass
        await guild.leave()
    else:
        update_whitelist(guild.id, guild.name, data['whitelist'].get('active', True))

class LinkModal(ui.Modal, title='ยืนยันตัวตน Minecraft'):
    xbox_name = ui.TextInput(label='Xbox Gamertag')
    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        gamertag = self.xbox_name.value.strip()
        update_user(interaction.guild_id, interaction.user.id, gamertag)
        
        data = get_guild_data(interaction.guild_id)
        cfg = data.get('config', {})
        msg = f"✅ Saved **{gamertag}**"
        if 'start_channel_id' in cfg:
            chan = interaction.guild.get_channel(cfg['start_channel_id'])
            if chan: msg += f"\n👉 Go to {chan.mention}"
        await interaction.followup.send(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="Connect", style=discord.ButtonStyle.green, custom_id="mc_link")
    async def link(self, i: discord.Interaction, b: ui.Button): await i.response.send_modal(LinkModal())
    @ui.button(label="Edit Name", style=discord.ButtonStyle.gray, custom_id="mc_edit")
    async def edit(self, i: discord.Interaction, b: ui.Button): await i.response.send_modal(LinkModal())

@bot.tree.command(name="setup")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel, role: discord.Role = None):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.administrator: return await interaction.followup.send("❌ Admin Only", ephemeral=True)
    
    update_whitelist(interaction.guild_id, interaction.guild.name)
    update_config(interaction.guild_id, category.id, start_channel.id, DEFAULT_RANGE)

    msg = "✅ Setup Complete!"
    if role:
        await interaction.followup.send(f"⏳ Setting permissions...", ephemeral=True)
        try:
            for c in category.channels:
                if c.id == start_channel.id: await c.set_permissions(role, view_channel=True, connect=True)
                else: await c.set_permissions(role, view_channel=False)
            if isinstance(interaction.channel, discord.TextChannel):
                 await interaction.channel.set_permissions(role, view_channel=True)
            msg += "\n✨ Permissions Updated"
        except: msg += "\n⚠️ Permission Error"
        await interaction.edit_original_response(content=msg)
    else:
        await interaction.followup.send(msg, ephemeral=True)
    await interaction.channel.send(embed=discord.Embed(title="Voice Chat", description="Click to Connect", color=0x2ecc71), view=SetupView())

@bot.tree.command(name="whitelist")
async def wl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    update_whitelist(int(server_id), "Added via Cmd")
    await i.response.send_message(f"✅ Whitelisted {server_id}", ephemeral=True)

@bot.tree.command(name="delwhitelist")
async def dwl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    remove_whitelist(int(server_id))
    await i.response.send_message(f"🗑️ Deleted {server_id}", ephemeral=True)

@bot.tree.command(name="range")
async def set_range(i: discord.Interaction, distance: int):
    data = get_guild_data(i.guild_id)
    cfg = data.get('config', {})
    if 'category_id' in cfg:
        update_config(i.guild_id, cfg['category_id'], cfg['start_channel_id'], distance)
        await i.response.send_message(f"🔊 Range set to {distance}", ephemeral=True)
    else:
        await i.response.send_message("❌ Please run /setup first", ephemeral=True)

@bot.tree.command(name="zone")
@app_commands.describe(name="ชื่อโซน", category="หมวดหมู่สำหรับโซนนี้")
async def set_zone(i: discord.Interaction, name: str, category: discord.CategoryChannel):
    if not i.user.guild_permissions.administrator:
        return await i.response.send_message("❌ Admin Only", ephemeral=True)
    upsert_zone(i.guild_id, name.strip(), category.id)
    await i.response.send_message(
        f"✅ Zone `{name}` linked to category **{category.name}**\n"
        f"Addon pull list: `/zones?guild_id={i.guild_id}`",
        ephemeral=True
    )

@bot.tree.command(name="delzone")
@app_commands.describe(name="ชื่อโซนที่จะลบ")
async def remove_zone_cmd(i: discord.Interaction, name: str):
    if not i.user.guild_permissions.administrator:
        return await i.response.send_message("❌ Admin Only", ephemeral=True)
    ok = delete_zone(i.guild_id, name.strip())
    if ok:
        await i.response.send_message(f"🗑️ Deleted zone `{name}`", ephemeral=True)
    else:
        await i.response.send_message(f"⚠️ Zone `{name}` not found", ephemeral=True)

# --- 🟢 CORE LOGIC: Auto-Move & Disconnect Handling 🟢 ---
async def process_voice_logic():
    curr = time.time()
    for u in [k for k,v in user_last_move.items() if curr-v > 60]: del user_last_move[u]

    for guild_id, data in server_data.items():
        if not data.get('whitelist', {}).get('active'): continue
        
        cfg = data.get('config', {})
        if not cfg: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        default_cat = guild.get_channel(cfg.get('category_id'))
        start = guild.get_channel(cfg.get('start_channel_id'))
        if not default_cat or not start: continue

        managed_category_ids = {default_cat.id}
        zones = data.get('zones', {})
        for zone in zones.values():
            if zone.get('category_id'):
                managed_category_ids.add(zone['category_id'])

        dist_sq = cfg.get('range', DEFAULT_RANGE) ** 2
        users_map = data.get('users', {})

        # bucket_key -> {category_id, players:[(member,x,y,z)]}
        buckets = {}
        for uid, gamertag in users_map.items():
            mem = guild.get_member(uid)
            if not mem or not mem.voice or not mem.voice.channel: continue
            if mem.voice.channel.category_id not in managed_category_ids: continue

            # ✅ LOGIC 1: เช็คว่าอยู่ในเกมหรือไม่?
            if gamertag in game_state:
                p = game_state[gamertag]
                zone_name, zone_data = resolve_player_zone(data, p)
                if zone_name:
                    bucket_key = f"zone::{zone_name}"
                    category_id = zone_data.get('category_id', default_cat.id)
                else:
                    bucket_key = "default"
                    category_id = default_cat.id
                if bucket_key not in buckets:
                    buckets[bucket_key] = {'category_id': category_id, 'players': []}
                buckets[bucket_key]['players'].append((mem, p['x'], p['y'], p['z']))
            else:
                # ❌ ออกจากเกมแล้ว (Disconnect) -> ย้ายกลับ Lobby
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: 
                            await mem.move_to(start)
                            user_last_move[mem.id] = curr
                            await asyncio.sleep(0.2)
                        except: pass
        
        if not buckets:
            continue

        for bucket in buckets.values():
            players = bucket['players']
            target_category = guild.get_channel(bucket['category_id'])
            if not target_category:
                target_category = default_cat

            # ✅ LOGIC 2: จัดกลุ่มคนเล่น (Clustering by range)
            groups = []
            processed = set()
            for i in range(len(players)):
                if i in processed: continue
                m1, x1, y1, z1 = players[i]
                grp = [m1]
                processed.add(i)
                for j in range(i + 1, len(players)):
                    if j in processed: continue
                    m2, x2, y2, z2 = players[j]
                    if ((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2) <= dist_sq:
                        grp.append(m2)
                        processed.add(j)
                groups.append(grp)

            # ✅ LOGIC 3: เลือกห้องใน category ของ bucket เท่านั้น
            avail = [c for c in target_category.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
            taken = set()

            for g in groups:
                target = None
                room_counts = {}
                for m in g:
                    c = m.voice.channel
                    # ไม่ให้นับห้องนอก category ของ bucket
                    if c.category_id != target_category.id:
                        continue
                    room_counts[c] = room_counts.get(c, 0) + 1

                if room_counts:
                    majority_channel = max(room_counts, key=room_counts.get)
                    if majority_channel.id != start.id:
                        target = majority_channel

                if not target:
                    for c in avail:
                        if len(c.members) == 0 and c.id not in taken:
                            target = c
                            break

                if not target and avail:
                    target = avail[0]

                if not target:
                    continue

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
        except discord.errors.HTTPException as e:
            if e.status == 429:
                bot.is_rate_limited = True
                time.sleep(60)
            else: time.sleep(10)
        except: time.sleep(30)
