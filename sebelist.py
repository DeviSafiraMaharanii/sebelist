import asyncio
import datetime
import sys
import os
import threading
import logging

from datetime import datetime, timedelta
from telethon import TelegramClient, events
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask

# === KONFIGURASI TELEGRAM ===
api_id = 24861776
api_hash = '16ac95328df27e7640ef7f995abfefb1'
client = TelegramClient("user_session_1", api_id, api_hash)

# === SETUP LOGGER ===
logging.basicConfig(filename='bot.log', level=logging.INFO,
                    format='[%(asctime)s] [%(levelname)s] %(message)s')

# === SCHEDULER ===
scheduler = AsyncIOScheduler()

# === DATA GLOBAL ===
blacklisted_groups = set()
job_data = {}
delay_setting = {}
MASA_AKTIF = datetime(2030, 12, 31)
delay_per_group_setting = {}  # Menyimpan delay per user (detik)
pesan_simpan = {}   # key: user_id, value: pesan terbaru
preset_pesan = {}   # key: user_id, value: {nama_preset: isi_pesan}
usage_stats = {}    # key: user_id, value: jumlah pesan yang berhasil dikirim
start_time = datetime.now()
TOTAL_SENT_MESSAGES = 0
JOBS = {}

HARI_MAPPING = {
    "senin": "monday", "selasa": "tuesday", "rabu": "wednesday",
    "kamis": "thursday", "jumat": "friday", "sabtu": "saturday", "minggu": "sunday"
}

def update_usage(user_id, count):
    global TOTAL_SENT_MESSAGES
    usage_stats[user_id] = usage_stats.get(user_id, 0) + count
    TOTAL_SENT_MESSAGES += count

# === FUNCTION UNTUK MELAKUKAN FORWARDING PESAN ===
async def forward_job(user_id, mode, source, message_id_or_text, jumlah_grup, durasi_jam: float, jumlah_pesan, delay_per_group: int = 0):
    start = datetime.now()
    end = start + timedelta(hours=durasi_jam)
    jeda_batch = delay_setting.get(user_id, 5)

    now = datetime.now()
    next_reset = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    harian_counter = 0  
    total_counter = 0  

    info_msg = f"[{now:%H:%M:%S}] 💖 Mulai meneruskan pesan selama {durasi_jam:.2f} jam."
    print(info_msg)
    logging.info(info_msg)
    try:
        await client.send_message(user_id, f"⏱💗 Sedang meneruskan pesan...\nDurasi: {durasi_jam:.2f} jam\nTarget harian: {jumlah_pesan} pesan.")
    except Exception as e:
        logging.error(f"💔 Error kirim notifikasi ke {user_id}: {e}")

    while datetime.now() < end:
        if datetime.now() >= next_reset:
            harian_counter = 0
            next_reset += timedelta(days=1)
            reset_msg = f"[{datetime.now():%H:%M:%S}] 💖 Reset harian: lanjut besok, sayang!"
            print(reset_msg)
            logging.info(reset_msg)

        counter = 0  
        async for dialog in client.iter_dialogs():
            if datetime.now() >= end or harian_counter >= jumlah_pesan:
                break
            if not dialog.is_group or dialog.name in blacklisted_groups:
                continue
            try:
                if mode == "forward":
                    msg = await client.get_messages(source, ids=int(message_id_or_text))
                    if msg:
                        await client.forward_messages(dialog.id, msg.id, from_peer=source)
                else:
                    await client.send_message(dialog.id, message_id_or_text, link_preview=True)

                counter += 1
                harian_counter += 1
                total_counter += 1
                update_usage(user_id, 1)

                log_msg = f"[{datetime.now():%H:%M:%S}] 💗✅ Dikirim ke grup: {dialog.name}"
                print(log_msg)
                logging.info(log_msg)

                # Delay antar grup (jika diset)
                if delay_per_group > 0:
                    await asyncio.sleep(delay_per_group)

                if counter >= jumlah_grup or harian_counter >= jumlah_pesan:
                    break

            except Exception as e:
                error_msg = f"[{datetime.now():%H:%M:%S}] 💔❌ Gagal kirim ke {dialog.name}: {e}"
                print(error_msg)
                logging.error(error_msg)
                continue

        if harian_counter >= jumlah_pesan:
            notif = f"🎯 Target harian {jumlah_pesan} pesan tercapai!\nBot lanjut lagi besok, tetap semangat ya!"
            info_notif = f"[{datetime.now():%H:%M:%S}] 💖 {notif}"
            print(info_notif)
            logging.info(info_notif)
            try:
                await client.send_message(user_id, notif)
            except Exception as e:
                logging.error(f"💔 Error kirim notifikasi ke {user_id}: {e}")
            sleep_seconds = (next_reset - datetime.now()).total_seconds()
            await asyncio.sleep(sleep_seconds)
        else:
            batch_msg = f"[{datetime.now():%H:%M:%S}] 💞 Batch {counter} grup selesai. Istirahat {jeda_batch} detik dulu ya..."
            print(batch_msg)
            logging.info(batch_msg)
            await asyncio.sleep(jeda_batch)

    selesai = f"✅ Forward selesai!\nTotal terkirim ke {total_counter} grup selama {durasi_jam:.2f} jam."
    selesai_msg = f"[{datetime.now():%H:%M:%S}] 💖 {selesai}"
    print(selesai_msg)
    logging.info(selesai_msg)
    try:
        await client.send_message(user_id, selesai)
    except Exception as e:
        logging.error(f"💔 Error kirim pesan selesai ke {user_id}: {e}")

# === PERINTAH BOT ===

@client.on(events.NewMessage(pattern='/scheduleforward'))
async def schedule_cmd(event):
    args = event.message.raw_text.split(maxsplit=2)
    if len(args) < 3:
        return await event.respond("❌ Format salah:\n/scheduleforward mode pesan/sumber jumlah_grup durasi jeda jumlah_pesan hari,jam jam:menit")
    try:
        mode = args[1]
        sisa = args[2].rsplit(" ", 6)
        if len(sisa) != 7:
            return await event.respond("❌ Format tidak sesuai. Pastikan argumen lengkap!")
        isi_pesan, jumlah, durasi, jeda, jumlah_pesan, hari_str, waktu = sisa
        jumlah = int(jumlah)
        durasi = int(durasi)
        jeda = int(jeda)
        jumlah_pesan = int(jumlah_pesan)
        jam, menit = map(int, waktu.split(":"))
        hari_list = [HARI_MAPPING.get(h.lower()) for h in hari_str.split(",")]

        if None in hari_list:
            return await event.respond("❌ Terdapat nama hari yang tidak valid. Gunakan: senin,selasa,...,minggu.")

        for hari_eng in hari_list:
            job_id = f"{event.sender_id}{hari_eng}{int(datetime.now().timestamp())}"
            job_data[job_id] = {
                "user": event.sender_id, "mode": mode, "source": "",
                "message": isi_pesan, "jumlah": jumlah,
                "durasi": durasi, "jeda": jeda, "jumlah_pesan": jumlah_pesan
            }
            delay_setting[event.sender_id] = jeda
            scheduler.add_job(
                forward_job,
                trigger=CronTrigger(day_of_week=hari_eng, hour=jam, minute=menit),
                args=[event.sender_id, mode, "", isi_pesan, jumlah, durasi, jumlah_pesan],
                id=job_id
            )

        daftar_hari = ", ".join([h.title() for h in hari_str.split(",")])
        await event.respond(f"💗 Jadwal forward berhasil ditambahkan untuk hari {daftar_hari} pukul {waktu}!")
    except Exception as e:
        err_msg = f"💔 Error: {e}"
        logging.error(err_msg)
        await event.respond(err_msg)

@client.on(events.NewMessage(pattern='/forward'))
async def forward_sekarang(event):
    args = event.message.raw_text.split(maxsplit=7)
    if len(args) < 7:
        return await event.respond("❌ Format salah:\n/forward mode sumber/id/isipesan jumlah_grup jeda durasi jumlah_pesan\nContoh:\n/forward forward @channel 5 12345 5 2 300\natau\n/forward text \"Halo semua!\" 10 5 3 300")
    try:
        mode = args[1]
        if mode == "forward":
            source = args[2]
            jumlah = int(args[3])
            message_id = int(args[4])
            jeda_batch = int(args[5])
            durasi = int(args[6])
            jumlah_pesan = int(args[7]) if len(args) >= 8 else 300
            delay_setting[event.sender_id] = jeda_batch
            await forward_job(event.sender_id, mode, source, message_id, jumlah, durasi, jumlah_pesan)
        elif mode == "text":
            text = args[2]
            jumlah = int(args[3])
            jeda_batch = int(args[4])
            durasi = int(args[5])
            jumlah_pesan = int(args[6]) if len(args) >= 7 else 300
            delay_setting[event.sender_id] = jeda_batch
            pesan_simpan[event.sender_id] = text
            await forward_job(event.sender_id, mode, "", text, jumlah, durasi, jumlah_pesan)
        else:
            await event.respond("❌ Mode harus 'forward' atau 'text'")
    except Exception as e:
        err_msg = f"💔 Error: {e}"
        logging.error(err_msg)
        await event.respond(err_msg)

@client.on(events.NewMessage(pattern='/setdelay'))
async def set_delay(event):
    try:
        delay = int(event.message.raw_text.split()[1])
        delay_setting[event.sender_id] = delay
        await event.respond(f"💗 Jeda antar batch diset ke {delay} detik!")
    except Exception as e:
        logging.error(f"💔 Error pada /setdelay: {e}")
        await event.respond("❌ Gunakan: /setdelay <detik>")

@client.on(events.NewMessage(pattern='/review'))
async def review_jobs(event):
    teks = "💗== Jadwal Aktif ==\n"
    if not job_data:
        teks += "Tidak ada jadwal."
    else:
        for job_id, info in job_data.items():
            teks += f"- ID: {job_id}\n  Mode: {info['mode']}\n  Grup: {info['jumlah']}\n  Durasi: {info['durasi']} jam\n"
    await event.respond(teks)

@client.on(events.NewMessage(pattern='/deletejob'))
async def delete_job(event):
    try:
        job_id = event.message.raw_text.split()[1]
        scheduler.remove_job(job_id)
        job_data.pop(job_id, None)
        await event.respond("💗 Jadwal berhasil dihapus!")
    except Exception as e:
        logging.error(f"💔 Error pada /deletejob: {e}")
        await event.respond("❌ Gagal menghapus. Pastikan ID yang dimasukkan benar.")

# Command untuk menghentikan semua job forward milik pengguna tertentu
@client.on(events.NewMessage(pattern='/stopforward'))
async def stop_forward(event):
    user_id = event.sender_id
    removed = []
    for job in scheduler.get_jobs():
        if str(user_id) in job.id:
            try:
                scheduler.remove_job(job.id)
                job_data.pop(job.id, None)
                removed.append(job.id)
            except Exception as e:
                logging.error(f"💔 Error menghapus job {job.id}: {e}")
    if removed:
        await event.respond(f"💗 Semua job forward untuk Anda telah dihapus: {', '.join(removed)}")
    else:
        await event.respond("❌ Tidak ditemukan job forward untuk Anda.")

@client.on(events.NewMessage(pattern='/blacklist_add'))
async def add_blacklist(event):
    try:
        nama = " ".join(event.message.raw_text.split()[1:])
        blacklisted_groups.add(nama)
        await event.respond(f"💗 '{nama}' berhasil masuk ke blacklist!")
    except Exception as e:
        logging.error(f"💔 Error pada /blacklist_add: {e}")
        await event.respond("❌ Format salah. Gunakan: /blacklist_add <nama grup>")

@client.on(events.NewMessage(pattern='/blacklist_remove'))
async def remove_blacklist(event):
    try:
        nama = " ".join(event.message.raw_text.split()[1:])
        blacklisted_groups.discard(nama)
        await event.respond(f"💗 '{nama}' telah dihapus dari blacklist!")
    except Exception as e:
        logging.error(f"💔 Error pada /blacklist_remove: {e}")
        await event.respond("❌ Format salah. Gunakan: /blacklist_remove <nama grup>")

@client.on(events.NewMessage(pattern='/list_blacklist'))
async def list_blacklist(event):
    if not blacklisted_groups:
        await event.respond("💗 Blacklist kosong!")
    else:
        teks = "💗== Grup dalam blacklist ==\n" + "\n".join(blacklisted_groups)
        await event.respond(teks)

@client.on(events.NewMessage(pattern='/status'))
async def cek_status(event):
    now = datetime.now()
    sisa = (MASA_AKTIF - now).days
    tanggal_akhir = MASA_AKTIF.strftime('%d %B %Y')
    await event.respond(
        f"💖 Masa aktif tersisa: {sisa} hari\n💖 Userbot aktif sampai: {tanggal_akhir}"
    )

@client.on(events.NewMessage(pattern='/review_pesan'))
async def review_pesan(event):
    pesan = pesan_simpan.get(event.sender_id)
    if not pesan:
        await event.respond("💗 Belum ada pesan yang disimpan.")
    else:
        await event.respond(f"💗== Isi Pesan Saat Ini ==\n{pesan}")

@client.on(events.NewMessage(pattern='/ubah_pesan'))
async def ubah_pesan(event):
    try:
        teks = event.message.raw_text.split(" ", maxsplit=1)[1]
        pesan_simpan[event.sender_id] = teks
        await event.respond("💗 Isi pesan berhasil diubah!")
    except Exception as e:
        logging.error(f"💔 Error pada /ubah_pesan: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/ubah_pesan <pesan_baru>")

@client.on(events.NewMessage(pattern='/simpan_preset'))
async def simpan_preset(event):
    try:
        user_id = event.sender_id
        parts = event.message.raw_text.split(" ", maxsplit=2)
        if len(parts) < 3:
            return await event.respond("❌ Format salah. Gunakan:\n/simpan_preset <nama> <pesan>")
        _, nama, pesan = parts
        preset_pesan.setdefault(user_id, {})[nama] = pesan
        await event.respond(f"💗 Preset '{nama}' berhasil disimpan!")
    except Exception as e:
        logging.error(f"💔 Error pada /simpan_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/simpan_preset <nama> <pesan>")

@client.on(events.NewMessage(pattern='/pakai_preset'))
async def pakai_preset(event):
    try:
        user_id = event.sender_id
        nama = event.message.raw_text.split(" ", maxsplit=1)[1]
        pesan = preset_pesan.get(user_id, {}).get(nama)
        if not pesan:
            return await event.respond(f"❌ Tidak ada preset dengan nama '{nama}'!")
        pesan_simpan[user_id] = pesan
        await event.respond(f"💗 Preset '{nama}' dipilih:\n\n{pesan}")
    except Exception as e:
        logging.error(f"💔 Error pada /pakai_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/pakai_preset <nama>")

@client.on(events.NewMessage(pattern='/list_preset'))
async def list_preset(event):
    user_id = event.sender_id
    daftar = preset_pesan.get(user_id, {})
    if not daftar:
        return await event.respond("💗 Belum ada preset.")
    teks = "💗== Daftar Preset ==\n" + "\n".join(f"- {nama}" for nama in daftar)
    await event.respond(teks)

@client.on(events.NewMessage(pattern=r'^/setdelaygroup (\d+)$', incoming=True))
async def set_delay_group(event):
    try:
        user_id = event.sender_id
        delay = int(event.pattern_match.group(1))
        delay_per_group_setting[user_id] = delay
        await event.reply(f"✨💗 Delay antar grup udah diset ke {delay} detik, bubb!")
    except Exception as e:
        await event.reply(f"❌ Ups, terjadi error saat menyetel delay: {e}")

@client.on(events.NewMessage(pattern=r'^/cekdelaygroup$', incoming=True))
async def cek_delay_group(event):
    user_id = event.sender_id
    delay = delay_per_group_setting.get(user_id, 0)
    await event.respond(
        f"⏱💗 Delay antar grup kamu saat ini: {delay} detik.\n"
        "Makin rapih makin mantep kirimannya!"
    )
    
@client.on(events.NewMessage(pattern=r'^/resetdelaygroup$', incoming=True))
async def reset_delay_group(event):
    user_id = event.sender_id
    delay_per_group_setting.pop(user_id, None)
    await event.reply("♻💗 Delay antar grup udah di-reset ke 0 detik, maniiss!\n"
                      "Siap ngebut kirim pesan ke semua grup!")

@client.on(events.NewMessage(pattern='/edit_preset'))
async def edit_preset(event):
    try:
        user_id = event.sender_id
        parts = event.message.raw_text.split(" ", maxsplit=2)
        if len(parts) < 3:
            return await event.respond("❌ Format salah. Gunakan:\n/edit_preset <nama> <pesan_baru>")
        _, nama, pesan_baru = parts
        if nama not in preset_pesan.get(user_id, {}):
            return await event.respond(f"❌ Tidak ada preset dengan nama '{nama}'!")
        preset_pesan[user_id][nama] = pesan_baru
        await event.respond(f"💗 Preset '{nama}' berhasil diubah!")
    except Exception as e:
        logging.error(f"💔 Error pada /edit_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/edit_preset <nama> <pesan_baru>")

@client.on(events.NewMessage(pattern='/hapus_preset'))
async def hapus_preset(event):
    try:
        user_id = event.sender_id
        nama = event.message.raw_text.split(" ", maxsplit=1)[1]
        if nama in preset_pesan.get(user_id, {}):
            del preset_pesan[user_id][nama]
            await event.respond(f"💗 Preset '{nama}' berhasil dihapus!")
        else:
            await event.respond(f"❌ Preset '{nama}' tidak ditemukan.")
    except Exception as e:
        logging.error(f"💔 Error pada /hapus_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/hapus_preset <nama>")

@client.on(events.NewMessage(pattern='/ping'))
async def ping(event):
    await event.respond("💖 Bot aktif dan siap melayani!")

# Command untuk restart bot
@client.on(events.NewMessage(pattern='/restart'))
async def restart(event):
    await event.respond("💗 Bot akan restart...")
    logging.info("💖 Restarting bot upon command...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@client.on(events.NewMessage(pattern='/log'))  
async def log(event):  
    try:  
        with open("bot.log", "r", encoding="utf-8") as log_file:  # Tentukan encoding UTF-8
            logs = log_file.read()  
            if len(logs) > 4000:  
                logs = logs[-4000:]  # Ambil 4000 karakter terakhir jika terlalu panjang  
            await event.respond(f"📜💗 Log Terbaru:\n{logs}")  
    except FileNotFoundError:  
        await event.respond("❌ Log tidak ditemukan.")  
    except UnicodeDecodeError:  
        await event.respond("❌ Ada masalah dalam membaca log (karakter tidak valid).")
    except Exception as e:  
        await event.respond(f"❌ Terjadi kesalahan: {e}")  # Menangani error lainnya

PENGEMBANG_USERNAME = "@explicist"
@client.on(events.NewMessage(pattern=r'/feedback(?:\s+(.*))?'))
async def feedback_handler(event):
    sender = await event.get_sender()
    name = sender.first_name or "Pengguna"
    username = f"@{sender.username}" if sender.username else "(tanpa username)"
    user_id = sender.id
    message = event.pattern_match.group(1)

    if not message:
        await event.reply(
            "💖 Hai! Kalau kamu punya saran, kritik, atau sekadar ucapan manis, kirim seperti ini ya:\n\n"
            "/feedback Botnya keren banget!"
        )
        return

    # Balasan ke pengirim
    await event.reply(
        "💌 Terima kasih atas feedback-nya!\n"
        "Masukanmu sangat berarti dan akan kami baca dengan penuh cinta!"
    )

    feedback_text = (
        "💌 Feedback Baru!\n\n"
        f"• Dari: {name}\n"
        f"• Username: {username}\n"
        f"• ID: {user_id}\n"
        f"• Pesan: {message}"
    )

    try:
        await client.send_message(PENGEMBANG_USERNAME, feedback_text)
    except Exception as e:
        await event.reply("Ups! Gagal mengirim feedback ke pengembang. Coba lagi nanti yaaw.")
        print(f"[Feedback Error] {e}")
      
@client.on(events.NewMessage(pattern=r'/reply (\d+)\s+([\s\S]+)', from_users=PENGEMBANG_USERNAME))
async def reply_to_user(event):
    match = event.pattern_match
    user_id = int(match.group(1))
    reply_message = match.group(2).strip()

    try:
        await client.send_message(user_id, f"💬 Pesan dari pengembang:\n\n{reply_message}")
        await event.reply("✅ Balasanmu sudah dikirim ke pengguna!")
    except Exception as e:
        await event.reply("❌ Gagal mengirim balasan ke pengguna. Mungkin user sudah block bot?")
        print(f"[Reply Error] {e}")

@client.on(events.NewMessage(pattern=r'^/help$', incoming=True))
async def help_cmd(event):
    teks = """
✨💖 PANDUAN USERBOT HEARTIE 💖✨

Hai, sayang! Aku Heartie, userbot-mu yang siap membantu menyebarkan pesan cinta ke semua grup-grup favoritmu. Berikut daftar perintah yang bisa kamu gunakan:

============================
1. /forward  
   Kirim pesan langsung ke grup.  
   - Mode forward (dari channel):  
     /forward forward @namachannel jumlah_grup id_pesan jeda detik durasi jam jumlah_pesan_perhari  
     Contoh: /forward forward @usnchannel 50 27 5 3 300  
   - Mode text (kirim teks langsung):  
     /forward text "Halo semua!" jumlah_grup jeda detik durasi jam jumlah_pesan_perhari  
     Contoh: /forward text "Halo semua!" 10 5 3 300  

============================
2. /scheduleforward  
   Jadwalkan pesan mingguan otomatis.  
   Format:  
   /scheduleforward mode pesan/sumber jumlah_grup durasi jeda jumlah_pesan hari1,day2 jam:menit  
   Contoh:  
   /scheduleforward forward @usnchannel 20 2 5 300 senin,jumat 08:00  
   /scheduleforward text "Halo dari bot!" 30 3 5 300 selasa,rabu 10:00  

============================
3. Manajemen Preset & Pesan  
   - /review_pesan — Lihat pesan default  
   - /ubah_pesan <pesan_baru> — Ubah pesan default  
   - /simpan_preset <nama> <pesan> — Simpan preset pesan  
   - /pakai_preset <nama> — Pilih preset sebagai pesan default  
   - /list_preset — Tampilkan daftar preset  
   - /edit_preset <nama> <pesan_baru> — Edit preset pesan  
   - /hapus_preset <nama> — Hapus preset  

============================
4. Pengaturan Job Forward & Delay  
   - /review — Tampilkan jadwal aktif  
   - /deletejob <id> — Hapus jadwal forward  
   - /setdelay <detik> — Atur jeda antar batch kirim  
   - /stopforward — Hentikan semua job forward aktif kamu
   - /setdelaygroup 5 - Set delay antar grup ke 5 detik (bisa diubah)
   - /cekdelaygroup - Cek delay antar grup kamu saat ini 
   - /resetdelaygroup - Reset delay antar grup ke default 

============================
5. Blacklist Grup
   - /blacklist_add <nama grup> — Tambahkan grup ke blacklist  
   - /blacklist_remove <nama grup> — Hapus grup dari blacklist  
   - /list_blacklist — Lihat daftar grup dalam blacklist  

============================
6. Info & Lain-lain  
   - /status — Cek masa aktif userbot  
   - /ping — Periksa apakah bot aktif  
   - /log — Tampilkan log aktivitas bot  
   - /feedback <pesan> — Kirim feedback ke pengembang  
   - /stats — Lihat statistik penggunaan forward  
   - /restart — Restart bot  

============================
✨ Cara mendapatkan ID pesan channel:  
Klik kanan bagian kosong (atau tap lama) pada pesan di channel → Salin link.  
Misal, jika linknya https://t.me/usnchannel/19 maka id pesan adalah 19.

Selamat mencoba dan semoga hari-harimu penuh cinta! 💗 Kalau masih ada yang bingung bisa chat pengembangku (zero) ya!
"""
    await event.respond(teks)

@client.on(events.NewMessage(pattern=r'^/info$', incoming=True))
async def info_handler(event):
    now = datetime.now()
    uptime = now - start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    aktif_sejak = start_time.strftime("%d %B %Y pukul %H:%M WIB")

    text = (
        "💖 Tentang Bot Ini 💖\n\n"
        "Hai! Aku adalah Heartie Bot — sahabatmu dalam meneruskan pesan otomatis!\n\n"
        "✨ Dibuat oleh: @explicist\n"
        "🛠 Versi: 1.2.0\n"
        "🧠 Ditenagai oleh: Python + Telethon\n"
        "🎯 Fungsi: Ngebantu kamu meneruskan pesan secara otomatis & terjadwal\n\n"
        f"⏳ Uptime: {hours} jam, {minutes} menit\n"
        f"📅 Aktif sejak: {aktif_sejak}\n\n"
        "Butuh bantuan? Coba ketik /help yaaw!"
    )

    await event.respond(text, parse_mode='markdown')

@client.on(events.NewMessage(pattern=r'^/stats$', incoming=True))
async def stats_handler(event):
    try:
        global TOTAL_SENT_MESSAGES

        sender = await event.get_sender()
        name = sender.first_name or "Pengguna"
        username = f"@{sender.username}" if sender.username else "(tanpa username)"

        stats_text = (
            f"💖 Hai {name} ({username})!\n\n"
            "📊 Statistik Bot:\n"
            f"• Total job aktif: {len(JOBS)}\n"
            f"• Total pesan terkirim: {TOTAL_SENT_MESSAGES}\n"
            f"• Waktu server: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await event.respond(stats_text)
        TOTAL_SENT_MESSAGES += 1

    except Exception as e:
        await event.respond(f"❌ Error: {e}")
      
# === PENGECEKAN LISENSI ===
async def cek_lisensi():
    if datetime.now() > MASA_AKTIF:
        logging.error("💔 Lisensi expired. Bot dihentikan.")
        sys.exit("💔 Lisensi expired.")

# === SETUP FLASK UNTUK KEEP ALIVE (misal untuk Railway / UptimeRobot) ===
app = Flask(__name__)

@app.route('/')
def home():
    return "💗 Heartie Bot is alive!"

@app.route('/ping')
def ping():
    return "✅ Xixi! Bot masih hidup."

def keep_alive():
    app.run(host="0.0.0.0", port=8000)

# Jalankan server Flask di thread terpisah
threading.Thread(target=keep_alive).start()

# === JALANKAN BOT ===
async def main():
    await client.start()
    scheduler.start()  # Scheduler berjalan di event loop
    await cek_lisensi()
    me = await client.get_me()
    welcome_msg = f"💖 Bot aktif, kamu masuk sebagai {me.first_name}. Menunggu perintahmu, sayang!"
    print(welcome_msg)
    logging.info(welcome_msg)
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())