#!/bin/sh
# Loads MySQL's named-timezone tables (mysql.time_zone_name and friends) from the
# container's own /usr/share/zoneinfo. The official mysql:8.0 image does NOT do
# this by default.
#
# Why this matters: settings.TIME_ZONE='Asia/Jakarta' with USE_TZ=True means every
# Django `__date` lookup on a DateTimeField (e.g. occurred_at__date, settled_at__date)
# compiles to `DATE(CONVERT_TZ(col, 'UTC', 'Asia/Jakarta'))`. Without the named-zone
# tables, CONVERT_TZ() silently returns NULL for every row -- no exception, no log
# line. The day-bucketed reporting rollups in apps/reporting/services.py
# (refresh_wallet_activity, refresh_daily_finance, refresh_daily_attendance,
# refresh_ar_aging) then group NULL into nothing and silently write zero rows.
#
# Runs automatically on first container init (docker-entrypoint-initdb.d convention).
# For an already-initialized volume, run manually instead:
#   mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -uroot -p"$MYSQL_ROOT_PASSWORD" mysql
set -e
mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" mysql
