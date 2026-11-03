# Подготовка исходников сборки

## Сохранение диагностики при обновлениях автора

- После переноса новых авторских файлов сохранять pack-owned слой: `config/log4j2.xml`, `launcher/jvm-args.txt`, `launcher/pack-owned.txt`, `tools/network_logging_policy.py`, `tools/network-logging-policy.json`. Не заменять весь репозиторий одним авторским экспортом.
- В независимой исходной копии после импорта выполнить `python -B tools/network_logging_policy.py prepare --pack .`, затем `verify --pack .`; актуализировать затронутые support-lock записи по точным байтам. Это реальный ручной процесс подготовки, не автоматический апдейтер автора.
- Публикация и verify-only обязаны вызывать read-only `network_logging_policy.verify(pack)` до сбора/проверки assets. Не вызывать prepare из генератора, CI-публикации или работающего лаунчера; не отключать этот guard ради новой версии.
- Ownership только `config/log4j2.xml`, без нового `config/**`. Другие личные конфиги сохранять; отличающуюся старую копию этого файла обновление лаунчера сохраняет в PackConflicts.
- На новой версии Minecraft/загрузчика повторно проверить настоящий caller, logger и четыре Throwable-сообщения; конфиг не исправляет причину разрыва связи и не доказывает игровой запуск.

## Изоляция меток JourneyMap

- При авторском импорте сохранить source-owned слой 	ools/waypoint_source_policy.py, 	ools/journeymap-waypoints-policy.json, 	ools/client-policy/scripts/lanmc_journeymap_waypoint_policy.js и его точную runtime-копию под kubejs/startup_scripts/. После импорта выполнить prepare --pack . --apply, затем erify --pack .; generator и verify-only остаются read-only.
- Для новой версии JourneyMap/KubeJS/Rhino повторно проверить API и настоящие KubeJS type wrappers, обновить точные pins и native proof. Не расширять ClassFilter и не обходить gate.
- В launcher/pack-owned.txt принадлежит только точный путь этого скрипта, без kubejs/**. Авторские файлы не меняются; support-lock содержит единственную новую runtime-копию. Личные карты и миры в поставку не входят, метки не очищаются по эвристике даты.
