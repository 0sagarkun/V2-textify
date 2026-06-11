from io import BytesIO
import zipfile
import time
import asyncio
import logging
from datetime import datetime
from PIL import Image
from fpdf import FPDF
import img2pdf
from gtts import gTTS
import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

# ============= CONFIGURATION =============
TOKEN = "8712164558:AAEQfE8UAaJDa3_5fGGhWzSZ6tAfcUK7gWM"
OWNER = "@sagarkun0"
OWNER_ID = 8033719088

# Setup logging for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============= GLOBAL STATE =============
users = set()
start_time = time.time()
user_sessions = {}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit
MAX_TEXT_LENGTH = 10000  # characters

# Conversation states
(SELECTING_FORMAT, COLLECTING_TEXT, ASKING_FILENAME, WAITING_CUSTOM_EXT,
 WAITING_TTS_TEXT, WAITING_CONVERT_FILE, WAITING_CONVERT_TARGET,
 WAITING_QR_TEXT, WAITING_FOR_ZIP_FILES, WAITING_NEW_NAME) = range(10)

# ============= FORMATS & KEYBOARDS =============
FORMATS = [
    ("📄 txt", "txt"), ("📄 pdf", "pdf"), ("🐍 py", "py"),
    ("🌐 html", "html"), ("🎨 css", "css"),
    ("📦 json", "json"), ("📜 js", "js"),
    ("📄 xml", "xml"), ("📊 csv", "csv"),
    ("⚙️ yaml", "yaml"), ("🐘 php", "php"),
    ("💻 sh", "sh"), ("📘 md", "md"),
    ("🖼 svg", "svg"), ("🖼 png", "png"),
    ("📸 jpg", "jpg"), ("🌐 webp", "webp"),
    ("🎨 Custom", "custom")
]

def get_format_keyboard():
    """Generate format selection keyboard"""
    keyboard = []
    for i in range(0, len(FORMATS), 2):
        row = [InlineKeyboardButton(FORMATS[i][0], callback_data=FORMATS[i][1])]
        if i+1 < len(FORMATS):
            row.append(InlineKeyboardButton(FORMATS[i+1][0], callback_data=FORMATS[i+1][1]))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_main_menu():
    """Get main menu keyboard"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Create File", callback_data="select_format")],
        [InlineKeyboardButton("🔧 More Tools", callback_data="more_command"),
         InlineKeyboardButton("🎤 Text to Speech", callback_data="tts_command")]
    ])

def get_more_menu():
    """Get more options menu"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 ZIP Creator", callback_data="more_zip")],
        [InlineKeyboardButton("✏️ Rename File", callback_data="more_rename")],
        [InlineKeyboardButton("🔄 Convert Format", callback_data="more_converter")],
        [InlineKeyboardButton("🎯 QR Code Generator", callback_data="more_qr")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]])

def get_filename_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Skip", callback_data="skip_filename"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

# ============= HELPER FUNCTIONS =============
def get_user_count(user_id):
    """Get file count for user"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {"file_count": 0}
    return user_sessions[user_id].get("file_count", 0)

def increment_count(user_id):
    """Increment user's file counter"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {"file_count": 0}
    user_sessions[user_id]["file_count"] += 1

def auto_filename(user, ext, user_id):
    """Generate automatic filename"""
    try:
        first = user.first_name or "User"
        last = user.last_name or ""
        name = f"{first}_{last}".strip("_") or "file"
        count = get_user_count(user_id) + 1
        return f"{name}_{count}.{ext}"
    except Exception as e:
        logger.error(f"Filename generation error: {e}")
        return f"file_{int(time.time())}.{ext}"

def text_to_file(content, ext):
    """Convert text to file (txt, pdf, or code)"""
    bio = BytesIO()
    try:
        if len(content) > MAX_TEXT_LENGTH:
            raise ValueError(f"Text too long (max {MAX_TEXT_LENGTH} chars)")
        
        if ext == "pdf":
            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.set_font("helvetica", size=11)
            
            # Split into lines and handle encoding
            for line in content.split("\n"):
                try:
                    clean_line = line.encode('latin-1', errors='ignore').decode('latin-1')
                    pdf.multi_cell(0, 10, clean_line)
                except:
                    pdf.multi_cell(0, 10, "[Unable to render text]")
            
            pdf_bytes = pdf.output(dest='S')
            if isinstance(pdf_bytes, str):
                bio.write(pdf_bytes.encode('latin1'))
            else:
                bio.write(pdf_bytes)
        else:
            bio.write(content.encode("utf-8"))
        
        bio.seek(0)
        return bio
    except Exception as e:
        logger.error(f"Text to file conversion error: {e}")
        raise

def image_to_bytes(img_bytes, target):
    """Convert image between formats"""
    try:
        img = Image.open(BytesIO(img_bytes))
        out = BytesIO()
        
        if target == "jpg":
            img = img.convert("RGB")
            img.save(out, "JPEG", quality=90, optimize=True)
        elif target == "webp":
            img.save(out, "WEBP", quality=85, method=6)
        elif target == "png":
            img.save(out, "PNG", optimize=True)
        else:
            img.save(out, "PNG")
        
        out.seek(0)
        return out
    except Exception as e:
        logger.error(f"Image conversion error: {e}")
        raise

def cleanup_session(user_id):
    """Clean up user session data"""
    if user_id in user_sessions:
        user_sessions[user_id] = {"file_count": get_user_count(user_id)}

# ============= ERROR HANDLING =============
async def error_handler(update, context):
    """Global error handler"""
    logger.error(f"Update {update} caused error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong! Try again or contact @sagarkun0"
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# ============= MAIN COMMANDS =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    users.add(user_id)
    
    msg = (
        "✨ *File Maker Bot* ✨\n\n"
        "🚀 Fast • Reliable • Feature-Rich\n\n"
        "📝 Send text → Get file (txt, pdf, py, etc.)\n"
        "🖼️ Send image → Convert format\n"
        "🎙️ Create speech from text\n"
        "📦 Batch files & ZIP them\n"
        "🎯 Generate QR codes\n\n"
        "👇 Choose an option:"
    )
    
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu())

async def select_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select file format"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📁 *Select File Format*\n\nChoose the format you want:",
        parse_mode="Markdown",
        reply_markup=get_format_keyboard()
    )
    return SELECTING_FORMAT

async def format_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle format selection"""
    query = update.callback_query
    await query.answer()
    
    ext = query.data
    uid = query.from_user.id
    
    if uid not in user_sessions:
        user_sessions[uid] = {}
    
    user_sessions[uid]["format"] = ext
    user_sessions[uid]["content"] = ""
    
    await query.edit_message_text(
        f"✅ Format: `{ext.upper()}`\n\n📝 Send your text content.\n(Max {MAX_TEXT_LENGTH} characters)",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data="done_text")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
    )
    return COLLECTING_TEXT

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input"""
    uid = update.effective_user.id
    
    if uid not in user_sessions or "format" not in user_sessions[uid]:
        await update.message.reply_text("Start with /start first.")
        return
    
    text = update.message.text
    session = user_sessions[uid]
    
    # Check length
    total_len = len(session.get("content", "")) + len(text) + 1
    if total_len > MAX_TEXT_LENGTH:
        await update.message.reply_text(
            f"⚠️ Text too long! Max {MAX_TEXT_LENGTH} chars. Current: {total_len} chars"
        )
        return COLLECTING_TEXT
    
    session["content"] += text + "\n"
    await update.message.reply_text("✅ Text added! Send more or press Done.")
    return COLLECTING_TEXT

async def done_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle done button"""
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    session = user_sessions.get(uid, {})
    
    if not session.get("content"):
        await query.answer("No content! Send some text first.", show_alert=True)
        return COLLECTING_TEXT
    
    await query.edit_message_text(
        "✏️ *Filename* (optional)\n\nEnter desired filename or skip for auto-naming.",
        parse_mode="Markdown",
        reply_markup=get_filename_keyboard()
    )
    return ASKING_FILENAME

async def receive_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom filename"""
    uid = update.effective_user.id
    session = user_sessions.get(uid)
    
    if not session:
        await update.message.reply_text("Error: Session expired. Start with /start")
        return
    
    session["custom_filename"] = update.message.text.strip()
    await generate_file(update, uid, session)

async def skip_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip filename and use auto-generated"""
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    session = user_sessions.get(uid)
    
    if not session:
        await query.answer("Error: Session expired", show_alert=True)
        return ConversationHandler.END
    
    # Create temp update object for generate_file
    class TempUpdate:
        def __init__(self, msg):
            self.effective_message = msg
    
    session["custom_filename"] = None
    temp_update = TempUpdate(query.message)
    await generate_file(temp_update, uid, session)

async def generate_file(update, uid, session):
    """Generate and send file"""
    try:
        ext = session.get("format", "txt")
        content = session.get("content", "").strip()
        
        if not content:
            await update.effective_message.reply_text("❌ No content to save!")
            return ConversationHandler.END
        
        # Generate filename
        user = update.effective_user if hasattr(update, 'effective_user') else None
        if session.get("custom_filename"):
            filename = f"{session['custom_filename']}.{ext}"
        else:
            filename = auto_filename(user, ext, uid) if user else f"file_{int(time.time())}.{ext}"
        
        # Send processing message
        msg = await update.effective_message.reply_text("⏳ Processing... Please wait")
        
        # Generate file
        file_io = text_to_file(content, ext)
        file_io.name = filename
        
        # Send file
        await update.effective_message.reply_document(
            document=file_io,
            filename=filename,
            caption=f"✅ File: `{filename}`\nSize: ~{len(content)} bytes",
            parse_mode="Markdown"
        )
        
        increment_count(uid)
        
        # Cleanup and offer next steps
        await update.effective_message.reply_text(
            "✨ Done! What next?",
            reply_markup=get_main_menu()
        )
        
        # Delete processing message
        try:
            await msg.delete()
        except:
            pass
        
        cleanup_session(uid)
        
    except Exception as e:
        logger.error(f"File generation error: {e}")
        await update.effective_message.reply_text(f"❌ Error: {str(e)[:100]}")
    finally:
        return ConversationHandler.END

# ============= TEXT TO SPEECH =============
async def tts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start TTS"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🎤 *Text to Speech*\n\nSend text (max 200 chars, English only)",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_tts")]])
    )
    return WAITING_TTS_TEXT

async def handle_tts_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate speech"""
    text = update.message.text.strip()
    
    if len(text) > 200:
        await update.message.reply_text("⚠️ Max 200 characters!")
        return WAITING_TTS_TEXT
    
    msg = await update.message.reply_text("🎧 Generating speech...")
    
    try:
        tts = gTTS(text, lang='en', slow=False)
        audio_io = BytesIO()
        tts.write_to_fp(audio_io)
        audio_io.seek(0)
        
        await update.message.reply_audio(
            audio=audio_io,
            filename="speech.mp3",
            caption="✅ Speech ready!"
        )
        
        await update.message.reply_text("🎉 What next?", reply_markup=get_main_menu())
        
    except Exception as e:
        logger.error(f"TTS error: {e}")
        await update.message.reply_text(f"❌ TTS failed: {str(e)[:80]}")
    finally:
        try:
            await msg.delete()
        except:
            pass
        cleanup_session(update.effective_user.id)
    
    return ConversationHandler.END

async def cancel_tts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel TTS"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= MORE OPTIONS =============
async def more_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show more options"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "🔧 *More Tools*\n\nChoose an option:",
            parse_mode="Markdown",
            reply_markup=get_more_menu()
        )
    else:
        # Called as /more command
        await update.message.reply_text(
            "🔧 *More Tools*\n\nChoose an option:",
            parse_mode="Markdown",
            reply_markup=get_more_menu()
        )

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to main menu"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✨ *File Maker Bot* ✨\n\nWhat would you like to do?",
        parse_mode="Markdown",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

# ============= QR CODE GENERATOR =============
async def start_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start QR generator"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {"mode": "qr"}
    
    await query.edit_message_text(
        "🎯 *QR Code Generator*\n\nSend text or URL (max 500 chars):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_qr")]])
    )
    return WAITING_QR_TEXT

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate QR code"""
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if len(text) > 500:
        await update.message.reply_text("⚠️ Max 500 characters!")
        return WAITING_QR_TEXT
    
    msg = await update.message.reply_text("⏳ Generating QR code...")
    
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img_io = BytesIO()
        img.save(img_io, "PNG")
        img_io.seek(0)
        
        await update.message.reply_photo(
            photo=img_io,
            caption=f"✅ QR Code ready!\n`{text[:50]}`...",
            parse_mode="Markdown"
        )
        
        await update.message.reply_text("🎉 What next?", reply_markup=get_main_menu())
        
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
    finally:
        try:
            await msg.delete()
        except:
            pass
        cleanup_session(uid)
    
    return ConversationHandler.END

async def cancel_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel QR"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cleanup_session(uid)
    await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= FILE CONVERTER =============
async def start_converter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start file converter"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {"mode": "convert"}
    
    await query.edit_message_text(
        "🔄 *Convert Image Format*\n\nSend an image file (jpg, png, webp):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_convert")]])
    )
    return WAITING_CONVERT_FILE

async def collect_file_for_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect file for conversion"""
    uid = update.effective_user.id
    session = user_sessions.get(uid, {})
    
    try:
        if update.message.document:
            file_obj = await update.message.document.get_file()
            filename = update.message.document.file_name
        elif update.message.photo:
            file_obj = await update.message.photo[-1].get_file()
            filename = "photo.jpg"
        else:
            await update.message.reply_text("Send an image or document!")
            return WAITING_CONVERT_FILE
        
        # Download file
        file_data = await file_obj.download_as_bytearray()
        
        if len(file_data) > MAX_FILE_SIZE:
            await update.message.reply_text(f"⚠️ File too large (max 50MB)")
            return WAITING_CONVERT_FILE
        
        session["convert_data"] = file_data
        session["convert_filename"] = filename
        
        # Show format options
        keyboard = [
            [InlineKeyboardButton("📸 JPG", callback_data="conv_jpg"),
             InlineKeyboardButton("🖼️ PNG", callback_data="conv_png")],
            [InlineKeyboardButton("🌐 WEBP", callback_data="conv_webp")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_convert")]
        ]
        
        await update.message.reply_text(
            "📁 Choose target format:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_CONVERT_TARGET
        
    except Exception as e:
        logger.error(f"File collection error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
        return WAITING_CONVERT_FILE

async def convert_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Convert file format"""
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    session = user_sessions.get(uid, {})
    target = query.data.replace("conv_", "")
    
    if "convert_data" not in session:
        await query.answer("Error: File expired", show_alert=True)
        return ConversationHandler.END
    
    msg = await query.message.reply_text("⏳ Converting...")
    
    try:
        file_data = session["convert_data"]
        original_name = session.get("convert_filename", "image")
        base_name = original_name.rsplit(".", 1)[0]
        new_filename = f"{base_name}.{target}"
        
        # Convert
        converted = image_to_bytes(file_data, target)
        converted.name = new_filename
        
        await query.message.reply_document(
            document=converted,
            filename=new_filename,
            caption=f"✅ Converted to {target.upper()}!"
        )
        
        await query.message.reply_text("🎉 What next?", reply_markup=get_main_menu())
        
    except Exception as e:
        logger.error(f"Conversion error: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)[:80]}")
    finally:
        try:
            await msg.delete()
        except:
            pass
        cleanup_session(uid)
    
    return ConversationHandler.END

async def cancel_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversion"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cleanup_session(uid)
    await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= ZIP CREATOR =============
async def start_zip_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start ZIP creation"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {"mode": "zip", "zip_files": []}
    
    await query.edit_message_text(
        "📦 *ZIP Creator*\n\nSend files one by one. Then use /donezip to create archive.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_zip")]])
    )
    return WAITING_FOR_ZIP_FILES

async def collect_zip_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect file for ZIP"""
    uid = update.effective_user.id
    session = user_sessions.get(uid, {})
    
    try:
        if update.message.document:
            file_obj = await update.message.document.get_file()
            filename = update.message.document.file_name
        elif update.message.photo:
            file_obj = await update.message.photo[-1].get_file()
            filename = f"photo_{len(session['zip_files'])}.jpg"
        else:
            await update.message.reply_text("Send a file or photo!")
            return WAITING_FOR_ZIP_FILES
        
        file_data = await file_obj.download_as_bytearray()
        
        if len(file_data) > MAX_FILE_SIZE:
            await update.message.reply_text(f"⚠️ File too large")
            return WAITING_FOR_ZIP_FILES
        
        session["zip_files"].append((filename, file_data))
        count = len(session["zip_files"])
        
        await update.message.reply_text(
            f"✅ File {count} added!\n\nSend more files or `/donezip` to finish.",
            parse_mode="Markdown"
        )
        return WAITING_FOR_ZIP_FILES
        
    except Exception as e:
        logger.error(f"ZIP file collection error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
        return WAITING_FOR_ZIP_FILES

async def finish_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish ZIP creation"""
    uid = update.effective_user.id
    session = user_sessions.get(uid, {})
    
    if not session.get("zip_files"):
        await update.message.reply_text("❌ No files added!")
        return ConversationHandler.END
    
    msg = await update.message.reply_text("⏳ Creating ZIP...")
    
    try:
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename, data in session["zip_files"]:
                zf.writestr(filename, data)
        
        zip_buffer.seek(0)
        zip_buffer.name = f"archive_{uid}.zip"
        
        await update.message.reply_document(
            document=zip_buffer,
            filename=f"archive_{uid}.zip",
            caption=f"✅ ZIP with {len(session['zip_files'])} files created!"
        )
        
        await update.message.reply_text("🎉 What next?", reply_markup=get_main_menu())
        
    except Exception as e:
        logger.error(f"ZIP creation error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
    finally:
        try:
            await msg.delete()
        except:
            pass
        cleanup_session(uid)
    
    return ConversationHandler.END

async def cancel_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel ZIP"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cleanup_session(uid)
    await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= RENAME FILE =============
async def start_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start rename"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {"mode": "rename"}
    
    await query.edit_message_text(
        "✏️ *Rename File*\n\nSend a file first:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_rename")]])
    )
    return WAITING_NEW_NAME

async def collect_file_for_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect file for renaming"""
    uid = update.effective_user.id
    session = user_sessions.get(uid, {})
    
    try:
        if update.message.document:
            file_obj = await update.message.document.get_file()
            filename = update.message.document.file_name
            ext = filename.split(".")[-1] if "." in filename else "bin"
        elif update.message.photo:
            file_obj = await update.message.photo[-1].get_file()
            ext = "jpg"
            filename = "photo.jpg"
        else:
            await update.message.reply_text("Send a file!")
            return WAITING_NEW_NAME
        
        file_data = await file_obj.download_as_bytearray()
        session["rename_data"] = file_data
        session["rename_ext"] = ext
        
        await update.message.reply_text(
            "📝 Send new filename (without extension):\n`Example: my_file`",
            parse_mode="Markdown"
        )
        return WAITING_NEW_NAME
        
    except Exception as e:
        logger.error(f"Rename file collection error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
        return WAITING_NEW_NAME

async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new filename"""
    uid = update.effective_user.id
    session = user_sessions.get(uid, {})
    
    if "rename_data" not in session:
        await update.message.reply_text("No file! Start again.")
        return ConversationHandler.END
    
    new_name = update.message.text.strip()
    ext = session.get("rename_ext", "bin")
    final = f"{new_name}.{ext}" if ext else new_name
    
    try:
        file_io = BytesIO(session["rename_data"])
        file_io.name = final
        
        await update.message.reply_document(
            document=file_io,
            filename=final,
            caption=f"✅ Renamed to: `{final}`",
            parse_mode="Markdown"
        )
        
        await update.message.reply_text("🎉 What next?", reply_markup=get_main_menu())
        
    except Exception as e:
        logger.error(f"Rename error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:80]}")
    finally:
        cleanup_session(uid)
    
    return ConversationHandler.END

async def cancel_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel rename"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cleanup_session(uid)
    await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= ADMIN =============
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Unauthorized!")
        return
    
    total_users = len(users)
    active = len([u for u in user_sessions if user_sessions[u].get("file_count", 0) > 0])
    uptime = int(time.time() - start_time)
    days, remainder = divmod(uptime, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    
    await update.message.reply_text(
        f"📊 *Bot Stats*\n\n"
        f"👥 Total users: `{total_users}`\n"
        f"⚡ Active sessions: `{len(user_sessions)}`\n"
        f"📈 Files created: `{sum(u.get('file_count', 0) for u in user_sessions.values())}`\n"
        f"⏱️ Uptime: `{days}d {hours}h {minutes}m`\n\n"
        f"✅ Bot is running smoothly!",
        parse_mode="Markdown"
    )

# ============= CANCEL HANDLER =============
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel operation"""
    query = update.callback_query
    if query:
        await query.answer()
        uid = query.from_user.id
        cleanup_session(uid)
        await query.edit_message_text("❌ Cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ============= MAIN =============
def main():
    """Start bot"""
    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(error_handler)
    
    # Main conversation
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(select_format, pattern="^select_format$"),
            CallbackQueryHandler(more_command, pattern="^more_command$"),
            CallbackQueryHandler(tts_command, pattern="^tts_command$"),
            CommandHandler("more", more_command),
        ],
        states={
            SELECTING_FORMAT: [
                CallbackQueryHandler(format_chosen, pattern="^(txt|pdf|py|html|css|json|js|xml|csv|yaml|php|sh|md|svg|png|jpg|webp|custom)$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            COLLECTING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(done_text, pattern="^done_text$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ASKING_FILENAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_filename),
                CallbackQueryHandler(skip_filename, pattern="^skip_filename$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAITING_TTS_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tts_text),
                CallbackQueryHandler(cancel_tts, pattern="^cancel_tts$"),
            ],
            WAITING_CONVERT_FILE: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, collect_file_for_convert),
                CallbackQueryHandler(cancel_convert, pattern="^cancel_convert$"),
            ],
            WAITING_CONVERT_TARGET: [
                CallbackQueryHandler(convert_file, pattern="^conv_(jpg|png|webp)$"),
                CallbackQueryHandler(cancel_convert, pattern="^cancel_convert$"),
            ],
            WAITING_QR_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, generate_qr),
                CallbackQueryHandler(cancel_qr, pattern="^cancel_qr$"),
            ],
            WAITING_FOR_ZIP_FILES: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, collect_zip_file),
                CommandHandler("donezip", finish_zip),
                CallbackQueryHandler(cancel_zip, pattern="^cancel_zip$"),
            ],
            WAITING_NEW_NAME: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, collect_file_for_rename),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_name),
                CallbackQueryHandler(cancel_rename, pattern="^cancel_rename$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
        ]
    )
    
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(start_zip_collection, pattern="^more_zip$"))
    app.add_handler(CallbackQueryHandler(start_rename, pattern="^more_rename$"))
    app.add_handler(CallbackQueryHandler(start_converter, pattern="^more_converter$"))
    app.add_handler(CallbackQueryHandler(start_qr, pattern="^more_qr$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CommandHandler("admin", admin_command))
    
    logger.info("✅ Bot starting... @sagarkun0")
    app.run_polling()

if __name__ == "__main__":
    main()
