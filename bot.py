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
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASS", "admin1234") 
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL", "") 
# ลิงก์ Database ของคุณ
FIREBASE_DB_URL = "https://vcdata-2212b-default-rtdb.asia-southeast1.firebasedatabase.app/"
DEFAULT_RANGE = 10
MOVE_COOLDOWN = 3.0

# --- DATA STORES (In-Memory) ---
# โครงสร้างใหม่: แยกข้อมูลตาม Guild ID เพื่อไม่ให้ตีกัน
server_data = {}  # { guild_id: { 'whitelist': {}, 'config': {}, 'users': {uid: gamertag} } }
game_state = {}   # { gamertag: {x, y, z} } (Global)
user_last_move = {}

# --- FIREBASE CREDENTIALS (HARDCODED) ---
FIREBASE_CREDENTIALS = {
  "type": "service_account",
  "project_id": "vcdata-2212b",
  "private_key_id": "95334a89b140ade35f522fa759d0738585396120",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCyFTbC+pauyKes\nae68HFSM19c2zJSCergpg41Hw6MTnZ2sKXIVFGNwElSrDAmU5jl5RVvLLLqsyUs0\n+BivGmTQ0m4P7R7xuhkt8yopPmWEDPbf882USBervJsaIzntxqaeUkS7KNOBag/l\nwX0o3dk7OCA8qUl3h0Zn9U/UzKI79cFIJtfUNTaRezl+bl3D/1Gw2o52QrRTjVAk\nG/k8JCxfHXJxCy6mvSX4m15Od7ucRPA2RNQ7oHpLQ5a8cnoVnC0tR0sFq6Fj2t8L\nGG077SXB32XplMvhLKV4LHvZnc1hVFofJVJCfOk6pjyPYQhmEQS2ql9VkwksKQVv\nn8nyJzoXAgMBAAECggEADIlExR4J3H0AnLkKVtCxvQZ2voNRUwwbicSaffpOMRPP\n5S43uzcnttx7fF0JEaPRWPGigB+CdqZm9nAeoLj9btvZZqKdIowkuKDdD3E2iUC2\nYlaR2sXmcK8CxijDq7LnyM5myzwZA4u5WcWwr19KqwoM7uhF5TWvJaNVvte29fx9\nRnkFnsixZg6kOYAKs1bEnRWJiuQ0miJEgO+F8AZsjZQ7DhP+lsua4VBIqyf3zCnQ\nq7AEO4ggewpzxRu4iAaD7F60yyUsqjYD/gY0NSJafcgflyJSSQUi+hjkGB9mIm7M\nq36HSAdln1vo7bPNmaf/Rf+lYM1lJ15Rku4EBaRK4QKBgQDhx1QgiETbixt7H7A/\n3DJ1WkQBTGVLb4XrLa/BMMShWzY2pnSCdN/Mmdc16IpkMTrPqcaB56i3R85d8ttR\nKReFKq0pQ8UvZMqhybVTNoPUoRzauff4qchXkQT/1DBLgzKMvaaUqoPHtClRC/jY\nY3XJhAZDSsJDnnAhengHHrOM9wKBgQDJ64M47jM1xxYQ3/ZmTEPxg/9rUaoqay4s\nfa0tHD/o5yv6Nq3UhPoVNMBw5fYOTbwlZ2qLSP0dpWElKytAzGMkwawhC1eSIYo/\nwA7wcNzzHx3bxRsRWHCmng3rxqRYHw7nB0IooR53hQQFQQ6AABOCHQhNZyJ0VJHb\nXum6Ba4T4QKBgAO+qZ+Mgw/dI8yL/wFgJpoZsC0RVlDE/cSj0lly9J/0glavthj/\n1UJwfshPHhSBWIdfOoKnE/5OO5cFUyvqcZBs38hibl/V3SKH1PEXY2JgdbkPApTm\nRANnzVxs6YwnFeyNrLikh2EFlPXaK/ty0t5PyUbOc6BpfVSg0mLT2IiLAoGBAIDP\njVa0HlcgOiNpvHZmELHx0u9TmYqV9U7Mnb05WEvrrVJhr2LzsdX1YQ6kpONbE7uI\nzZ8tYMuYxPBBKcacnGLGalhqM+M1Ikyo6N7aIRm3sASTKUFXegXQrnDKt+y/Y3Je\nXwYsQpNcd8QiTG27nrZSbwlx0bkEekfHtLLHDNYBAoGBAKICuZyA0HW7Nn4i3P/2\ncwRdKNhs7kXAmwDLf7WzOTBrlfjp1ZceDkNhuAaM2IhFxRwV0vkzAcD3a0/tMGhi\neyKg1aMiU2np5db1QsHi+NidQT4tI9gEk1M3DMnmy2TQ3HhDUBh9DtqWtSUKByFS\nhKHcG7h9tHPl3MK6XMNpy6my\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-fbsvc@vcdata-2212b.iam.gserviceaccount.com",
  "client_id": "118188525770963332924",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40vcdata-2212b.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}

# --- FIREBASE INIT ---
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    
    # Root Reference: servers
    ref_servers = db.reference('servers')
    
    print("📡 Loading data from Firebase...")
    raw_data = ref_servers.get() or {}
    
    # Parse Data to Memory
    for gid_str, data in raw_data.items():
        gid = int(gid_str)
        server_data[gid] = {
            'whitelist': data.get('whitelist', {'active': False, 'name': 'Unknown'}),
            'config': data.get('config', {}),
            'users': {int(uid): tag for uid, tag in data.get('users', {}).items()}
        }
        
    print(f"✅ Loaded Data for {len(server_data)} Servers")

except Exception as e:
    print(f"⚠️ Firebase Init Error: {e}")

# --- HELPER FUNCTIONS (Scoped by Guild ID) ---
def get_guild_data(guild_id):
    if guild_id not in server_data:
        server_data[guild_id] = {'whitelist': {}, 'config': {}, 'users': {}}
    return server_data[guild_id]

def db_save_whitelist(guild_id, name, active=True):
    data = get_guild_data(guild_id)
    data['whitelist'] = {'active': active, 'name': name}
    try: ref_servers.child(str(guild_id)).child('whitelist').set(data['whitelist'])
    except: pass

def db_remove_whitelist(guild_id):
    if guild_id in server_data:
        del server_data[guild_id]['whitelist']
        try: ref_servers.child(str(guild_id)).child('whitelist').delete()
        except: pass

def db_toggle_whitelist(guild_id):
    data = get_guild_data(guild_id)
    if data['whitelist']:
        new_stat = not data['whitelist'].get('active', False)
        data['whitelist']['active'] = new_stat
        try: ref_servers.child(str(guild_id)).child('whitelist').update({'active': new_stat})
        except: pass

def db_save_config(guild_id, category_id, start_channel_id, range_val):
    data = get_guild_data(guild_id)
    cfg = {'category_id': category_id, 'start_channel_id': start_channel_id, 'range': range_val}
    data['config'] = cfg
    try: ref_servers.child(str(guild_id)).child('config').set(cfg)
    except: pass

def db_save_user(guild_id, user_id, gamertag):
    data = get_guild_data(guild_id)
    data['users'][user_id] = gamertag
    try: ref_servers.child(str(guild_id)).child('users').child(str(user_id)).set(gamertag)
    except: pass

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
        await self.tree.sync()

    # --- WEB HANDLERS ---
    async def handle_index(self, request):
        count = sum(1 for g in server_data.values() if g.get('whitelist', {}).get('active'))
        return web.Response(text=f"Bot Online | Active Whitelists: {count}")

    async def handle_dashboard(self, request):
        try:
            # Flatten data for template
            whitelist_flat = {str(gid): d['whitelist'] for gid, d in server_data.items() if d.get('whitelist')}
            
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template('dashboard.html')
            rendered = template.render(whitelist=whitelist_flat, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e: return web.Response(text=str(e), status=500)

    async def check_pass(self, data): return data.get('password') == DASHBOARD_PASSWORD

    async def handle_dash_toggle(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        db_toggle_whitelist(int(data.get('guild_id')))
        return web.HTTPFound('/dashboard')

    async def handle_dash_add(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        gid = data.get('guild_id')
        if gid: db_save_whitelist(int(gid), "Added via Web")
        return web.HTTPFound('/dashboard')

    async def handle_dash_remove(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        db_remove_whitelist(int(data.get('guild_id')))
        return web.HTTPFound('/dashboard')

    async def handle_coords(self, request):
        try:
            data = await request.json()
            global game_state
            
            # 1. Update Game State (Global)
            current = {}
            for p in data: current[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            game_state = current
            
            # 2. Collect ALL verified names from ALL servers to return
            # (Safety net to prevent invalid name errors on any connected server)
            verified_names = []
            for g_data in server_data.values():
                verified_names.extend(g_data['users'].values())
            
            # 3. Process Logic
            if not self.is_rate_limited:
                await process_voice_logic()
            
            # Return UNIQUE names
            return web.json_response({'status': 'ok', 'verified': list(set(verified_names))})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- SECURITY ---
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
        db_save_whitelist(guild.id, guild.name, data['whitelist'].get('active', True))

# --- UI & COMMANDS ---
class LinkModal(ui.Modal, title='ยืนยันตัวตน Minecraft'):
    xbox_name = ui.TextInput(label='Xbox Gamertag')
    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        gamertag = self.xbox_name.value.strip()
        
        # Save Scoped to Guild
        db_save_user(interaction.guild_id, interaction.user.id, gamertag)
        
        data = get_guild_data(interaction.guild_id)
        cfg = data.get('config', {})
        msg = f"✅ Saved **{gamertag}** for this server."
        
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
    
    db_save_whitelist(interaction.guild_id, interaction.guild.name)
    db_save_config(interaction.guild_id, category.id, start_channel.id, DEFAULT_RANGE)

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
    db_save_whitelist(int(server_id), "Added via Cmd")
    await i.response.send_message(f"✅ Whitelisted {server_id}", ephemeral=True)

@bot.tree.command(name="delwhitelist")
async def dwl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    db_remove_whitelist(int(server_id))
    await i.response.send_message(f"🗑️ Deleted {server_id}", ephemeral=True)

@bot.tree.command(name="range")
async def set_range(i: discord.Interaction, distance: int):
    data = get_guild_data(i.guild_id)
    cfg = data.get('config', {})
    if 'category_id' in cfg:
        db_save_config(i.guild_id, cfg['category_id'], cfg['start_channel_id'], distance)
        await i.response.send_message(f"🔊 Range set to {distance}", ephemeral=True)
    else:
        await i.response.send_message("❌ Please run /setup first", ephemeral=True)

# --- CORE LOGIC (Few-to-Many + Disconnect + Majority Rule) ---
async def process_voice_logic():
    curr = time.time()
    for u in [k for k,v in user_last_move.items() if curr-v > 60]: del user_last_move[u]

    # Iterate through all loaded server data
    for guild_id, data in server_data.items():
        if not data.get('whitelist', {}).get('active'): continue
        
        cfg = data.get('config', {})
        if not cfg: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        cat = guild.get_channel(cfg.get('category_id'))
        start = guild.get_channel(cfg.get('start_channel_id'))
        if not cat or not start: continue
        
        dist_sq = cfg.get('range', DEFAULT_RANGE) ** 2
        users_map = data.get('users', {}) # Users specific to this guild
        
        # 1. Gather Online Users
        online = []
        for uid, gamertag in users_map.items():
            mem = guild.get_member(uid)
            if not mem or not mem.voice or not mem.voice.channel: continue
            if mem.voice.channel.category_id != cat.id: continue
            
            if gamertag in game_state:
                p = game_state[gamertag]
                online.append((mem, p['x'], p['y'], p['z']))
            else:
                # Disconnect -> Lobby
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: 
                            await mem.move_to(start)
                            user_last_move[mem.id] = curr
                            await asyncio.sleep(0.2)
                        except: pass
        
        # 2. Clustering
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
            
        # 3. Channel Management (Majority Rule)
        avail = [c for c in cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
        taken = set()
        
        for g in groups:
            target = None
            room_counts = {}
            for m in g:
                c = m.voice.channel
                room_counts[c] = room_counts.get(c, 0) + 1
            
            # Majority Vote
            majority_channel = max(room_counts, key=room_counts.get)
            
            if majority_channel.id == start.id:
                # Majority in Lobby -> New Room for everyone
                for c in avail:
                    if len(c.members) == 0 and c.id not in taken:
                        target = c
                        break
            else:
                # Majority in Game Room -> Few to Many
                target = majority_channel
            
            # Fallback if target invalid
            if not target:
                 # Try finding any non-lobby room in the group
                 for c in room_counts:
                     if c.id != start.id: target = c; break
            
            if not target: continue
            taken.add(target.id)
            
            # Move Logic
            for m in g:
                if m.voice.channel.id != target.id:
                    if curr - user_last_move.get(m.id, 0) < MOVE_COOLDOWN: continue
                    try:
                        await m.move_to(target)
                        user_last_move[m.id] = curr
                        await asyncio.sleep(0.2)
                    except discord.HTTPException as e:
                        if e.status == 429: await asyncio.sleep(2)

# --- MAIN LOOP ---
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
