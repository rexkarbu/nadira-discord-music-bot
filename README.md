# Nadira — Discord Music Bot (Fase 1: Foundation & Architecture)

**Nadira** adalah Discord Music Bot modern berbasis **Python 3.12**, **discord.py** (native application commands), **Wavelink 3.5.2**, dan node audio **Lavalink 4.2.2** (DAVE-compatible voice stack).

---

## 1. Ikhtisar Arsitektur Fase 1

Fase 1 membangun fondasi arsitektur, konfigurasi tersertifikasi, lifecycle bot, observabilitas terstruktur, serta integrasi node audio sebelum fitur playback diimplementasikan.

### Keputusan Teknis Utama
- **Nama Bot & Package:** Nadira (`nadira_bot`, class utama `NadiraBot`).
- **Runtime:** Python 3.12 dikelola via `uv`.
- **Framework Bot:** `discord.py` v2.7.x menggunakan **native slash application commands** (`discord.app_commands`).
- **Discord Intents:** `message_content` intent dinonaktifkan. Bot hanya meminta intent standar yang diperlukan (`guilds`, `voice_states`).
- **Audio Client & Node:** Wavelink `3.5.2` terhubung ke container Lavalink `4.2.2-alpine` melalui Docker Compose.
- **Startup Failure Policy:** Validasi ketat untuk kredensial Discord (fail fast). Koneksi Lavalink bersifat *degraded startup* (bot tetap online dan mencoba reconnect dengan supervisor backoff terukur jika Lavalink belum siap).
- **Quality Gates:** 100% lulus linting (`ruff`), type checking (`pyright`), testing offline (`pytest -m "not integration"`), dan validasi Docker Compose.

---

## 2. Struktur Direktori

```text
dc_music/
├── .env.example                     # Template konfigurasi environment
├── .gitignore                       # Proteksi file rahasia dan cache
├── Dockerfile                       # Multi-stage container build Python 3.12
├── docker-compose.yml               # Service Lavalink v4.2.2-alpine & Bot
├── pyproject.toml                   # Definisi paket dan quality gate tools
├── uv.lock                          # Dependency lockfile
├── README.md                        # Dokumentasi operasional & panduan
├── lavalink/
│   ├── application.yml.example      # Template konfigurasi Lavalink v4
│   └── application.yml              # Konfigurasi aktif (dibuat lokal, diabaikan Git)
├── src/
│   └── nadira_bot/
│       ├── __init__.py              # Package metadata
│       ├── __main__.py              # Application entrypoint & signal handling
│       ├── bot.py                   # NadiraBot subclass & lifecycle supervisor
│       ├── settings.py              # Pydantic v2 settings & strict validation
│       ├── commands/
│       │   ├── __init__.py
│       │   └── health.py            # Slash command /health
│       └── observability/
│           ├── __init__.py
│           └── logging.py           # Structured JSON logging
└── tests/
    ├── __init__.py
    ├── conftest.py                  # Pytest fixtures & mock objects
    ├── unit/
    │   ├── __init__.py
    │   ├── test_settings.py         # Pengujian validasi settings & secret masking
    │   ├── test_health.py           # Pengujian slash command /health
    │   └── test_lifecycle.py        # Pengujian intent, lifecycle, & supervisor
    └── integration/
        ├── __init__.py
        └── test_lavalink_integration.py # Pengujian koneksi real Lavalink container
```

---

## 3. Prasyarat Sistem

1. **Sistem Operasi:** Windows 10/11, macOS, atau Linux.
2. **Python:** Python 3.12 (disarankan diinstal via `uv`).
3. **Package Manager:** `uv` (versi >= 0.4.0).
4. **Docker:** Docker Desktop (aktifkan WSL 2 backend di Windows).

---

## 4. Panduan Instalasi & Setup Lokal (Windows)

### Langkah 1: Kloning & Persiapan Environment
Buka PowerShell di direktori proyek:
```powershell
# Buat virtual environment dengan Python 3.12
uv venv --python 3.12

# Sinkronkan dan instal semua dependensi (termasuk dev tools)
uv sync --all-groups
```

### Langkah 2: Konfigurasi File Lingkungan & Lavalink
Salin template konfigurasi lokal:
```powershell
Copy-Item .env.example .env
Copy-Item lavalink/application.yml.example lavalink/application.yml
```
Buka file `.env` dan ganti nilai-nilai kredensial berikut dengan data bot Anda:
- `DISCORD_TOKEN`: Token bot Anda dari Discord Developer Portal (Wajib).
- `DISCORD_APPLICATION_ID`: Client ID aplikasi bot Anda (Wajib).
- `DISCORD_TEST_GUILD_ID`: (Opsional) Server ID Discord untuk instant command sync saat development.
- `LAVALINK_URI`: Gunakan `http://localhost:2333` untuk development lokal di host.
- `LAVALINK_PASSWORD`: Password node Lavalink (default: `youshallnotpass`).

> **Catatan Fail-Fast:** Jika `.env` belum dibuat atau `DISCORD_TOKEN`/`DISCORD_APPLICATION_ID` dikosongkan, bot akan gagal startup dengan log level `CRITICAL` berformat JSON dan exit code `1`.

### Langkah 3: Menjalankan Lavalink v4
Jalankan node Lavalink menggunakan Docker Compose:
```powershell
docker compose up -d lavalink
```
Periksa apakah container Lavalink sudah berjalan dan berstatus healthy:
```powershell
docker compose ps
```

### Langkah 4: Menjalankan Bot
Jalankan bot menggunakan `uv`:
```powershell
uv run python -m nadira_bot
```

Saat bot berhasil login, Anda akan melihat log terstruktur dalam format JSON dan slash command `/health` siap digunakan.

---

## 5. Menjalankan Quality Gates

### A. Pengujian Offline (Tanpa Memerlukan Lavalink Aktif)
Jalankan seluruh suite verifikasi kualitas kode:

```powershell
# 1. Linting kode dengan Ruff
uv run ruff check .

# 2. Pengecekan formatting dengan Ruff
uv run ruff format --check .

# 3. Static Type Checking dengan Pyright
uv run pyright

# 4. Unit Testing (100% Offline)
uv run pytest -v -m "not integration"
```

### B. Pengujian Integrasi Live (Memerlukan Lavalink Container Berjalan)
Setelah container Lavalink aktif:
```powershell
# 1. Validasi konfigurasi Docker Compose
docker compose config

# 2. Jalankan integration test terhadap container Lavalink
$env:RUN_LAVALINK_INTEGRATION="1"
uv run pytest -v -m integration
```

---

## 6. Slash Command yang Tersedia di Fase 1

| Command | Deskripsi | Respons |
|---|---|---|
| `/health` | Memeriksa status kesehatan Nadira bot, latensi Discord, status node Lavalink, uptime, dan mode startup. | Discord Embed (Bahasa Indonesia) |

> **Catatan:** Fitur playback musik (`/play`, `/skip`, `/queue`, integrasi Spotify/YouTube) belum ada pada fase ini dan akan diimplementasikan pada Fase 2 hingga Fase 5.
