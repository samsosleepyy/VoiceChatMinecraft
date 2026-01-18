import discord
from discord import app_commands, ui, Webhook
from discord.ext import commands
import math
import asyncio
from aiohttp import web, ClientSession
import os
import random
import json
from jinja2 import Environment, FileSystemLoader

# --- CONFIGURATION ---
TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASS", "admin1234") 
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL", "") # ใส่ URL Webhook เพื่อรับแจ้งเตือน
DEFAULT_RANGE = 10

# --- DATA STORAGE ---
server_config = {}     # {guild_id: {'category_id': int, 'start_channel_id': int}}
user_links = {}        # {user_id: gamertag}
range_config = {}      # {guild_id: int}
game_state = {}        # {gamertag: {x, y, z}}

# --- WHITELIST SYSTEM ---
# structure: {str(guild_id): {"active": bool, "name": str}}
whitelist_data = {}

# Load Whitelist
if os.path.exists("whitelist.json"):
    try:
        with open("whitelist.json", "r", encoding="utf-8") as f:
            whitelist_data = json.load(f)
    except:
        whitelist_data = {}

def save_whitelist():
    try:
        with open("whitelist.json", "w", encoding="utf-8") as f:
            json.dump(whitelist_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving whitelist: {e}")

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True 

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.web_server = None

    async def setup_hook(self):
        # Setup Web Server
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
        
        # Get Port from Render
        port = int(os.environ.get("PORT", 8080))
        self.web_server = web.TCPSite(runner, '0.0.0.0', port)
        await self.web_server.start()
        print(f"✅ Bot started on port {port}")
        
        await self.tree.sync()

    # --- WEB HANDLERS ---
    async def handle_index(self, request):
        return web.Response(text="Bot is running. System Online.")

    async def handle_dashboard(self, request):
        try:
            # โหลดไฟล์ HTML จากโฟลเดอร์ templates
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template('dashboard.html')
            
            # Render HTML พร้อมส่งข้อมูลเข้าไป
            rendered = template.render(whitelist=whitelist_data, password=DASHBOARD_PASSWORD)
            return web.Response(text=rendered, content_type='text/html')
        except Exception as e:
            return web.Response(text=f"Error loading dashboard: {e}", status=500)

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
            # Update Game State
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            
            global game_state
            game_state = current_players
            
            # Find Verified Players
            verified_names = []
            for d_id, xbox in user_links.items():
                if xbox in game_state: verified_names.append(xbox)
            
            # Process Voice Logic
            await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- SECURITY SYSTEM (AUTO LEAVE) ---
@bot.event
async def on_guild_join(guild):
    gid_str = str(guild.id)
    
    # ถ้าไม่อยู่ใน Whitelist
    if gid_str not in whitelist_data:
        print(f"⚠️ Unauthorized join: {guild.name} ({guild.id})")
        
        # 1. สร้าง Invite Link ถาวร
        invite_url = "Cannot Create Invite"
        try:
            if guild.text_channels:
                invite = await guild.text_channels[0].create_invite(max_age=0, max_uses=0)
                invite_url = invite.url
        except: pass

        # 2. ส่ง Log เข้า Webhook ส่วนตัว
        if LOG_WEBHOOK_URL:
            async with ClientSession() as session:
                webhook = Webhook.from_url(LOG_WEBHOOK_URL, session=session)
                embed = discord.Embed(title="🚨 Unauthorized Server Detected", color=0xff0000)
                embed.add_field(name="Server Name", value=guild.name)
                embed.add_field(name="Server ID", value=guild.id)
                embed.add_field(name="Invite", value=invite_url)
                await webhook.send(embed=embed, username="Security Bot")

        # 3. เตือนใน Server นั้น 3 ข้อความ
        target_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
        # สุ่มเลือกมา 3 ห้อง (หรือเท่าที่มี)
        selected = random.sample(target_channels, min(len(target_channels), 3))
        
        msg = (
            "🚫 **This server is not whitelisted!**\n"
            "เซิฟเวอร์นี้ไม่ได้รับอนุญาตให้ใช้งานบอท\n"
            "กรุณาติดต่อแอดมิน: https://discord.gg/FnmWw7nWyq"
        )
        
        for ch in selected:
            try: await ch.send(msg)
            except: pass
        
        # 4. ออกจาก Server
        await guild.leave()
    
    else:
        # ถ้าอยู่ใน Whitelist ให้อัปเดตชื่อ
        whitelist_data[gid_str]['name'] = guild.name
        save_whitelist()

# --- SLASH COMMANDS ---

@bot.tree.command(name="whitelist", description="[Admin] เพิ่มเซิร์ฟเวอร์ลง whitelist")
async def add_whitelist(interaction: discord.Interaction, server_id: str):
    if not interaction.user.guild_permissions.administrator: 
        return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)

    whitelist_data[server_id] = {"active": True, "name": "Added via Command"}
    save_whitelist()
    await interaction.response.send_message(f"✅ เพิ่ม ID {server_id} เรียบร้อย", ephemeral=True)

@bot.tree.command(name="delwhitelist", description="[Admin] ลบเซิร์ฟเวอร์จาก whitelist")
async def remove_whitelist(interaction: discord.Interaction, server_id: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)

    if server_id in whitelist_data:
        del whitelist_data[server_id]
        save_whitelist()
        await interaction.response.send_message(f"🗑️ ลบ ID {server_id} เรียบร้อย", ephemeral=True)
    else:
        await interaction.response.send_message("❌ ไม่พบ ID นี้", ephemeral=True)

# --- UI SETUP ---
class LinkModal(ui.Modal, title='ยืนยันตัวตน Minecraft'):
    xbox_name = ui.TextInput(label='Xbox Gamertag', placeholder='ใส่ชื่อตัวละครของคุณให้ถูกต้อง')
    async def on_submit(self, interaction: discord.Interaction):
        gamertag = self.xbox_name.value.strip()
        user_links[interaction.user.id] = gamertag
        
        config = server_config.get(interaction.guild_id)
        msg = f"✅ บันทึกชื่อ **{gamertag}** เรียบร้อย!"
        if config:
            chan = interaction.guild.get_channel(config['start_channel_id'])
            if chan: msg += f"\n👉 ไปรอที่ห้อง {chan.mention} ได้เลย"
            
        await interaction.response.send_message(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="เชื่อมต่อ (Connect)", style=discord.ButtonStyle.green, custom_id="mc_link")
    async def link_btn(self, interaction: discord.Interaction, button: ui.Button): await interaction.response.send_modal(LinkModal())
    @ui.button(label="แก้ไขชื่อ (Edit Name)", style=discord.ButtonStyle.gray, custom_id="mc_edit")
    async def edit_btn(self, interaction: discord.Interaction, button: ui.Button): await interaction.response.send_modal(LinkModal())

@bot.tree.command(name="setup", description="ตั้งค่าระบบ Voice Chat")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    # Auto Add Whitelist for current server
    gid = str(interaction.guild_id)
    if gid not in whitelist_data:
         whitelist_data[gid] = {"active": True, "name": interaction.guild.name}
         save_whitelist()

    if start_channel.category_id != category.id: return await interaction.response.send_message("❌ ห้อง Start ต้องอยู่ใน Category เดียวกัน", ephemeral=True)
    
    server_config[interaction.guild_id] = {'category_id': category.id, 'start_channel_id': start_channel.id}
    if interaction.guild_id not in range_config: range_config[interaction.guild_id] = DEFAULT_RANGE

    embed = discord.Embed(
        title="Voice Chat System", 
        description="กดปุ่มด้านล่างเพื่อเชื่อมต่อและเริ่มใช้งาน",
        color=0x2ecc71
    )
    await interaction.channel.send(embed=embed, view=SetupView())
    await interaction.response.send_message("✅ Setup Completed!", ephemeral=True)

@bot.tree.command(name="range", description="ตั้งค่าระยะเสียง (Block)")
async def set_rage(interaction: discord.Interaction, distance: int):
    range_config[interaction.guild_id] = distance
    await interaction.response.send_message(f"🔊 ระยะเสียง: **{distance}** บล็อก", ephemeral=True)

# --- CORE LOGIC (FEW TO MANY) ---
async def process_voice_logic():
    for guild_id, config in server_config.items():
        gid_str = str(guild_id)
        
        # 1. Check Whitelist & Active Status
        if gid_str not in whitelist_data: continue
        if not whitelist_data[gid_str]['active']: continue

        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        category = guild.get_channel(config['category_id'])
        start_channel = guild.get_channel(config['start_channel_id'])
        if not category or not start_channel: continue

        dist_limit = range_config.get(guild_id, DEFAULT_RANGE)
        dist_sq_limit = dist_limit ** 2 # Optimize calc

        # 2. Prepare Users
        online_users = []
        for member_id, xbox_name in user_links.items():
            member = guild.get_member(member_id)
            if not member or not member.voice or not member.voice.channel: continue
            
            if member.voice.channel.category_id == category.id:
                if xbox_name in game_state:
                    pos = game_state[xbox_name]
                    online_users.append((member, pos['x'], pos['y'], pos['z']))
                else:
                    # Link but not in game -> Move to Start
                    if member.voice.channel.id != start_channel.id:
                        try: await member.move_to(start_channel)
                        except: pass

        # 3. Clustering
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
                
                # Check Distance (Squared)
                d_sq = (x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2
                if d_sq <= dist_sq_limit:
                    current_group.append(mem2)
                    processed.add(j)
            groups.append(current_group)

        # 4. Assign Channels (Majority Rule)
        available_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel) and c.id != start_channel.id]
        taken_channels = set() # Channels claimed in this tick

        for group in groups:
            target_channel = None
            
            # Vote for target channel
            votes = {}
            for m in group:
                c = m.voice.channel
                if c.id != start_channel.id and c.id not in taken_channels:
                    votes[c] = votes.get(c, 0) + 1
            
            if votes:
                target_channel = max(votes, key=votes.get)
            
            # If no majority (everyone in lobby), pick empty channel
            if not target_channel:
                for c in available_channels:
                    if len(c.members) == 0 and c.id not in taken_channels:
                        target_channel = c
                        break
            
            if not target_channel: continue # No room left
            
            taken_channels.add(target_channel.id)

            # Move members who are NOT in target
            for m in group:
                if m.voice.channel.id != target_channel.id:
                    try:
                        await m.move_to(target_channel)
                        await asyncio.sleep(0.1)
                    except: pass

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN missing.")
