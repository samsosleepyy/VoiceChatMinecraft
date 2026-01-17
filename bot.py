import discord
from discord import app_commands, ui
from discord.ext import commands
import math
import asyncio
from aiohttp import web
import os

# --- CONFIGURATION ---
TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_RANGE = 10

# Data Stores
server_config = {}
user_links = {}
range_config = {}
game_state = {}

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.web_server = None

    async def setup_hook(self):
        app = web.Application()
        app.router.add_post('/update_coords', self.handle_coords)
        app.router.add_get('/', self.handle_keep_alive)
        runner = web.AppRunner(app)
        await runner.setup()
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
            current_players = {}
            for p in data:
                current_players[p['name']] = {'x': p['x'], 'y': p['y'], 'z': p['z']}
            
            global game_state
            game_state = current_players
            
            verified_names = []
            for d_id, xbox in user_links.items():
                if xbox in game_state:
                    verified_names.append(xbox)
            
            await process_voice_logic()
            return web.json_response({'status': 'ok', 'verified': verified_names})
        except Exception as e:
            print(f"API Error: {e}")
            return web.json_response({'status': 'error'}, status=500)

bot = MyBot()

# --- UI CLASSES ---
class LinkModal(ui.Modal, title='เชื่อมต่อ ID Minecraft'):
    xbox_name = ui.TextInput(label='ชื่อ Xbox Gamertag', placeholder='ชื่อตัวละคร (ตัวเล็กใหญ่ต้องตรง)')
    async def on_submit(self, interaction: discord.Interaction):
        gamertag = self.xbox_name.value.strip()
        user_links[interaction.user.id] = gamertag
        config = server_config.get(interaction.guild_id)
        msg = f"✅ ยืนยันเรียบร้อย! คุณคือ **{gamertag}**"
        if config:
            chan = interaction.guild.get_channel(config['start_channel_id'])
            if chan: msg += f"\n👉 ไปรอที่ห้อง {chan.mention} ได้เลย"
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

# --- COMMANDS ---
@bot.tree.command(name="setup", description="ตั้งค่าระบบ Voice Chat (Admin only)")
async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, start_channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only", ephemeral=True)
    if start_channel.category_id != category.id:
        return await interaction.response.send_message("❌ Start Channel ต้องอยู่ในหมวดหมู่ที่เลือก", ephemeral=True)

    server_config[interaction.guild_id] = {'category_id': category.id, 'start_channel_id': start_channel.id}
    if interaction.guild_id not in range_config: range_config[interaction.guild_id] = DEFAULT_RANGE

    embed = discord.Embed(title="Voice Chat Minecraft PE", description="กดปุ่มด้านล่างเพื่อเชื่อมต่อ", color=0x2ecc71)
    await interaction.channel.send(embed=embed, view=SetupView())
    await interaction.response.send_message("✅ ตั้งค่าเรียบร้อย", ephemeral=True)

@bot.tree.command(name="range", description="ตั้งค่าระยะการได้ยิน (Blocks)")
async def set_rage(interaction: discord.Interaction, distance: int):
    range_config[interaction.guild_id] = distance
    await interaction.response.send_message(f"🔊 ตั้งค่าระยะเป็น **{distance}** บล็อก", ephemeral=True)

# --- 🔴 UPDATED LOGIC (MOVE FEW TO MANY) 🔴 ---
async def process_voice_logic():
    for guild_id, config in server_config.items():
        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        category = guild.get_channel(config['category_id'])
        start_channel = guild.get_channel(config['start_channel_id'])
        if not category or not start_channel: continue

        dist_limit = range_config.get(guild_id, DEFAULT_RANGE)
        
        # 1. เตรียมข้อมูลผู้เล่น
        online_users = [] 
        for member_id, xbox_name in user_links.items():
            member = guild.get_member(member_id)
            if not member or not member.voice or not member.voice.channel: continue
            
            # ผู้เล่นต้องอยู่ใน Category นี้เท่านั้น
            if member.voice.channel.category_id == category.id:
                if xbox_name in game_state:
                    pos = game_state[xbox_name]
                    online_users.append((member, pos['x'], pos['y'], pos['z']))
                else:
                    # ถ้า Link แล้วแต่ไม่อยู่ในเกม ให้ดีดกลับ Lobby
                    if member.voice.channel.id != start_channel.id:
                        try: await member.move_to(start_channel)
                        except: pass

        # 2. จัดกลุ่มผู้เล่น (Clustering)
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

        # 3. การจัดการห้อง (Logic: Few to Many)
        # ดึงรายชื่อห้องว่างที่ใช้ได้ (ไม่รวม Start Channel)
        available_game_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel) and c.id != start_channel.id]
        
        # เก็บว่าห้องไหนถูกจองไปแล้วในรอบ Loop นี้ เพื่อไม่ให้กลุ่มอื่นมาทับ
        taken_channels = set()

        for group in groups:
            target_channel = None
            
            # Step A: เช็คเสียงข้างมาก (Majority Vote)
            # ดูว่าสมาชิกในกลุ่มนี้ ส่วนใหญ่นั่งอยู่ที่ห้องไหน (ไม่นับ Start Channel)
            channel_votes = {}
            for member in group:
                curr_chan = member.voice.channel
                if curr_chan.id != start_channel.id and curr_chan.id not in taken_channels:
                    channel_votes[curr_chan] = channel_votes.get(curr_chan, 0) + 1
            
            # ถ้ามีคนอยู่ในห้องเกมอยู่แล้ว ให้เลือกห้องที่มีคนเยอะที่สุด
            if channel_votes:
                # เลือกห้องที่มีคะแนนโหวตสูงสุด
                target_channel = max(channel_votes, key=channel_votes.get)
            
            # Step B: ถ้าทุกคนอยู่ใน Start Channel หรือห้องที่อยู่ดันไม่ว่าง
            # ให้หาห้องว่างใหม่
            if not target_channel:
                for chan in available_game_channels:
                    # เช็คว่าห้องนี้ว่างจริงๆ (ไม่มีคนนอกกลุ่ม) และยังไม่ถูกจองในรอบนี้
                    if len(chan.members) == 0 and chan.id not in taken_channels:
                        target_channel = chan
                        break
            
            # ถ้าห้องเต็มจริงๆ ก็ข้ามไป (หรือจะให้ไปรวมห้องสุดท้ายก็ได้)
            if not target_channel:
                continue

            # จองห้องนี้ไว้
            taken_channels.add(target_channel.id)

            # Step C: ย้ายสมาชิก (ย้ายเฉพาะคนที่ไม่ได้อยู่ที่นั่น)
            for member in group:
                if member.voice.channel.id != target_channel.id:
                    try:
                        await member.move_to(target_channel)
                        # ใส่ delay นิดหน่อยป้องกัน Rate Limit
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        print(f"Move Error: {e}")

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN not found")
