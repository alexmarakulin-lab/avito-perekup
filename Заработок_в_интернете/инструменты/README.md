# Инструменты для нарезок

Скрипты, которыми делаются ролики Grief Clips. Видео и исходники сюда не кладём (тяжёлые) — только код и спеки.

Зависимости: `ffmpeg`, `yt-dlp`, Python: `faster-whisper`, `Pillow`, `piper-tts` (для озвучки).
Шрифт: Montserrat ExtraBold (`Montserrat_wght_800.ttf`, Google Fonts) в папке `assfonts/` рядом со скриптом.

## Порядок работы
1. Скачать исходник с Rutube:
   `yt-dlp -f "default-3333-0/m3u8-3333-0/best[height<=1080]" -o src/<id>.mp4 https://rutube.ru/video/<id>/`
2. Звук и транскрипт (быстро, модель small):
   `ffmpeg -i src/<id>.mp4 -ac 1 -ar 16000 wav/<id>.wav`
   `python transcribe.py wav/<id>.wav <id>.json`
3. Точные слова в выбранных моментах (модель medium): нарезать куски `snip/s_<секунда>.wav`, затем
   `python snipt.py snip snip.json`
4. Описать ролики в спеке (см. `примеры_спеков/`): `name`, `video`, `hook` (плашка сверху), `parts` [[начало, конец], …], `chunks` [[время, «текст», конец?], …], `*слово*` — жёлтая подсветка.
5. Собрать: `python make_short.py spec.json` → `out/<name>.mp4` (1080×1920, размытый фон, полоса видео 16:9, субтитры, громкость −14 LUFS).
6. Если файл >30 МБ: `ffmpeg -i in.mp4 -c:v libx264 -preset medium -crf 24 -maxrate 3500k -bufsize 7000k -c:a copy out_s.mp4`

`po_short_voice.py` — пример вертикального ролика с озвучкой Piper (голос `ru_RU-dmitri-medium`), плашками и субтитрами.
