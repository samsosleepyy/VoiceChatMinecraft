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
FIREBASE_DB_URL = "https://vcdata-2212b-default-rtdb.asia-southeast1.firebasedatabase.app/"
DEFAULT_RANGE = 10
MOVE_COOLDOWN = 3.0

# --- DATA STORES (In-Memory Cache) ---
server_config = {}
user_links = {}
range_config = {}
game_state = {}
whitelist_data = {}
user_last_move = {}

# --- FIREBASE CREDENTIALS ---
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
    
    # DB Refs
    ref_whitelist = db.reference('whitelist')
    ref_users = db.reference('users')
    ref_config = db.reference('server_config')
    
    # Load Data to Memory (Sync)
    print("📡 Loading data from Firebase...")
    whitelist_data = ref_whitelist.get() or {}
    users_data_raw = ref_users.get() or {}
    server_config_raw = ref_config.get() or {}
    
    # Transform Data for Memory
    user_links = {int(k): v.get('gamertag') for k, v in users_data_raw.items()}
    server_config = {}
    range_config = {}
    
    for gid, cfg in server_config_raw.items():
        server_config[int(gid)] = {
            'category_id': cfg.get('category_id'),
            'start_channel_id': cfg.get('start_channel_id')
        }
        range_config[int(gid)] = cfg.get('range', DEFAULT_RANGE)
        
    print(f"✅ Loaded: {len(whitelist_data)} Whitelists, {len(user_links)} Users")

except Exception as e:
    print(f"⚠️ Firebase Error: {e}")
    # Fallback to empty if failed
    whitelist_data = {}
    user_links = {}
    server_config = {}
    range_config = {}

# --- HELPER FUNCTIONS (Sync to Firebase) ---
def db_save_user(user_id, gamertag, guild_id):
    user_links[user_id] = gamertag # Memory
    try:
        ref_users.child(str(user_id)).set({
            'gamertag': gamertag,
            'source_guild': str(guild_id),
            'timestamp': time.time()
        })
    except: pass

def db_save_config(guild_id, category_id, start_channel_id, range_val):
    server_config[guild_id] = {'category_id': category_id, 'start_channel_id': start_channel_id} # Memory
    range_config[guild_id] = range_val
    try:
        ref_config.child(str(guild_id)).set({
            'category_id': category_id,
            'start_channel_id': start_channel_id,
            'range': range_val
        })
    except: pass

def db_add_whitelist(guild_id, name):
    gid = str(guild_id)
    whitelist_data[gid] = {'active': True, 'name': name} # Memory
    try: ref_whitelist.child(gid).set(whitelist_data[gid])
    except: pass

def db_remove_whitelist(guild_id):
    gid = str(guild_id)
    if gid in whitelist_data: del whitelist_data[gid] # Memory
    try: ref_whitelist.child(gid).delete()
    except: pass

def db_toggle_whitelist(guild_id):
    gid = str(guild_id)
    if gid in whitelist_data:
        new_stat = not whitelist_data[gid]['active']
        whitelist_data[gid]['active'] = new_stat
        try: ref_whitelist.child(gid).update({'active': new_stat})
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
        # API Routes
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
        print(f"✅ Web Server running on port {port}")
        await self.tree.sync()

    # --- WEB HANDLERS ---
    async def handle_index(self, request):
        return web.Response(text=f"Bot Online | Whitelist: {len(whitelist_data)}")

    async def handle_dashboard(self, request):
        try:
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template('dashboard.html')
            rendered = template.render(whitelist=whitelist_data, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e: return web.Response(text=str(e), status=500)

    async def check_pass(self, data): return data.get('password') == DASHBOARD_PASSWORD

    async def handle_dash_toggle(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        db_toggle_whitelist(data.get('guild_id'))
        return web.HTTPFound('/dashboard')

    async def handle_dash_add(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        gid = data.get('guild_id')
        if gid: db_add_whitelist(gid, "Added via Web")
        return web.HTTPFound('/dashboard')

    async def handle_dash_remove(self, request):
        data = await request.post()
        if not await self.check_pass(data): return web.Response(text="Wrong Password", status=403)
        db_remove_whitelist(data.get('guild_id'))
        return web.HTTPFound('/dashboard')

    # --- 🟢 RESTORED LOGIC FROM ORIGINAL BOT 🟢 ---
    # ใช้ Logic เดิมที่เสถียรที่สุดในการรับส่งข้อมูล
    async def handle_coords(self, request):
        try:
            data = await request.json()
            
            # 1. Update Game State
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            
            global game_state
            game_state = current_players
            
            # 2. Get Verified List (Always return this to prevent "Invalid Name" error)
            verified_names = []
            for d_id, xbox in user_links.items():
                # ส่งกลับไปบอก Minecraft ว่าคนนี้ยืนยันแล้วนะ (แม้จะไม่ออนไลน์ในเกมก็ตาม)
                verified_names.append(xbox)
            
            # 3. Process Voice Logic (Only if not rate limited)
            if not self.is_rate_limited:
                await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- SECURITY ---
@bot.event
async def on_guild_join(guild):
    gid = str(guild.id)
    if gid not in whitelist_data:
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
        db_add_whitelist(gid, guild.name)

# --- UI & COMMANDS ---
class LinkModal(ui.Modal, title='ยืนยันตัวตน Minecraft'):
    xbox_name = ui.TextInput(label='Xbox Gamertag')
    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        gamertag = self.xbox_name.value.strip()
        
        # Save to DB & Memory
        db_save_user(interaction.user.id, gamertag, interaction.guild_id)
        
        gid = interaction.guild_id
        msg = f"✅ Saved **{gamertag}**"
        
        if gid in server_config:
            chan_id = server_config[gid]['start_channel_id']
            chan = interaction.guild.get_channel(chan_id)
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
    
    # Save Config
    db_add_whitelist(interaction.guild_id, interaction.guild.name)
    db_save_config(interaction.guild_id, category.id, start_channel.id, range_config.get(interaction.guild_id, DEFAULT_RANGE))

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
    db_add_whitelist(server_id, "Added via Cmd")
    await i.response.send_message(f"✅ Whitelisted {server_id}", ephemeral=True)

@bot.tree.command(name="delwhitelist")
async def dwl(i: discord.Interaction, server_id: str):
    if not i.user.guild_permissions.administrator: return await i.response.send_message("❌ Admin Only", ephemeral=True)
    db_remove_whitelist(server_id)
    await i.response.send_message(f"🗑️ Deleted {server_id}", ephemeral=True)

@bot.tree.command(name="range")
async def set_range(i: discord.Interaction, distance: int):
    gid = i.guild_id
    if gid in server_config:
        cfg = server_config[gid]
        db_save_config(gid, cfg['category_id'], cfg['start_channel_id'], distance)
    else:
        # Fallback if config not set yet
        range_config[gid] = distance
    await i.response.send_message(f"🔊 Range set to {distance}", ephemeral=True)

# --- LOGIC (Few-to-Many + Anti-Rate Limit + Disconnect) ---
async def process_voice_logic():
    curr = time.time()
    # Cleanup Cooldowns
    for u in [k for k,v in user_last_move.items() if curr-v > 60]: del user_last_move[u]

    for guild_id, config in server_config.items():
        gid_str = str(guild_id)
        if gid_str not in whitelist_data or not whitelist_data[gid_str]['active']: continue
        
        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        cat = guild.get_channel(config['category_id'])
        start = guild.get_channel(config['start_channel_id'])
        if not cat or not start: continue
        
        dist_sq = range_config.get(guild_id, DEFAULT_RANGE) ** 2
        
        online = []
        for uid, gamertag in user_links.items():
            mem = guild.get_member(uid)
            if not mem or not mem.voice or not mem.voice.channel: continue
            if mem.voice.channel.category_id != cat.id: continue
            
            if gamertag in game_state:
                # In Game
                p = game_state[gamertag]
                online.append((mem, p['x'], p['y'], p['z']))
            else:
                # Disconnected -> Move to Lobby (Lobby Check)
                if mem.voice.channel.id != start.id:
                    if curr - user_last_move.get(mem.id, 0) > MOVE_COOLDOWN:
                        try: 
                            await mem.move_to(start)
                            user_last_move[mem.id] = curr
                            await asyncio.sleep(0.2)
                        except: pass
        
        # Clustering
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
            
        # Assign & Move
        avail = [c for c in cat.channels if isinstance(c, discord.VoiceChannel) and c.id != start.id]
        taken = set()
        
        for g in groups:
            target = None
            votes = {}
            for m in g:
                if m.voice.channel.id != start.id and m.voice.channel.id not in taken:
                    votes[m.voice.channel] = votes.get(m.voice.channel, 0)+1
            if votes: target = max(votes, key=votes.get)
            if not target:
                for c in avail:
                    if len(c.members)==0 and c.id not in taken: target=c; break
            if not target: continue
            taken.add(target.id)
            
            for m in g:
                if m.voice.channel.id == target.id: continue
                if curr - user_last_move.get(m.id, 0) < MOVE_COOLDOWN: continue
                try:
                    await m.move_to(target)
                    user_last_move[m.id] = curr
                    await asyncio.sleep(0.2)
                except discord.HTTPException as e:
                    if e.status==429: await asyncio.sleep(2)

# --- MAIN LOOP ---
if __name__ == "__main__":
    if not TOKEN: sys.exit(1)
    # Start Delay to prevent rate limit on frequent restarts
    time.sleep(random.randint(5, 15))
    while True:
        try:
            bot.is_rate_limited = False
            bot.run(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                bot.is_rate_limited = True
                time.sleep(60) # Sleep longer if rate limited
            else: time.sleep(10)
        except: time.sleep(30)
