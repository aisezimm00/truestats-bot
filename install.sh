#!/bin/bash
# Установка / обновление бота TrueStats ДДС (@kirpix_dds_bot) на Mac.
# Одна команда, повторный запуск = обновление кода и перезапуск:
#   curl -fsSL https://raw.githubusercontent.com/aisezimm00/truestats-bot/main/install.sh | bash
# Перед первым запуском положить файл truestats-bot.env (от Айсезим) в папку «Загрузки».
set -euo pipefail

GH_USER="aisezimm00"
REPO="truestats-bot"
BRANCH="main"
DIR="$HOME/truestats-bot"
LABEL="com.kirpix.truestats-bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mОШИБКА: %s\033[0m\n\n' "$*"; exit 1; }

[ "$(uname)" = "Darwin" ] || die "Этот скрипт для macOS."

# 1. uv (ставит нужный Python сам, Xcode и Homebrew не нужны)
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Ставлю uv (менеджер Python)"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv не установился. Напиши Айсезим."

# 2. Код
say "Скачиваю код бота в $DIR"
mkdir -p "$DIR/data"
TMP="$(mktemp -d)"
curl -fsSL "https://github.com/$GH_USER/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$TMP"
cp -R "$TMP/$REPO-$BRANCH/." "$DIR/"
rm -rf "$TMP"
cd "$DIR"

# 3. Настройки (.env): берём из Загрузок, если ещё не лежит в папке бота
if [ ! -f "$DIR/.env" ]; then
  for c in "$HOME/Downloads/truestats-bot.env" "$HOME/Desktop/truestats-bot.env" "$HOME/Downloads/truestats-bot.env.txt"; do
    if [ -f "$c" ]; then cp "$c" "$DIR/.env"; say "Взял настройки из $c"; break; fi
  done
fi
[ -f "$DIR/.env" ] || die "Нет файла настроек. Сохрани файл truestats-bot.env от Айсезим в папку «Загрузки» и запусти команду ещё раз."
if ! grep -Eq '^ANTHROPIC_API_KEY=sk-' "$DIR/.env"; then
  die "В файле настроек пустой ANTHROPIC_API_KEY. Без ключа Claude бот не сможет читать чеки. Попроси у Айсезим файл с ключом."
fi
if ! grep -Eq '^TELEGRAM_BOT_TOKEN=.{20,}' "$DIR/.env"; then
  die "В файле настроек нет TELEGRAM_BOT_TOKEN."
fi

# 4. Python и библиотеки
say "Ставлю Python 3.13 и библиотеки (1–3 минуты)"
uv venv --python 3.13 --quiet .venv
uv pip install --quiet --python .venv/bin/python -r requirements.txt
.venv/bin/python -c "import aiogram, anthropic, httpx, fitz, dotenv; import rules, fx, receipt_parser, truestats_client" \
  || die "Библиотеки не импортируются. Напиши Айсезим, пришли вывод этой команды."

# 5. Автозапуск через launchd (стартует при входе в систему, перезапускается при падении)
say "Регистрирую автозапуск"
mkdir -p "$HOME/Library/LaunchAgents"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/bot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$DIR/data/bot.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/bot.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

# 6. Mac не должен засыпать (спросит пароль от Mac; если не хочется, Ctrl+C и настроить в Системных настройках → Энергия)
say "Запрещаю Mac засыпать (введи пароль от Mac)"
sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1 2>/dev/null || echo "   пропущено, настрой вручную: Системные настройки → Энергия → не переводить в режим сна"

# 7. Проверка
say "Жду запуска бота…"
sleep 10
if launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | grep -q "state = running"; then
  say "ГОТОВО. Бот запущен и будет работать круглосуточно."
  echo "   Последние строки лога:"
  tail -n 5 "$DIR/data/bot.log" 2>/dev/null | sed 's/^/   /'
  echo
  echo "   Проверка: напиши в группе Д_Д_С_Kirpix команду /ping"
  echo "   Лог:       tail -f ~/truestats-bot/data/bot.log"
  echo "   Обновить:  та же команда установки ещё раз"
  echo "   Выключить: launchctl bootout gui/$UID_NUM/$LABEL"
else
  echo "Бот не поднялся. Лог:"
  tail -n 30 "$DIR/data/bot.log" 2>/dev/null
  die "Пришли Айсезим скрин этого окна."
fi
