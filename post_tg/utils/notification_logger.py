# utils/notification_logger.py
import logging
import logging.handlers
import os
from datetime import datetime
import pytz
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import json
import traceback

# Настройки
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))

class NotificationDBHandler(logging.Handler):
    """Кастомный обработчик логов для записи в таблицу уведомлений"""
    
    def __init__(self, db_url):
        super().__init__()
        self.db_url = db_url
        self.engine = create_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)
        
        # Маппинг уровней логирования на severity уведомлений
        self.level_mapping = {
            logging.DEBUG: 10,    # DEBUG
            logging.INFO: 20,     # INFO  
            logging.WARNING: 30,  # WARNING
            logging.ERROR: 40,    # ERROR
            logging.CRITICAL: 50, # CRITICAL
        }
        
        # Маппинг модулей на коды модулей уведомлений
        self.module_mapping = {
            'channel_sync': '001',
            'blondinka': '002', 
            'group_post': '003',
            'ads_post': '004',
            'pinner': '005',
            'view_booster': '006',
            'subscribers_booster': '007',
            'reaction_booster': '008',
            'system': '999'
        }

    def emit(self, record):
        """Обрабатывает запись лога и создает уведомление в БД"""
        try:
            # Пропускаем логи ниже WARNING (можно настроить)
            if record.levelno < logging.WARNING:
                return
                
            # Определяем модуль
            module_name = self._extract_module_name(record.name)
            module_code = self.module_mapping.get(module_name, '999')  # system по умолчанию
            
            # Определяем тип уведомления на основе уровня
            type_code = self._get_type_code(record.levelno)
            
            # Генерируем код уведомления
            notification_code = self._generate_notification_code(module_code, type_code, record.levelno)
            
            # Подготавливаем данные
            notification_data = {
                'notification_code': notification_code,
                'module_code': module_code,
                'type_code': type_code,
                'severity': self.level_mapping.get(record.levelno, 30),
                'title': self._generate_title(record),
                'message': self.format(record),
                'details': self._extract_details(record),
                'source': record.name,
                'stack_trace': getattr(record, 'exc_text', ''),
                'created_at': datetime.now(TZ)
            }
            
            # Сохраняем в БД
            self._save_to_db(notification_data)
            
        except Exception as e:
            # Fallback на обычный вывод если БД недоступна
            print(f"ERROR saving notification to DB: {e}")

    def _extract_module_name(self, logger_name):
        """Извлекает имя модуля из имени логгера"""
        if '.' in logger_name:
            return logger_name.split('.')[0]
        return logger_name

    def _get_type_code(self, level):
        """Определяет код типа уведомления на основе уровня"""
        type_mapping = {
            logging.WARNING: '001',  # Предупреждение
            logging.ERROR: '002',    # Ошибка
            logging.CRITICAL: '003', # Критическая ошибка
        }
        return type_mapping.get(level, '001')

    def _generate_notification_code(self, module_code, type_code, severity_level):
        """Генерирует код уведомления: MOD-TYP-SEV"""
        severity_code = str(severity_level // 10).zfill(2)
        return f"{module_code}-{type_code}-{severity_code}"

    def _generate_title(self, record):
        """Генерирует заголовок уведомления"""
        level_name = logging.getLevelName(record.levelno)
        module_name = self._extract_module_name(record.name)
        return f"[{module_name}] {level_name}: {record.getMessage().split('.')[0]}"

    def _extract_details(self, record):
        """Извлекает дополнительные детали из записи лога"""
        details = {
            'logger': record.name,
            'level': record.levelno,
            'level_name': logging.getLevelName(record.levelno),
            'file': record.pathname,
            'line': record.lineno,
            'function': record.funcName,
        }
        
        # Добавляем дополнительные поля если есть
        if hasattr(record, 'entity_id'):
            details['entity_id'] = record.entity_id
        if hasattr(record, 'task_id'):
            details['task_id'] = record.task_id
        if hasattr(record, 'external_id'):
            details['external_id'] = record.external_id
            
        return json.dumps(details)

    def _save_to_db(self, notification_data):
        """Сохраняет уведомление в БД через SQLAlchemy"""
        session = self.Session()
        try:
            # Проверяем, не существует ли уже такое уведомление (по коду)
            check_sql = text("""
                SELECT id FROM api_systemnotification 
                WHERE notification_code = :code 
                AND status IN ('NEW', 'ACKNOWLEDGED', 'IN_PROGRESS')
                LIMIT 1
            """)
            result = session.execute(check_sql, {'code': notification_data['notification_code']}).fetchone()
            
            if result:
                # Обновляем существующее уведомление
                update_sql = text("""
                    UPDATE api_systemnotification 
                    SET message = :message, details = :details, updated_at = :updated_at
                    WHERE notification_code = :code
                """)
                session.execute(update_sql, {
                    'message': notification_data['message'],
                    'details': notification_data['details'],
                    'updated_at': notification_data['created_at'],
                    'code': notification_data['notification_code']
                })
            else:
                # Создаем новое уведомление
                insert_sql = text("""
                    INSERT INTO api_systemnotification (
                        notification_code, module_id, notification_type_id, 
                        title, message, details, status, created_at, updated_at,
                        source, stack_trace
                    ) 
                    SELECT 
                        :notification_code,
                        m.id,
                        nt.id,
                        :title,
                        :message,
                        :details,
                        'NEW',
                        :created_at,
                        :created_at,
                        :source,
                        :stack_trace
                    FROM api_notificationmodule m
                    LEFT JOIN api_notificationtype nt ON (
                        nt.module_id = m.id AND 
                        nt.code = :type_code AND 
                        nt.severity = :severity
                    )
                    WHERE m.code = :module_code
                    LIMIT 1
                """)
                session.execute(insert_sql, {
                    'notification_code': notification_data['notification_code'],
                    'module_code': notification_data['module_code'],
                    'type_code': notification_data['type_code'],
                    'severity': notification_data['severity'],
                    'title': notification_data['title'],
                    'message': notification_data['message'],
                    'details': notification_data['details'],
                    'source': notification_data['source'],
                    'stack_trace': notification_data['stack_trace'],
                    'created_at': notification_data['created_at']
                })
            
            session.commit()
            
        except Exception as e:
            session.rollback()
            print(f"Database error in notification handler: {e}")
        finally:
            session.close()

def setup_notification_logging(db_url, level=logging.WARNING):
    """Настраивает систему логирования с уведомлениями"""
    
    # Создаем обработчик для БД
    db_handler = NotificationDBHandler(db_url)
    db_handler.setLevel(level)
    
    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    db_handler.setFormatter(formatter)
    
    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(db_handler)
    
    # Настраиваем специфичные логгеры для модулей
    modules = [
        'channel_sync', 'blondinka', 'group_post', 'ads_post', 
        'pinner', 'view_booster', 'subscribers_booster', 'reaction_booster'
    ]
    
    for module in modules:
        module_logger = logging.getLogger(module)
        module_logger.setLevel(logging.INFO)
        module_logger.addHandler(db_handler)
        # Предотвращаем дублирование сообщений
        module_logger.propagate = False
    
    return db_handler
