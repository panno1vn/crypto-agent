#!/bin/bash
# scripts/init-multi-db.sh
#
# Postgres image chính thức tự động chạy MỌI file .sh/.sql đặt trong
# /docker-entrypoint-initdb.d/ CHỈ MỘT LẦN — lúc data volume còn rỗng
# (lần đầu tiên container được tạo).
#
# Mục đích: mặc định POSTGRES_DB chỉ tạo ĐÚNG 1 database (vd: crypto_agent).
# Nhưng Airflow cần 1 database metadata RIÊNG (airflow_db) để tránh
# trộn lẫn schema của Airflow (dag_run, task_instance, ...) với
# schema business của mình (telegram_messages, ohlcv, ...).
#
# Script này đọc biến POSTGRES_MULTIPLE_DATABASES (danh sách phân tách
# bởi dấu phẩy) và tạo từng database nếu chưa tồn tại.

set -e
set -u

function create_database() {
	local database=$1
	echo "  → Creating database '$database' (owner: $POSTGRES_USER)"
	psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	    SELECT 'CREATE DATABASE $database'
	    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$database')\gexec
EOSQL
}

if [ -n "${POSTGRES_MULTIPLE_DATABASES:-}" ]; then
	echo "[init-multi-db] Multiple databases requested: $POSTGRES_MULTIPLE_DATABASES"
	for db in $(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' ' '); do
		create_database "$db"
	done
	echo "[init-multi-db] Done."
else
	echo "[init-multi-db] POSTGRES_MULTIPLE_DATABASES not set, skipping."
fi