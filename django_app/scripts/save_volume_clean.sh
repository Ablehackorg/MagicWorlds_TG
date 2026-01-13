#!/usr/bin/env bash
set -euo pipefail

# === НАСТРОЙКИ ===
# 0 — реально удалять; 1 — только показывать, что бы удалили
DRY_RUN="${DRY_RUN:-1}"

# ЯВНО оставить эти тома (через пробел)
KEEP_NAMES="${KEEP_NAMES:-pgdata postgres_data db_data myproj_postgres_data}"

# Регэкспы, по которым считаем том "похожим на БД" и не трогаем
# (регистр игнорируется)
KEEP_REGEXES="${KEEP_REGEXES:-postgres|pg|pgdata|db}"

# === ЛОГИКА ===
mapfile -t VOLS < <(docker volume ls -q)
[ "${#VOLS[@]}" -eq 0 ] && { echo "Нет томов."; exit 0; }

keep_set=" $KEEP_NAMES "
del_list=()

for v in "${VOLS[@]}"; do
  # 1) Явный вайтлист по имени
  if [[ " $keep_set " == *" $v "* ]]; then
    echo "⏭  Пропуск (в списке KEEP_NAMES): $v"
    continue
  fi

  # 2) Режим «похоже на БД»
  shopt -s nocasematch
  if [[ "$v" =~ $KEEP_REGEXES ]]; then
    echo "⏭  Пропуск (совпало с KEEP_REGEXES): $v"
    shopt -u nocasematch
    continue
  fi
  shopt -u nocasematch

  # 3) Проверяем, используется ли том контейнером
  if docker ps -a --filter volume="$v" -q | grep -q .; then
    echo "⏭  Пропуск (том используется контейнером): $v"
    continue
  fi

  del_list+=("$v")
done

if [ "${#del_list[@]}" -eq 0 ]; then
  echo "✔  Нечего удалять — все тома либо используются, либо помечены как KEEP."
  exit 0
fi

echo
echo "Найдены неиспользуемые тома (к удалению):"
printf '  - %s\n' "${del_list[@]}"
echo

if [ "$DRY_RUN" != "0" ]; then
  echo "DRY-RUN: ничего не удаляю. Чтобы удалить — запустите: DRY_RUN=0 bash $0"
  exit 0
fi

# Реальное удаление
for v in "${del_list[@]}"; do
  echo "🗑  Удаляю том: $v"
  docker volume rm "$v" || echo "⚠️  Не удалось удалить: $v"
done

echo "Готово."

