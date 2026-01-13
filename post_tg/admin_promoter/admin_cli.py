# admin_promoter/simple_cli.py

#!/usr/bin/env python3
"""
Простой CLI для управления AdminPromoter
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

COMMAND_FILE = Path("/app/data/admin_commands.json")

def print_help():
    print("AdminPromoter CLI - Управление одним ботом-промоутером")
    print("=" * 60)
    print("Использование:")
    print("  python simple_cli.py add <тип> <entity_id> <bot_id>")
    print("  python simple_cli.py list")
    print("  python simple_cli.py clear")
    print("\nТипы команд:")
    print("  promote - назначить бота администратором")
    print("  demote  - снять бота с админки")
    print("  leave   - вывести бота из сущности")
    print("\nПримеры:")
    print("  python simple_cli.py add promote 123 456")
    print("  python simple_cli.py list")
    print("  python simple_cli.py clear")

def add_command(cmd_type, entity_id, bot_id):
    """Добавляет команду"""
    # Создаем директорию если нет
    COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Загружаем существующие команды
    if COMMAND_FILE.exists():
        try:
            with open(COMMAND_FILE, 'r', encoding='utf-8') as f:
                commands = json.load(f)
        except:
            commands = []
    else:
        commands = []
    
    # Создаем новую команду
    command = {
        "id": len(commands) + 1,
        "type": cmd_type,
        "data": {
            "entity_id": int(entity_id),
            "bot_id": int(bot_id)
        },
        "created_at": datetime.now().isoformat(),
        "status": "pending"
    }
    
    commands.append(command)
    
    # Сохраняем
    with open(COMMAND_FILE, 'w', encoding='utf-8') as f:
        json.dump(commands, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Команда добавлена (ID: {command['id']})")
    print(f"   Тип: {cmd_type}")
    print(f"   Сущность ID: {entity_id}")
    print(f"   Бот ID: {bot_id}")

def list_commands():
    """Показывает список команд"""
    if not COMMAND_FILE.exists():
        print("📭 Файл команд не найден")
        return
    
    try:
        with open(COMMAND_FILE, 'r', encoding='utf-8') as f:
            commands = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения файла: {e}")
        return
    
    if not commands:
        print("📭 Нет команд")
        return
    
    print(f"📋 Всего команд: {len(commands)}")
    print("=" * 60)
    
    pending = [c for c in commands if c.get('status') == 'pending']
    completed = [c for c in commands if c.get('status') == 'completed']
    errors = [c for c in commands if 'error' in str(c.get('status', '')).lower()]
    
    print(f"⏳ Ожидают: {len(pending)}")
    print(f"✅ Выполнены: {len(completed)}")
    print(f"❌ Ошибки: {len(errors)}")
    print("-" * 60)
    
    for cmd in commands:
        status = cmd.get('status', 'pending')
        icon = '⏳' if status == 'pending' else '✅' if status == 'completed' else '❌'
        
        print(f"{icon} #{cmd['id']} [{cmd['type'].upper()}]")
        print(f"   Сущность: #{cmd['data']['entity_id']}")
        print(f"   Бот: #{cmd['data']['bot_id']}")
        print(f"   Статус: {status}")
        if 'result' in cmd:
            print(f"   Результат: {cmd['result']}")
        print()

def clear_commands():
    """Очищает выполненные команды"""
    if not COMMAND_FILE.exists():
        print("📭 Файл команд не найден")
        return
    
    try:
        with open(COMMAND_FILE, 'r', encoding='utf-8') as f:
            commands = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения файла: {e}")
        return
    
    # Оставляем только pending команды
    pending_commands = [cmd for cmd in commands if cmd.get('status') == 'pending']
    removed = len(commands) - len(pending_commands)
    
    # Сохраняем
    with open(COMMAND_FILE, 'w', encoding='utf-8') as f:
        json.dump(pending_commands, f, ensure_ascii=False, indent=2)
    
    if removed > 0:
        print(f"🗑️ Удалено {removed} выполненных команд")
    else:
        print("📭 Нет выполненных команд для удаления")
    print(f"📋 Осталось {len(pending_commands)} ожидающих команд")

def main():
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "add":
        if len(sys.argv) != 5:
            print("❌ Неверное количество аргументов для add")
            print("   Использование: python simple_cli.py add <тип> <entity_id> <bot_id>")
            sys.exit(1)
        
        cmd_type = sys.argv[2].lower()
        if cmd_type not in ['promote', 'demote', 'leave']:
            print("❌ Неверный тип команды. Допустимые: promote, demote, leave")
            sys.exit(1)
        
        try:
            entity_id = int(sys.argv[3])
            bot_id = int(sys.argv[4])
        except ValueError:
            print("❌ entity_id и bot_id должны быть числами")
            sys.exit(1)
        
        add_command(cmd_type, entity_id, bot_id)
    
    elif command == "list":
        list_commands()
    
    elif command == "clear":
        clear_commands()
    
    elif command in ["help", "--help", "-h"]:
        print_help()
    
    else:
        print(f"❌ Неизвестная команда: {command}")
        print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()