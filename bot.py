import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io
import json
import logging
from pathlib import Path
import asyncio
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('WatermarkBot')

class WatermarkBot(commands.Bot):
    def __init__(self, config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        
        super().__init__(command_prefix=config['prefix'], intents=intents)
        self.config = config
        self.watermark_path = Path(config['watermark_path'])
        
        if not self.watermark_path.exists():
            raise FileNotFoundError(f"Watermark image nahi mila: {self.watermark_path}")
    
    async def setup_hook(self):
        logger.info("Bot setup ho raha hai...")
    
    async def on_ready(self):
        logger.info(f'{self.user} successfully login ho gaya!')
        logger.info(f'Bot ID: {self.user.id}')
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.streaming, 
                name="MADE BY SUBHAN"
            )
        )
    
    async def on_message(self, message):
        # Sirf apni bot ki messages ignore karo (dusre bots, webhooks, members sab allow)
        if message.author.id == self.user.id:
            return
        
        # Check karo ki monitored channel hai ya nahi
        if message.channel.id not in self.config['monitored_channels']:
            return
        
        # Check karo ki message mein attachments hain
        if not message.attachments:
            return
        
        # Har attachment ko process karo
        for attachment in message.attachments:
            if self.is_image(attachment.filename):
                try:
                    await self.process_image(message, attachment)
                except Exception as e:
                    logger.error(f"Image process karte waqt error: {e}", exc_info=True)
                    if self.config.get('send_error_messages', False):
                        await message.channel.send(
                            f"❌ Image process karte waqt error aaya: {attachment.filename}"
                        )
    
    def is_image(self, filename):
        """Check karo ki file image hai ya nahi"""
        image_extensions = self.config.get('allowed_extensions', ['.png', '.jpg', '.jpeg', '.gif', '.webp'])
        return any(filename.lower().endswith(ext) for ext in image_extensions)
    
    async def process_image(self, message, attachment):
        """Image download karo, watermark lagao aur repost karo"""
        
        # Check karo ki message kis type ka hai
        if message.author.bot:
            author_type = "Bot"
        elif message.webhook_id:
            author_type = "Webhook"
        else:
            author_type = "User"
        
        logger.info(f"Processing image: {attachment.filename} from {author_type}: {message.author}")
        
        try:
            # Image download karo
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        logger.error(f"Image download fail: Status {resp.status}")
                        return
                    image_data = await resp.read()
            
            # Watermark lagao
            watermarked_image = await self.add_watermark(image_data, attachment.filename)
            
            if watermarked_image is None:
                logger.error("Watermark apply nahi ho saka")
                return
            
            # Simple embed with just "WARRIOR ON TOP"
            embed = discord.Embed(
                description="**WARRIOR ON TOP**",
                color=discord.Color.from_str(self.config.get('embed_color', '#5865F2'))
            )
            
            # File object banao
            file = discord.File(
                fp=watermarked_image,
                filename=f"watermarked_{attachment.filename}"
            )
            
            # Repost karo
            await message.channel.send(file=file, embed=embed)
            logger.info(f"Successfully reposted: {attachment.filename} from {author_type}")
            
            # Original message delete karo agar permission hai
            if self.config.get('delete_original', True):
                try:
                    await asyncio.sleep(self.config.get('delete_delay', 0.5))
                    await message.delete()
                    logger.info(f"Original message deleted from {author_type}: {message.author}")
                except discord.Forbidden:
                    logger.warning("Original message delete karne ki permission nahi hai")
                except discord.NotFound:
                    logger.warning("Original message pehle se delete ho chuki hai")
                except discord.HTTPException as e:
                    logger.warning(f"Message delete karne mein error: {e}")
        
        except Exception as e:
            logger.error(f"Process image error: {e}", exc_info=True)
            raise
    
    async def add_watermark(self, image_data, filename):
        """Image pe watermark lagao"""
        try:
            # Original image load karo
            original_img = Image.open(io.BytesIO(image_data))
            
            # RGBA mode mein convert karo transparency ke liye
            if original_img.mode != 'RGBA':
                original_img = original_img.convert('RGBA')
            
            # Watermark load karo
            watermark = Image.open(self.watermark_path)
            if watermark.mode != 'RGBA':
                watermark = watermark.convert('RGBA')
            
            # Watermark ko resize karo config ke according
            wm_size_percent = self.config.get('watermark_size_percent', 20)
            wm_width = int(original_img.width * wm_size_percent / 100)
            wm_height = int(watermark.height * (wm_width / watermark.width))
            watermark = watermark.resize((wm_width, wm_height), Image.Resampling.LANCZOS)
            
            # Watermark ki transparency set karo
            opacity = self.config.get('watermark_opacity', 0.7)
            watermark_alpha = watermark.split()[3]
            watermark_alpha = watermark_alpha.point(lambda p: int(p * opacity))
            watermark.putalpha(watermark_alpha)
            
            # Position calculate karo
            position = self.config.get('watermark_position', 'bottom-right')
            padding = self.config.get('watermark_padding', 20)
            
            pos_map = {
                'top-left': (padding, padding),
                'top-right': (original_img.width - wm_width - padding, padding),
                'bottom-left': (padding, original_img.height - wm_height - padding),
                'bottom-right': (original_img.width - wm_width - padding, 
                                original_img.height - wm_height - padding),
                'center': ((original_img.width - wm_width) // 2, 
                          (original_img.height - wm_height) // 2)
            }
            
            paste_position = pos_map.get(position, pos_map['bottom-right'])
            
            # Watermark paste karo
            original_img.paste(watermark, paste_position, watermark)
            
            # Convert back to RGB if needed
            if filename.lower().endswith(('.jpg', '.jpeg')):
                original_img = original_img.convert('RGB')
            
            # BytesIO mein save karo
            output = io.BytesIO()
            img_format = 'PNG' if filename.lower().endswith('.png') else 'JPEG'
            original_img.save(output, format=img_format, quality=self.config.get('image_quality', 95))
            output.seek(0)
            
            return output
        
        except Exception as e:
            logger.error(f"Watermark apply karne mein error: {e}", exc_info=True)
            return None


def load_config():
    """Config file load karo"""
    config_path = Path('config.json')
    
    if not config_path.exists():
        logger.error("config.json file nahi mili!")
        raise FileNotFoundError("config.json file required hai")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Token ab .env se aayega, isliye config mein token ki zaroorat nahi
        required_fields = ['monitored_channels', 'watermark_path']
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Config mein '{field}' field missing hai")
        
        # Agar config mein token hai bhi to ignore karo, warning do
        if 'token' in config:
            logger.warning("⚠️ config.json mein token field hai, lekin ab .env file use hogi. config.json se token hata do.")
        
        logger.info("Config successfully load ho gaya")
        return config
    
    except json.JSONDecodeError as e:
        logger.error(f"Config file parse karne mein error: {e}")
        raise
    except Exception as e:
        logger.error(f"Config load karne mein error: {e}")
        raise


def main():
    """Main function"""
    try:
        # Check karo ki .env file mein token hai ya nahi
        token = os.getenv('DISCORD_TOKEN')
        if not token:
            logger.error("❌ DISCORD_TOKEN .env file mein nahi mila!")
            logger.error("Please .env file banao aur usme DISCORD_TOKEN=your_token_here likho")
            return
        
        # Config load karo
        config = load_config()
        
        # Bot initialize karo
        bot = WatermarkBot(config)
        
        # Bot run karo
        logger.info("Bot start ho raha hai...")
        bot.run(token)
    
    except FileNotFoundError as e:
        logger.error(f"File nahi mili: {e}")
    except Exception as e:
        logger.error(f"Bot start karne mein error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
