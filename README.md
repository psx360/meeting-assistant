# Meeting Assistant для Radxa ROCK 2F

Аппаратно-программный диктофон собраний: стереозапись с двух INMP441, управление кнопкой и OLED-платой HW-787AB, настройка Wi‑Fi по BLE, отложенная отправка при отсутствии сети, транскрибация на VPS и рассылка документов через Telegram/VK.

Репозиторий содержит исходники обеих частей системы и установщики для восстановления на чистых машинах. Секретные ключи, записи, база подписчиков и результаты встреч намеренно не входят в Git.

## Архитектура

1. Radxa записывает PulseAudio-источник INMP441 через FFmpeg в стерео Opus 48 кГц/64 кбит/с и делит поток на части по 10 минут.
2. Во время записи OLED показывает QR-код страницы подписки, таймер и уровень микрофона. Для остановки кнопку нужно удерживать не менее 1,5 секунды; подсказка на экране не занимает место.
3. Запись автоматически останавливается через 6 часов. После остановки QR остаётся на экране ещё 5 минут или до короткого нажатия кнопки.
4. После остановки части объединяются без повторного кодирования и помещаются в устойчивую локальную очередь. После скрытия QR OLED показывает процент подготовки и загрузки.
5. Таймер повторяет отправку на VPS; после скрытия QR OLED показывает «ОТПРАВКА» с процентами, а без сети — «ОЖИДАЕТ СЕТЬ», пока `.ready` не получил `.uploaded`.
6. VPS режет длинную запись на части по 20 минут и транскрибирует их моделью `gpt-4o-transcribe` с фиксированным языком `ru`.
7. Боты отправляют только два DOCX-файла: транскрибацию и протокол со сводкой. Постоянные подписчики получают все собрания, а перешедшие по QR — только выбранное.
8. В VK любое непустое текстовое сообщение включает постоянную подписку; команды отписки нет.

## Проверенная платформа

- Radxa ROCK 2F, Debian 12 (bookworm), Radxa kernel 6.1.43;
- 40-контактный GPIO-разъём;
- два цифровых микрофона INMP441 на SAI0/I2S0-M1;
- OLED/encoder HW-787AB, SSD1306-совместимый экран 128×64, I2C-адрес `0x3c`;
- отдельный Linux VPS с Debian/Ubuntu, Nginx и публичным HTTPS-доменом.

Другой образ ОС, нумерация GPIO или имя аудиоисточника могут потребовать адаптации констант.

## Подключение

Перед изменением проводов полностью отключайте питание. Номера ниже — физические номера контактов 40-pin разъёма.

### Два INMP441, стерео I2S0

| Сигнал INMP441 | Физический пин Radxa | Функция |
|---|---:|---|
| VDD обоих микрофонов | 17 | 3.3 V |
| GND обоих | 20 | GND |
| SCK/BCLK обоих | 12 | I2S0_SCLK_M1 / GPIO1_B5 |
| WS/LRCK обоих | 35 | I2S0_LRCK_M1 / GPIO1_B6 |
| SD обоих | 38 | I2S0_SDI_M1 / GPIO1_B7 |
| L/R первого | GND | левый канал |
| L/R второго | 3.3 V | правый канал |

Оба микрофона разделяют SCK, WS и SD; различаются только состоянием L/R.

### Кнопка записи

| Назначение | Подключение |
|---|---|
| Сигнальный контакт | физический пин 26, GPIO4_C1 (`gpiochip4`, line 17) |
| Подтяжка | 11 кОм от сигнала к 3.3 V |
| Второй контакт кнопки | GND |

Короткое нажатие запускает запись или убирает послесессионный QR-код. Во время записи короткое нажатие игнорируется; удержание не менее 1,5 секунды останавливает запись.

### HW-787AB

| Контакт платы | Физический пин Radxa | Назначение в ПО |
|---|---:|---|
| 3V3 | 1 или 17 | питание |
| GND | любой GND | земля |
| OLED_SDA | 32 | I2C0_SDA_M0 |
| OLED_SCL | 36 | I2C0_SCL_M0 |
| ENCODER_A | 11 | GPIO4_B7, line 15 |
| ENCODER_B | 13 | GPIO4_C0, line 16 |
| ENCODER_PUSH | 15 | GPIO4_C6, line 22 |
| BACK | 29 | GPIO4_B5, line 13 |
| CONFIRM | 31 | GPIO1_B0, `gpiochip1`, line 8 |

Пины `OLED_SDA/SCL` не совпадают с I2S и могут работать одновременно. WS2812B из окончательной конфигурации исключён: без преобразователя уровня 3.3→5 V управление было ненадёжным.

## Восстановление Radxa

На чистом Debian 12:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/psx360/meeting-assistant.git
cd meeting-assistant
sudo ./install-radxa.sh
sudo nano /home/radxa/.config/meeting-upload.env
sudo reboot
```

В `meeting-upload.env` задайте публичный HTTPS URL VPS и общий `MEETING_API_TOKEN`. Установщик:

- ставит FFmpeg, SoX, PipeWire, BlueZ, NetworkManager, gpiod и зависимости Python;
- компилирует и подключает оба DT-overlay;
- устанавливает системные и пользовательские systemd-службы;
- включает watchdog Wi‑Fi и повторную отправку очереди;
- сохраняет резервную копию `extlinux.conf` как `extlinux.conf.meeting-assistant.bak`.

В меню OLED пункт «IP АДРЕС» показывает текущий IPv4-адрес Wi‑Fi интерфейса.

После перезагрузки проверьте:

```bash
arecord -l
pactl list short sources
i2cdetect -y 0
systemctl status recorder-controller oled-dashboard wifi-watchdog
systemctl --user status meeting-upload.timer
journalctl -f -u recorder-controller -u oled-dashboard
```

Ожидаемый источник: `alsa_input.platform-inmp441-sound.stereo-fallback`, OLED — `0x3c`.

Коэффициент программного усиления по умолчанию задаётся как `MIC_GAIN=4`. Значение можно изменить в BLE-форме; оно сохраняется в `/var/lib/meeting-recorder/settings.json` и применяется после перезагрузки и при каждом новом запуске записи.

## Восстановление VPS

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/psx360/meeting-assistant.git
cd meeting-assistant
sudo ./install-vps.sh
sudo nano /etc/meeting-assistant.env
sudo systemctl restart meeting-assistant meeting-worker
```

Заполните все поля из `vps.env.example`. `MEETING_API_TOKEN` должен побайтно совпадать со значением на Radxa. Затем замените `server_name _` в `/etc/nginx/sites-available/meeting-assistant` на домен и настройте HTTPS. Telegram и VK требуют публичный HTTPS callback URL.

Регистрация Telegram webhook после настройки `PUBLIC_BASE_URL`:

```bash
sudo python3 register-meeting-telegram-webhook.py
```

Для VK укажите callbacks:

- `https://ВАШ-ДОМЕН/vk/callback`;
- confirmation code, secret, group ID и community access token — в `/etc/meeting-assistant.env`.
- `VK_GROUP_SCREEN_NAME` можно задать коротким именем сообщества; иначе страница подписки использует `club<VK_GROUP_ID>`.

Проверка:

```bash
curl http://127.0.0.1:8090/health
systemctl status meeting-assistant meeting-worker nginx
journalctl -f -u meeting-assistant -u meeting-worker
```

Данные VPS находятся в `/var/lib/meeting-assistant`:

- `incoming/` — очередь;
- `completed/<meeting-id>/transcript-raw.txt` — исходное распознавание;
- `completed/<meeting-id>/transcript.txt` — исходная русская транскрипция без диаризации и пересказа;
- `completed/<meeting-id>/summary.txt` — структурированный протокол и сводка встречи.

Для подписчиков Telegram результат приходит в таком порядке:

1. DOCX с полной транскрибацией одним документом.
2. DOCX с протоколом и сводкой: тезисы, решения, организационные выводы, поручения и нерешённые вопросы — только если они были в разговоре.
3. Полный текст транскрибации, автоматически разбитый на несколько сообщений Telegram.
- `completed/<meeting-id>/protocol.txt` — отправленный протокол;
- `meeting-assistant.sqlite3` — подписчики и статусы рассылки.

Эти данные резервируются отдельно от Git.

## BLE-настройка

Нажмите энкодер на экране готовности и выберите режим BT. Устройство рекламируется как `Meeting Assistant`. Веб-форма находится в `bluefy-setup.html` и предназначена для открытия в iOS-браузере Bluefy. Она позволяет найти похожее имя Wi‑Fi, передать пароль или выбрать открытую сеть, изменить пороги речи/тишины и выполнить 10-секундный замер шума.

## Основные журналы

```bash
# Radxa: кнопка, запись, OLED, загрузка
journalctl -f -u recorder-controller -u oled-dashboard -u wifi-watchdog
journalctl --user -f -u audio-recorder -u meeting-upload

# VPS: приём, транскрибация и рассылка
journalctl -f -u meeting-assistant -u meeting-worker
```

## Безопасность и резервное копирование

Никогда не добавляйте в Git `.env`, `*.key`, базу SQLite и записи. Для полного аварийного восстановления отдельно сохраняйте:

- `/etc/meeting-assistant.env` на VPS;
- `/var/lib/meeting-assistant/meeting-assistant.sqlite3`;
- `/home/radxa/.config/meeting-upload.env`;
- при необходимости каталоги `completed/` и записи на Radxa.
