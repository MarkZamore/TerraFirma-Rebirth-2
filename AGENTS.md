# Подготовка исходников сборки

## Публичные серверы

- `servers.dat` автора хранится только в исходном stock snapshot. Не включать его или seed server list в runtime, не менять личные записи игрока или Steam-discovery.
- После авторского импорта подготовка считается завершённой только после `python -B tools/public_server_policy.py prepare --pack .`. Команда read-only, не пересчитывает pin и останавливается при отсутствии receipt или изменении любого проверяемого каталога поставки, включая `data`, `resourcepacks`, `shaderpacks`, `global_packs` и `ambience_music`. Удаление модуля проверки останавливает импорт генератора, а удаление receipt останавливает prepare/verify и CI. Перед переносом правила новой версии вручную проверить новые каталоги серверов, seeding и все действия меню, затем явно утвердить новый pin.
- Генератор и verify-all вызывают `public_server_policy.verify`. При импорте автора сохранять `tools/public_server_policy.py` и независимый pin `tools/public-server-policy.json`. Изменение проверенных mods/config/scripts требует адресного повторного аудита автоматических рекомендаций, действий меню и прямого подключения перед явным обновлением pin. Автоматически пересчитывать pin при импорте или публикации нельзя.

## Метки игроков хоста и миграция конфигурации LSS

- При импорте авторского обновления сохранять `tools/host_compatibility_policy.py`, `tools/host-compatibility-policy.json`, шаблоны `lanmc_journeymap_player_markers.js` и `lanmc_lss_config_migration.js` под `tools/client-policy/scripts/`, их runtime-копии под `kubejs/startup_scripts/` и соответствующие точные строки ownership.
- В независимой исходной копии выполнить `python -B tools/host_compatibility_policy.py prepare --pack . --apply`, затем `verify --pack .`. Генератор и CI вызывают только read-only verify. Не принимать новые версии JourneyMap, LSS, KubeJS, Rhino или платформы без повторного разбора API, проверки миграции и игрового прогона. Новые pins в receipt утверждаются явно после проверки.
- Метки изменяются только в памяти клиента встроенного сервера через публичный JourneyMap API. Сохранённые точки, изображения карты, дальние записи и правила видимости остаются под управлением JourneyMap.
- Миграция LSS переносит совместимые старые параметры VSS в штатные поля LSS. Сохранять однократный receipt экземпляра и последующий личный выбор. Не вводить собственные пределы дальности или качества и не переносить настройки через C# лаунчера.

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


## Отрисовка рук Intel без шейдеров

- При авторском импорте сохранять `tools/hand_depth_policy.py`, `tools/hand-depth-policy.json`, `tools/hand-depth/`, `tools/client-policy/scripts/lanmc_intel_hand_depth.js` и точную runtime-копию `kubejs/startup_scripts/lanmc_intel_hand_depth.js`. Не заменять репозиторий целиком авторским экспортом.
- После импорта в независимой исходной копии выполнить `python -B tools/hand_depth_policy.py prepare --pack . --apply`, затем `python -B tools/hand_depth_policy.py verify --pack .`. Подготовка восстанавливает только проверенный слой и его точное ownership; остальные авторские и личные файлы не подменять.
- Minecraft, NeoForge, Sable и вложенный Veil, KubeJS, Rhino привязаны к точным pins. При изменении платформы или любого из этих JAR повторить native API и игровой прогон на затронутом GPU: пустая рука, обе руки с предметами, выключенные/включённые шейдеры и повторный вход. Только после проверки явно актуализировать профиль и pins; неизвестную версию не принимать автоматически.
- Генератор, verify-only и публикация вызывают read-only verify до упаковки. Не вызывать prepare из работающего лаунчера и не отключать guard. Отсутствующий слой или несовпадающие pins должны остановить публикацию.
- Ownership только точного startup-скрипта, без `kubejs/**`. Авторские JAR, scripts/resources, механики, stencil и свет не отключать. Скрипт повторяет только штатную очистку depth связанного first-person framebuffer на Intel, один раз перед первой рукой кадра; обработчики и рендер обеих рук остаются штатными.
