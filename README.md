# Iwed — Discord Music Bot

**Iwed** adalah Discord Music Bot modern berbasis **Python 3.12**, **discord.py** (native application commands), **Wavelink 3.5.2**, dan node audio **Lavalink 4.2.2** (DAVE-compatible voice stack).

---

## 1. Ikhtisar Arsitektur Iwed

Iwed dibangun dengan Clean Architecture berlapis (Domain, Ports, Application, Infrastructure, Presentation) yang memastikan integritas antrean terisolasi dari kegagalan jaringan atau node audio.

### Keputusan Teknis Utama
- **Nama Bot & Package:** Iwed (`iwed_bot`, class utama `IwedBot`).
- **Runtime:** Python 3.12 dikelola via `uv`.
- **Framework Bot:** `discord.py` v2.7.x menggunakan **native slash application commands** (`discord.app_commands`).
- **Discord Intents:** `message_content` intent dinonaktifkan. Bot hanya meminta intent standar yang diperlukan (`guilds`, `voice_states`).
- **Audio Client & Node:** Wavelink `3.5.2` terhubung ke container Lavalink `4.2.2-alpine` melalui Docker Compose.
- **Queue Source of Truth:** Domain queue session murni (`VersionedGuildSession`), bukan player internal cache Wavelink.
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
│   └── iwed_bot/
│       ├── __init__.py              # Package metadata
│       ├── __main__.py              # Application entrypoint & signal handling
│       ├── bot.py                   # IwedBot subclass & lifecycle supervisor
│       ├── settings.py              # Pydantic v2 settings & strict validation
│       ├── application/             # Application services & use cases
│       │   ├── concurrency.py       # Per-guild locking & runner registries
│       │   ├── errors.py            # Typed application errors
│       │   ├── play_service.py      # PlayRequestService
│       │   ├── playback_coordinator.py # PlaybackCoordinator (one-shot runner)
│       │   ├── queue_control.py     # QueueControlService (skip, pause, resume, queue)
│       │   ├── source_router.py     # URL classification & query sanitization
│       │   └── voice.py             # VoiceSessionService
│       ├── domain/                  # Pure immutable domain models & transitions
│       │   ├── errors.py            # Typed domain errors
│       │   ├── models.py            # VersionedGuildSession, QueueEntry, TrackReference
│       │   └── transitions.py       # Pure state transitions (track_end, skip, failure)
│       ├── ports/                   # Protocol interfaces (Gateway, Repository, Source)
│       ├── infrastructure/          # Adapters (Wavelink, Memory, YouTube Prototype)
│       │   ├── playback/            # WavelinkPlaybackGateway & metadata parser
│       │   ├── repositories/        # InMemoryQueueRepository
│       │   ├── sources/             # TrackSource adapters (prototype & compliance)
│       │   └── voice/               # WavelinkVoiceGateway
│       └── presentation/            # Discord Presentation layer
│           ├── command_tree.py      # IwedCommandTree & central error handler
│           ├── discord_notifier.py  # DiscordPlaybackNotifier
│           ├── formatting.py        # Progress bars & duration helpers
│           └── interactions.py      # Interaction responders & error translators
└── tests/
    ├── conftest.py                  # Pytest fixtures & mock objects
    ├── unit/                        # Unit testing (100% offline deterministic)
    └── integration/                 # Live container & YouTube integration tests
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
- `SOURCE_POLICY_MODE`: `prototype` untuk live testing YouTube, atau `compliance-first` untuk restricted mode.

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
uv run python -m iwed_bot
```

---

## 5. Menjalankan Quality Gates

### A. Pengujian Offline (Tanpa Memerlukan Lavalink Aktif)
Jalankan seluruh suite verifikasi kualitas kode:

```powershell
# 1. Dependency lock check
uv lock --check

# 2. Frozen sync verification
uv sync --frozen

# 3. Linting kode dengan Ruff
uv run ruff check .

# 4. Pengecekan formatting dengan Ruff
uv run ruff format --check .

# 5. Static Type Checking dengan Pyright
uv run pyright

# 6. Unit Testing (100% Offline, Deterministic Barrier Tests)
uv run pytest -v -m "not integration"
```

### B. Pengujian Integrasi Live (Memerlukan Lavalink Container Berjalan)
Setelah container Lavalink aktif:
```powershell
# 1. Validasi konfigurasi Docker Compose
docker compose config

# 2. Integration test Lavalink node & websocket
$env:RUN_LAVALINK_INTEGRATION="1"
uv run pytest -v tests/integration/test_lavalink_integration.py

# 3. Live YouTube Source Resolution test (Memerlukan akses jaringan)
$env:RUN_YOUTUBE_LIVE_INTEGRATION="1"
uv run pytest -v tests/integration/test_youtube_source_integration.py

# 4. Jalankan seluruh suite terpadu
uv run pytest -v
```

---

## 6. Slash Command yang Tersedia

| Command | Parameter | Deskripsi | Respons |
|---|---|---|---|
| `/health` | - | Memeriksa status kesehatan Iwed bot, latensi Discord, status node Lavalink, uptime, dan mode startup. | Discord Embed (Bahasa Indonesia) |
| `/join` | - | Menghubungkan Iwed ke voice channel pengguna atau memindahkan jika memiliki izin. | Ephemeral Message |
| `/stop` | - | Menghentikan pemutaran musik, mengosongkan antrean, dan memutuskan bot dari voice channel. | Ephemeral Message |
| `/play` | `query` (Wajib) | Mencari judul lagu di YouTube Music atau me-resolve URL video tunggal YouTube, lalu memasukkan ke antrean / memutar. | Discord Embed (Public) |
| `/skip` | `count` (Opsional, default: 1) | Melewati lagu yang sedang diputar atau beberapa lagu sekaligus (1-25) ke lagu berikutnya. | Discord Embed (Public) |
| `/pause` | - | Menjeda pemutaran musik fisik saat ini. | Discord Embed (Public) |
| `/resume` | - | Melanjutkan pemutaran musik yang sedang dijeda. | Discord Embed (Public) |
| `/queue` | `page` (Opsional, default: 1) | Menampilkan lagu yang sedang diputar dan daftar antrean berikutnya (10 lagu per halaman). | Discord Embed (Public) |
| `/nowplaying` | - | Menampilkan detail informasi dan progress bar lagu yang sedang diputar. | Discord Embed (Public) |

---

## 7. Prosedur Uji Manual Voice & Playback (Smoke Test)

Untuk memvalidasi integrasi real voice channel di server Discord:
1. Pastikan Lavalink berjalan (`docker compose up -d lavalink`) dan bot online (`uv run python -m iwed_bot`).
2. Masuk ke salah satu voice channel di Discord.
3. Jalankan `/join` -> Pastikan bot masuk ke voice channel.
4. Jalankan `/play query: lofi hip hop` -> Pastikan lagu mulai berbunyi dan embed muncul.
5. Jalankan `/pause` -> Lagu terjeda.
6. Jalankan `/resume` -> Lagu melanjutkan pemutaran.
7. Jalankan `/nowplaying` -> Embed menampilkan progres waktu dan status memutar.
8. Jalankan `/play query: https://www.youtube.com/watch?v=dQw4w9WgXcQ` -> Lagu kedua masuk ke antrean.
9. Jalankan `/queue` -> Menampilkan lagu saat ini dan lagu kedua di antrean.
10. Jalankan `/skip` -> Lagu pertama berhenti dan lagu kedua langsung diputar.
11. Jalankan `/stop` -> Pemutaran berhenti, antrean bersih, dan bot keluar dari voice channel.
