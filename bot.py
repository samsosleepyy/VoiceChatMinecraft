import discord
from discord import app_commands, ui
from discord.ext import commands
import math
import asyncio
from aiohttp import web
import os

# --- CONFIGURATION FROM RENDER ---
# รับค่า Token จาก Environment Variables ของ Render
TOKEN = os.getenv("DISCORD_TOKEN")

# ค่าคงที่
DEFAULT_RANGE = 10
# เก็บข้อมูล Config: {guild_id: {'category_id': int, 'start_channel_id': int}}
server_config = {}
# เก็บข้อมูล Link: {discord_id: "Xbox Gamertag"}
user_links = {}
# เก็บระยะ Range: {guild_id: int}
range_config = {}
# เก็บตำแหน่งผู้เล่น Real-time: {gamertag: {'x': float, 'y': float, 'z': float}}
game_state = {}

# ตั้งค่า Intents
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.web_server = None

    async def setup_hook(self):
        # สร้าง Web Server เพื่อรับข้อมูลจาก Minecraft
        app = web.Application()
        app.router.add_post('/update_coords', self.handle_coords)
        app.router.add_get('/', self.handle_keep_alive) # Route สำหรับ UptimeRobot
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        # Render จะส่ง Port มาทาง Env ถ้าไม่มีใช้ 8080
        port = int(os.environ.get("PORT", 8080))
        self.web_server = web.TCPSite(runner, '0.0.0.0', port)
        await self.web_server.start()
        print(f"✅ API Server running on port {port}")
        
        await self.tree.sync()

    async def handle_keep_alive(self, request):
        return web.Response(text="Bot is running!")

    async def handle_coords(self, request):
        try:
            data = await request.json()
            # data structure: [{"name": "Steve", "x": 10, "y": 64, "z": 10}, ...]
            
            # อัปเดตตำแหน่งผู้เล่น
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            
            global game_state
            game_state = current_players
            
            # ส่งรายชื่อคนที่ยืนยันตัวตนแล้วกลับไปให้ Minecraft
            verified_names = []
            for d_id, xbox in user_links.items():
                if xbox in game_state:
                    verified_names.append(xbox)
            
            # รัน Logic ย้ายห้อง
            await process_voice_logic()
            
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- UI COMPONENTS ---

class LinkModal(ui.Modal, title='เชื่อมต่อ ID Minecraft'):
    xbox_name = ui.TextInput(label='ชื่อ Xbox Gamertag', placeholder='ใส่ชื่อตัวละครในเกมของคุณ (ตัวเล็กใหญ่ต้องเป๊ะ)')

    async def on_submit(self, interaction: discord.Interaction):
        gamertag = self.xbox_name.value.strip()
        user_links[interaction.user.id] = gamertag
        
        config = server_config.get(interaction.guild_id)
        msg = f"✅ ยืนยันเรียบร้อย! บอทจดจำว่าคุณคือ **{gamertag}**"
        
        if config:
            chan = interaction.guild.get_channel(config['start_channel_id'])
            if chan:
                msg += f"\n👉 กรุณาไปรอที่ห้อง {chan.mention} แล้วเข้าเกมได้เลย"

        await interaction.response.send_message(msg, ephemeral=True)

class SetupView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="เชื่อมต่อ (Connect)", style=discord.ButtonStyle.green, custom_id="mc_link_btn")
    async def link_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(LinkModal())

    @ui.button(label="แก้ไขชื่อ (Edit Name)", style=discord.ButtonStyle.gray, custom_id="mc_edit_btn")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(LinkModal())

# --- SLASH COMMANDS ---

@bot.tree.command(name="setup", description="ตั้งค่าระบบ Voice Chat (Admin only)")
@app_commands.describe(category="หมวดหมู่ Vc", start_channel="ห้อง Lobby เริ่มต้น")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel):
    # Check Permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณต้องเป็น Admin เพื่อใช้คำสั่งนี้", ephemeral=True)
        return

    # 1. ตรวจสอบว่า Category มีแต่ Voice Channel
    for channel in category.channels:
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(f"❌ ผิดพลาด: ในหมวดหมู่ '{category.name}' มีช่องที่ไม่ใช่ Voice Channel ({channel.name})", ephemeral=True)
            return

    # 2. ตรวจสอบว่า Start Channel อยู่ใน Category นั้น
    if start_channel.category_id != category.id:
        await interaction.response.send_message("❌ ผิดพลาด: ห้อง Start Channel ต้องอยู่ภายในหมวดหมู่ที่เลือก", ephemeral=True)
        return

    # Save Config
    server_config[interaction.guild_id] = {
        'category_id': category.id,
        'start_channel_id': start_channel.id
    }
    if interaction.guild_id not in range_config:
        range_config[interaction.guild_id] = DEFAULT_RANGE

    embed = discord.Embed(
        title="Voice Chat Minecraft PE",
        description=(
            "**ระบบเชื่อมต่อไมค์ตามระยะทาง**\n\n"
            "🟢 **ขั้นตอนการใช้งาน:**\n"
            "1. กดปุ่ม **เชื่อมต่อ** ด้านล่าง\n"
            "2. กรอกชื่อ **Xbox Gamertag** ของคุณให้ถูกต้อง\n"
            "3. เข้าไปรอในห้อง **Start Channel** ที่กำหนด\n"
            "4. เข้าเกม Minecraft ระบบจะดึงคุณไปห้องส่วนตัวเมื่อเจอเพื่อน!"
        ),
        color=0x2ecc71
    )
    embed.set_footer(text="Bot Status: Online & Ready")
    
    await interaction.channel.send(embed=embed, view=SetupView())
    await interaction.response.send_message("✅ ตั้งค่าระบบเรียบร้อยแล้ว", ephemeral=True)

@bot.tree.command(name="range", description="ตั้งค่าระยะการได้ยิน (Blocks)")
async def set_rage(interaction: discord.Interaction, distance: int):
    # ชื่อคำสั่ง /rage ตามที่ขอ (แต่ในโค้ดใช้ set_rage func)
    range_config[interaction.guild_id] = distance
    await interaction.response.send_message(f"🔊 ตั้งค่าระยะ Proximity เป็น **{distance}** บล็อก", ephemeral=True)

# --- PROXIMITY LOGIC ---

async def process_voice_logic():
    for guild_id, config in server_config.items():
        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        category = guild.get_channel(config['category_id'])
        start_channel = guild.get_channel(config['start_channel_id'])
        if not category or not start_channel: continue

        dist_limit = range_config.get(guild_id, DEFAULT_RANGE)
        
        # 1. หาคนใน Discord ที่ Link แล้ว และอยู่ใน Category นี้
        online_users = [] # List of (Member, x, y, z)
        
        for member_id, xbox_name in user_links.items():
            member = guild.get_member(member_id)
            if not member or not member.voice or not member.voice.channel:
                continue
            
            # ต้องอยู่ในหมวดหมู่ Vc ที่เราคุมเท่านั้น
            if member.voice.channel.category_id == category.id:
                if xbox_name in game_state:
                    pos = game_state[xbox_name]
                    online_users.append((member, pos['x'], pos['y'], pos['z']))
                else:
                    # Link แล้ว แต่อไม่อยู่ในเกม (หรือหลุด) -> ดีดกลับ Start Channel
                    if member.voice.channel.id != start_channel.id:
                        try: await member.move_to(start_channel)
                        except: pass

        # 2. คำนวณกลุ่ม (Simple Clustering)
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
                # ระยะทาง 3D
                d = math.sqrt((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2)
                
                if d <= dist_limit:
                    current_group.append(mem2)
                    processed_indices.add(j)
            
            groups.append(current_group)

        # 3. ย้ายคนเข้าห้อง (เรียงห้องจากน้อยไปหามาก ตามที่ขอ)
        # กรองเอาเฉพาะห้องที่ไม่ใช่ Start Channel มาใช้เป็นห้องคุย
        voice_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel) and c.id != start_channel.id]
        # เรียงตามลำดับ position (บนลงล่าง)
        voice_channels.sort(key=lambda c: c.position)

        for idx, group in enumerate(groups):
            if idx < len(voice_channels):
                target_vc = voice_channels[idx]
                for member in group:
                    # ย้ายเฉพาะคนที่ยังไม่อยู่ห้องนั้น
                    if member.voice.channel.id != target_vc.id:
                        try: 
                            await member.move_to(target_vc)
                            await asyncio.sleep(0.1) # กัน Rate Limit
                        except Exception as e:
                            print(f"Move Error: {e}")
            else:
                # ห้องเต็ม - ปล่อยไว้อยู่ห้องเดิม หรือดีดไป Start Channel ก็ได้ (ในที่นี้ปล่อยไว้)
                pass

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: ไม่พบ DISCORD_TOKEN ใน Environment Variables")
